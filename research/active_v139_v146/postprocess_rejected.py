from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
from pathlib import Path

import numpy as np
import pandas as pd


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_equity(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, index_col=0, parse_dates=True)
    frame.index = pd.to_datetime(frame.index, utc=True)
    if 'equity' not in frame:
        raise ValueError(f'missing equity: {path}')
    for column, value in (
        ('gross', 0.0), ('turnover', 0.0), ('min_margin_buffer', 1.0),
        ('liquidated_notional', 0.0), ('trading_costs', 0.0),
        ('roll_costs', 0.0), ('liquidation_penalty', 0.0),
    ):
        if column not in frame:
            frame[column] = value
    return frame.sort_index()


def elapsed_years(index: pd.DatetimeIndex) -> float:
    if len(index) < 2:
        return 1 / 365.25
    return max((index[-1] - index[0]).total_seconds() / (365.25 * 86400), 1 / 365.25)


def metrics(account: pd.DataFrame, start=None, end=None) -> dict:
    x = account
    if start is not None:
        x = x[(x.index >= pd.Timestamp(start, tz='UTC')) & (x.index < pd.Timestamp(end, tz='UTC'))]
    if x.empty:
        return {k: 0.0 for k in ('total_return', 'annualized_return', 'max_drawdown', 'sharpe', 'annual_turnover')}
    equity = x.equity.astype(float)
    equity = equity * (10000.0 / equity.iloc[0])
    returns = equity.pct_change().fillna(equity.iloc[0] / 10000.0 - 1.0)
    years = elapsed_years(equity.index)
    observations_per_year = len(equity) / years
    std = returns.std(ddof=1)
    drawdown = equity / equity.cummax() - 1.0
    return {
        'total_return': float(equity.iloc[-1] / 10000.0 - 1.0),
        'annualized_return': float((equity.iloc[-1] / 10000.0) ** (1 / years) - 1.0),
        'max_drawdown': float(drawdown.min()),
        'sharpe': float(returns.mean() / std * math.sqrt(observations_per_year)) if std > 0 else 0.0,
        'annual_turnover': float(x.turnover.sum() / years),
        'average_gross': float(x.gross.mean()),
        'max_gross': float(x.gross.max()),
        'costs': float(x[['trading_costs', 'roll_costs', 'liquidation_penalty']].sum().sum()),
        'liquidations': int((x.liquidated_notional > 0).sum()),
        'min_margin_buffer': float(x.min_margin_buffer.min()),
        'observations_per_year': float(observations_per_year),
    }


def yearly(account: pd.DataFrame, label: str) -> pd.DataFrame:
    returns = account.equity.pct_change().fillna(account.equity.iloc[0] / 10000.0 - 1.0)
    rows = [{'year': int(year), label: float((1 + group).prod() - 1)} for year, group in returns.groupby(returns.index.year)]
    return pd.DataFrame(rows)


def bootstrap(account: pd.DataFrame, output: Path, seed: int = 20260726) -> pd.DataFrame:
    returns = account.equity.pct_change().dropna().to_numpy(float)
    rng = np.random.default_rng(seed)
    rows = []
    for block in (10, 21, 63):
        for horizon in (252, 504):
            terminal, drawdowns = [], []
            for _ in range(5000):
                sequence = []
                while len(sequence) < horizon:
                    start = int(rng.integers(0, max(1, len(returns) - block)))
                    sequence.extend(returns[start:start + block])
                sample = np.asarray(sequence[:horizon])
                equity = np.cumprod(1 + sample)
                terminal.append(equity[-1] - 1)
                drawdowns.append(float(np.min(equity / np.maximum.accumulate(equity) - 1)))
            terminal = np.asarray(terminal)
            drawdowns = np.asarray(drawdowns)
            rows.append({
                'block_days': block,
                'horizon_days': horizon,
                'median_return': float(np.median(terminal)),
                'p05_return': float(np.quantile(terminal, .05)),
                'prob_positive': float((terminal > 0).mean()),
                'median_max_drawdown': float(np.median(drawdowns)),
                'p05_max_drawdown': float(np.quantile(drawdowns, .05)),
            })
    frame = pd.DataFrame(rows)
    frame.to_csv(output / 'block_bootstrap.csv', index=False)
    return frame


def row(metrics_frame: pd.DataFrame, scenario: str, period: str) -> dict:
    match = metrics_frame[(metrics_frame.scenario == scenario) & (metrics_frame.period == period)]
    if match.empty:
        return {}
    return {key: (int(value) if key == 'liquidations' else float(value)) for key, value in match.iloc[0].items() if key not in ('scenario', 'period')}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--grid', type=Path, required=True)
    parser.add_argument('--atlas', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--raw-cache', type=Path)
    parser.add_argument('--artifact-id', type=int, required=True)
    parser.add_argument('--artifact-sha256', required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    proof = json.loads((args.grid / 'selection_proof_before_2021.json').read_text())
    if proof.get('selected_all_eligible') is not False:
        raise SystemExit('This deterministic post-process is only valid for the rejected standalone result')
    ranking = pd.read_csv(args.grid / 'selection_ranking_before_2021.csv')
    if int(ranking.eligible_before_2021.sum()) != 0:
        raise SystemExit('eligible policy count changed')
    sleeve_metrics = pd.read_csv(args.grid / 'selected_sleeve_metrics.csv')
    atlas = load_equity(args.atlas)
    sleeve = load_equity(args.grid / 'v142_selected_futures_equity.csv')

    for name in (
        'selection_proof_before_2021.json', 'selection_ranking_before_2021.csv',
        'selected_sleeve_metrics.csv', 'v142_selected_futures_equity.csv',
        'data_quality.csv', 'detected_roll_events.csv',
        'integration_weight_selection_2021_2023.csv', 'run.log',
    ):
        source = args.grid / name
        if source.exists():
            shutil.copy2(source, args.output / name)
    atlas.to_csv(args.output / 'V75_original_equity.csv')

    annual = yearly(atlas, 'V75_original').merge(yearly(sleeve, 'V142_futures_proxy'), on='year', how='outer').sort_values('year')
    annual.to_csv(args.output / 'ANNUAL_RETURNS.csv', index=False)
    bootstrap(sleeve, args.output)

    futures_annual = yearly(sleeve, 'return')
    positive_logs = np.log1p(futures_annual['return'].clip(lower=-.999999))
    positive_logs = positive_logs[positive_logs > 0]
    best_share = float(positive_logs.max() / positive_logs.sum()) if len(positive_logs) else 0.0

    summary = {
        'candidate': 'ACTIVE_V139_V146_GLOBAL_FUTURES_PROXY',
        'status': 'rejected_or_needs_iteration',
        'reason': '0 of 192 predeclared policies passed all standalone gates before 2021; integration with V75 was therefore prohibited.',
        'promoted_candidates': [],
        'policy_count': int(len(ranking)),
        'eligible_policy_count': 0,
        'selected_diagnostic_processes': proof.get('selected', []),
        'selection_cutoff': proof.get('selection_cutoff'),
        'selection_uses_2021_or_later': False,
        'standalone_selection_passed': False,
        'integration_permitted': False,
        'live_ready': False,
        'real_leverage_authorized': False,
        'data_source': 'Yahoo continuous front-month futures proxies',
        'execution_grade': False,
        'roll_model': 'calendar-window large-gap neutralization plus explicit roll cost',
        'artifact_provenance': {
            'repository': 'balkhaev/fin',
            'artifact_id': args.artifact_id,
            'artifact_sha256': args.artifact_sha256,
            'selection_proof_sha256': hashlib.sha256((args.grid / 'selection_proof_before_2021.json').read_bytes()).hexdigest(),
            'ranking_sha256': hashlib.sha256((args.grid / 'selection_ranking_before_2021.csv').read_bytes()).hexdigest(),
        },
        'original_v75_full': metrics(atlas),
        'diagnostic_sleeve_prefinal_2010_2020': metrics(sleeve, '2010-01-01', '2021-01-01'),
        'diagnostic_sleeve_bridge_2021_2023': metrics(sleeve, '2021-01-01', '2024-01-01'),
        'diagnostic_sleeve_holdout_2024_2025': metrics(sleeve, '2024-01-01', '2026-01-01'),
        'diagnostic_sleeve_final_2026h1': metrics(sleeve, '2026-01-01', '2026-07-01'),
        'stress_sleeve': {
            'prefinal_2010_2020': row(sleeve_metrics, 'stress', 'full_2010_2026h1') if False else {
                'development_2010_2015': row(sleeve_metrics, 'stress', 'development_2010_2015'),
                'validation_2016_2018': row(sleeve_metrics, 'stress', 'validation_2016_2018'),
                'validation_2019_2020': row(sleeve_metrics, 'stress', 'validation_2019_2020'),
            },
            'bridge_2021_2023': row(sleeve_metrics, 'stress', 'bridge_2021_2023'),
            'holdout_2024_2025': row(sleeve_metrics, 'stress', 'holdout_2024_2025'),
            'final_2026h1': row(sleeve_metrics, 'stress', 'final_2026h1'),
            'full_2010_2026h1': row(sleeve_metrics, 'stress', 'full_2010_2026h1'),
        },
        'extreme_sleeve': {
            'final_2026h1': row(sleeve_metrics, 'extreme', 'final_2026h1'),
            'full_2010_2026h1': row(sleeve_metrics, 'extreme', 'full_2010_2026h1'),
        },
        'best_year_positive_log_share': best_share,
        'evidence_limits': {
            'program_level_holdout_pristine': False,
            'actual_contract_chain_and_rolls': False,
            'broker_bid_ask_and_margin': False,
            'Yahoo_continuous_series_is_research_proxy': True,
        },
    }
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    decision = {
        'checkpoint': 'V146',
        'date': '2026-07-26',
        'decision': 'rejected_or_needs_iteration',
        'promoted_candidates': [],
        'live_ready': False,
        'real_leverage_authorized': False,
        'next_action': 'Do not tune these proxy policies further. Acquire dated contract chains and design a new predeclared execution-grade futures study.',
    }
    (args.output / 'FROZEN_DECISION.json').write_text(json.dumps(decision, indent=2) + '\n')

    raw_files = []
    if args.raw_cache and args.raw_cache.exists():
        for path in sorted(args.raw_cache.glob('*.json')):
            raw_files.append({'path': path.name, 'bytes': path.stat().st_size, 'sha256': sha256(path)})
    provenance = {
        'artifact': summary['artifact_provenance'],
        'atlas_input': {'path': str(args.atlas), 'bytes': args.atlas.stat().st_size, 'sha256': sha256(args.atlas)},
        'raw_yahoo_files': raw_files,
        'data_quality': pd.read_csv(args.grid / 'data_quality.csv').to_dict(orient='records'),
    }
    (args.output / 'DATA_PROVENANCE.json').write_text(json.dumps(provenance, indent=2) + '\n')

    report = [
        '# Active V139–V146: Global Futures Proxy', '',
        '## Решение', '',
        '**Отклонить текущий proxy-sleeve и запретить его интеграцию с V75.**', '',
        f"Из {len(ranking)} заранее заданных политик все gates прошли: **0**.",
        'Лучший диагностический процесс не достиг минимальных 5% CAGR / Sharpe 0,60 и имел отрицательную validation 2016–2018.', '',
        'Ни V143 static, ни V144 dynamic, ни V145 low-correlation leverage не создавались: слабый standalone sleeve не может скрываться внутри сильной V75-кривой.', '',
        '## Годовая доходность', '', annual.to_markdown(index=False, floatfmt='.4f'), '',
        '## Следующий шаг', '',
        'Не перебирать соседние пороги на Yahoo continuous series. Следующий допустимый цикл должен использовать датированные контракты, реальные даты экспирации/roll, bid/ask, multipliers, комиссии и broker margin schedule.', '',
        '`live_ready = false`; `real_leverage_authorized = false`.',
    ]
    (args.output / 'REPORT_RU.md').write_text('\n'.join(report) + '\n')
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

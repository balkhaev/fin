"""Publish already computed research evidence; no financial simulation or network.

All numbers derive from saved native accounts and their independent reproduction.
The archived defective reference is deliberately not copied into main.
"""
from pathlib import Path
import argparse
import csv
import hashlib
import json
import re
import shutil

STAGES = {
    'holding': ('holding_horizon', 68, 'fd786db742ddce242151cae621c38d430a74d399f84c19925978cf99b0d4d35b', 'all68_reports_equal'),
    'channels': ('channel_scale', 53, 'b4235bdfda95b01bd8ccaad1914adc1e5c7095d3535fb137de72f5ac08eef751', 'all53_reports_equal'),
    'budget': ('channel_budget', 43, None, 'all43_reports_equal'),
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def publish(package: Path, repo: Path):
    package, repo = Path(package), Path(repo)
    summary = {'id': 'holding-channel-release-20260906', 'stages': {}, 'total_reports': 164,
        'real_orders': 0, 'live_ready': False, 'stable500proven': False,
        'new_beta_policy_implemented': False, 'reference_engine_copied_to_main': False,
        'first_publication_attempt_failed_after_successful_reproduction': True,
        'local_execution': False, 'independent_implementation': False, 'unseen_market_holdout': False}
    lines = ['# Исследование удержания, каналов и распределения капитала', '',
        'Периоды: полная история — 01.01.2021–31.08.2026 (2 069 дней); поздняя — отдельный счёт 01.01.2025–31.08.2026 (608 дней). Начальные 10 000 USDT. Доходность за период и CAGR не взаимозаменяемы.', '',
        'Все стратегии пересчитаны с комиссиями, модельным неблагоприятным проскальзыванием и funding. Просадка — по часовым mark-закрытиям, не гарантированный предел убытка. Это исследование, не реальные сделки.', '',
        '## Три последовательных этапа', '',
        'Первым до результатов зафиксирован опыт сроков удержания. После его неудачи отдельно зафиксирована смена масштаба канальных сигналов. После результатов каналов зафиксированы меньшие размеры и две комбинации. Второй и третий этапы информированы предшествующими результатами; экономическая история во всех этапах уже исследовалась. Неудачные основные варианты не переименовываются в победителей.', '']
    compact = []
    source_files = set()
    for label, (folder, count, wanted, flag) in STAGES.items():
        base = package / label / 'report'
        r = json.loads((base / 'results.json').read_text())
        verification = json.loads((base / 'verification.json').read_text())
        proof = json.loads((package / 'proofs' / label / 'REPRODUCTION.json').read_text())
        actual = digest(r)
        if actual != verification['result_sha256'] or actual != proof['result_sha256'] or (wanted and actual != wanted):
            raise ValueError('Result identity mismatch: ' + label)
        if len(r['rows']) != count or proof[flag] is not True or r['ledger_sha256'] != proof['ledger_sha256']:
            raise ValueError('Independent reproduction incomplete: ' + label)
        if proof['exact_original_reports'] != 3:
            raise ValueError('Original baseline reproduction incomplete')
        if label == 'holding':
            for name, sha in r['source_sha256'].items():
                file = repo / 'research/holding_horizon' / name
                if hashlib.sha256(file.read_bytes()).hexdigest() != sha:
                    raise ValueError('Measured holding source changed')
                source_files.add(str(file.relative_to(repo)))
        else:
            file = repo / f'research/{folder}/study.py'
            if hashlib.sha256(file.read_bytes()).hexdigest() != r['script_sha256']:
                raise ValueError('Measured source changed: ' + label)
            source_files.add(str(file.relative_to(repo)))
        audit = json.loads((package / 'audits' / (label + '.json')).read_text())
        if audit['reports'] != count or audit['result_sha256'] != actual or not audit['all_saved_events_checked']:
            raise ValueError('Event audit mismatch')
        target = repo / 'research' / folder / 'results'; target.mkdir(parents=True, exist_ok=True)
        for name in ('results.json', 'comparison.csv', 'annual.csv', 'verification.json'):
            shutil.copyfile(base / name, target / name)
        shutil.copyfile(package / 'proofs' / label / 'REPRODUCTION.json', target / 'REPRODUCTION.json')
        shutil.copyfile(package / 'audits' / (label + '.json'), target / 'EVENT_AUDIT.json')
        order = list(dict.fromkeys(x['model'] for x in r['rows']))
        order.sort(key=lambda name: (name != r['control'], name))
        details = []
        for name in order:
            byperiod = {row['period']: row for row in r['rows'] if row['model'] == name and row['period'] != 'origin365'}
            entry = {'model': name}
            for period in ('full', 'later', 'validation', 'later_double_costs', 'later_delay2'):
                case = byperiod[period]
                for field in ('return_pct', 'cagr_pct', 'max_mark_close_drawdown_pct', 'completed_episodes'):
                    entry[period + '_' + field] = case[field]
            entry['full_max_mark_gross'] = byperiod['full']['leverage_audit'].get('max_mark_close_gross')
            entry['all_reported_scenarios_qualified'] = all(x['qualification']['qualified_historical_scenario'] for x in byperiod.values())
            details.append(entry); compact.append(dict(stage=label, **entry))
        gates = r.get('admission', r.get('gates'))
        summary['stages'][label] = dict(result_sha256=actual, ledger_sha256=r['ledger_sha256'], reports=count,
            qualified=sum(x['qualification']['qualified_historical_scenario'] for x in r['rows']),
            primary=r['primary'], control=r['control'], primary_admitted=r['admitted'], gates=gates,
            origin_sensitivity=r['origin_sensitivity'], models=details,
            event_audit={k: v for k, v in audit.items() if k != 'rows'})
        title = {'holding': '1. Максимальный срок удержания', 'channels': '2. Масштаб канального сигнала', 'budget': '3. Меньший бюджет и общий портфель'}[label]
        lines += ['## ' + title, '', f"Основной вариант: `{r['primary']}`. Контроль: `{r['control']}`. Совместные критерии допуска: **{'пройдены в этом историческом опыте' if r['admitted'] else 'не пройдены'}**.", '',
            '| Вариант | CAGR полной истории | Полная просадка | Поздний net за 608 дней | Поздняя просадка | Отдельный 2024 | Эпизодов позднее |',
            '|---|---:|---:|---:|---:|---:|---:|']
        def pct(v):
            return 'не подтверждено' if v is None else f'{v:+.2f}%'
        for d in details:
            lines.append(f"| `{d['model']}` | {pct(d['full_cagr_pct'])} | {pct(d['full_max_mark_close_drawdown_pct'])} | {pct(d['later_return_pct'])} | {pct(d['later_max_mark_close_drawdown_pct'])} | {pct(d['validation_return_pct'])} | {d['later_completed_episodes']} |")
        lines += ['', '### Исполнение, годы и риск по каждому варианту', '']
        for name in order:
            get = lambda p: next(x for x in r['rows'] if x['model'] == name and x['period'] == p)
            full, late = get('full'), get('later')
            years = ', '.join(f"{a['year']}{' (январь–август)' if not a['full_year'] else ''}: {pct(a['return_pct'])}" for a in full['annual'])
            lev = full['leverage_audit']; stat = late['episode_statistics']
            lines += [f"**`{name}`** — финальные деньги полной истории {full['final_balance']:.2f} USDT; накопленный net {pct(full['return_pct'])}. Годовые сегменты одного счёта: {years}.", '',
                f"Поздний net при двойных комиссии/slippage: {pct(get('later_double_costs')['return_pct'])}; при задержке ещё 2 часа: {pct(get('later_delay2')['return_pct'])}. Комиссии позднего счёта {late['fees']:.2f} USDT, funding {late['funding_cashflow']:+.2f} USDT. Отдельных исполнений ног {late['order_fills']}, завершённых эпизодов {late['completed_episodes']}.", '',
                f"Максимальный наблюдавшийся номинал/капитал на полной истории: {lev.get('max_mark_close_gross', 0):.4f}×. Поздние месяцы плюс/ноль/минус: {late['positive_months']}/{late['zero_months']}/{late['negative_months']}. Доля пяти лучших эпизодов в итоговом net: {pct(None if stat['largest_five_as_fraction_of_net'] is None else 100*stat['largest_five_as_fraction_of_net'])}; доля может превышать 100%, когда остальные результаты уменьшают прибыль.", '']
        lines += ['### Семь отдельных 365-дневных стартов', '']
        for name, origins in r['origin_sensitivity'].items():
            lines.append(f"`{name}`: проверены {origins['qualified']}/{origins['total']}, положительных {origins['positive']}, отрицательных {origins['negative']}, худший результат {pct(origins['worst_return_pct'])}. Интервалы перекрываются и не являются независимыми вероятностями успеха.")
            lines.append('')
    root_log = (package / 'budget/root-tests.log').read_text()
    match = re.search(r'(\d+) passed, (\d+) subtests passed', root_log)
    if not match or int(match[1]) < 756:
        raise ValueError('Full new code tests not confirmed')
    summary['tests'] = {'new': 23, 'full_repository_passed': int(match[1]), 'additional_subtests_passed': int(match[2]), 'new_included_in_full': True}
    summary['audited_events'] = {k: sum(s['event_audit'][k] for s in summary['stages'].values()) for k in ('fill_rows', 'funding_rows', 'entry_groups')}
    summary['financial_files_reproduced'] = 164 * 4
    lines += ['## Воспроизводимость и публикация', '',
        f"Всего 164 отчёта в трёх этапах, включая повторяющиеся контроли и перекрывающиеся периоды, не 164 независимые стратегии. Все 656 файлов исполнений, funding, эпизодов и кривых совпали с отдельными чистыми повторными запусками. Полный набор проекта: {match[1]} тестов плюс {match[2]} дополнительных подпроверок; 23 новых входят в общий набор.", '',
        f"Независимая сверка сохранённых событий проверила {summary['audited_events']['fill_rows']} исполнений, {summary['audited_events']['funding_rows']} записей funding и {summary['audited_events']['entry_groups']} групп входа. Проверены исходные цены, издержки, шаг/минимум, участие в объёме, количества на момент начисления и итоговые деньги. Это проверка заявленной модели исполнения, не подтверждение реальных биржевых сделок.", '',
        'В первом workflow пересчёт и побайтовое сравнение прошли, но отправка результатов остановилась из-за более свежих коммитов ветки. Финальная публикация переносит те же проверенные файлы поверх актуальной ветки без force-push; финансовые расчёты ради публикации не меняются.', '',
        'В main входят конечные исследовательские модули, тесты и все результаты. Известное неисправленное архивное ядро PR #134 в main не копируется. Его явное использование для исторического воспроизведения описано в README и требует отдельного архива с проверенными хешами. Дефект не устранён: полный снимок позволяет избежать его проявления здесь, но не гарантирует корректность произвольного будущего потока.', '',
        'Бета-модель, запись которой была заблокирована в начале прохода, не реализована и не использовалась. Никаких реальных ордеров, ключей, изменения производственного плеча или расписания. Расходы/funding-марки и требования маржи являются модельными допущениями; налоги, биржа, USDT, инфраструктура и хранение не учтены полностью.', '',
        '**Положительные исторические строки не доказывают стабильные 500% годовых.** При публикации сохраняются все проигравшие варианты, отрицательные годы, концентрация прибыли и повторное использование истории. Код сливается для сохранения и воспроизведения исследования, не как одобрение реального капитала.', '']
    root = repo / 'research/holding_horizon'
    write(root / 'verified_summary.json', summary)
    (root / 'RESULTS_RU.md').write_text('\n'.join(lines), encoding='utf-8')
    with (root / 'all_comparisons.csv').open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(compact[0])); writer.writeheader(); writer.writerows(compact)
    stamp = {'all_three_result_hashes_and_proofs_match': True, 'all_events_independently_checked': True,
        'reports': 164, 'financial_files': 656, 'source_files_checked': sorted(source_files),
        'summary_sha256': digest(summary), 'no_new_account_simulation': True, 'engine_not_copied_to_main': True}
    write(root / 'PUBLICATION_REVIEW.json', stamp)
    print('SUMMARY', json.dumps(summary, ensure_ascii=False), flush=True)
    print('PUBLICATION_REVIEW', json.dumps(stamp), flush=True)
    return summary


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--package', type=Path, required=True); p.add_argument('--repo', type=Path, default=Path('.'))
    a = p.parse_args(); publish(a.package, a.repo)

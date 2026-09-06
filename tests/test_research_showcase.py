"""Read-only publication tests. No experiment engine is imported or repaired."""
import copy
import csv
import gzip
import importlib.util
import json
from pathlib import Path
import pytest

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location('showcase_export', ROOT / 'scripts/build_research_showcase.py')
EXPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORT)


def curve(tmp_path, rows):
    path = tmp_path / 'curve.csv.gz'
    with gzip.open(path, 'wt', newline='') as f:
        writer = csv.writer(f); writer.writerow(['time', 'equity']); writer.writerows(rows)
    return path


def test_daily_display_preserves_hourly_worst_drawdown(tmp_path):
    path = curve(tmp_path, [('2024-01-01T01:00:00Z', 11000), ('2024-01-01T02:00:00Z', 8800), ('2024-01-02T00:00:00Z', 10500)])
    points = EXPORT.summarize_curve(path, 10000.)
    assert len(points) == 2 and points[-1][1] == 10500.
    assert points[-1][2] == pytest.approx(-20.)


@pytest.mark.parametrize('value', ['nan', 'inf', '-1', '0'])
def test_bad_equity_is_not_repaired(tmp_path, value):
    path = curve(tmp_path, [('2024-01-01T01:00:00Z', value)])
    with pytest.raises(ValueError): EXPORT.summarize_curve(path, 10000.)


def test_duplicate_or_wrong_timezone_rejected(tmp_path):
    for rows in [[('2024-01-01T01:00:00Z', 10000)] * 2, [('2024-01-01T01:00:00+03:00', 10000)]]:
        with pytest.raises(ValueError): EXPORT.summarize_curve(curve(tmp_path, rows), 10000.)


def test_canonical_result_identity_does_not_depend_on_json_order():
    assert EXPORT.canonical({'b': 2, 'a': 1}) == EXPORT.canonical({'a': 1, 'b': 2})
    assert EXPORT.canonical({'a': 1}) != EXPORT.canonical({'a': 2})


@pytest.fixture
def snapshot():
    path = ROOT / 'frontend/data/research-evidence.json'
    assert path.exists(), 'Run the finite evidence exporter before publication'
    return json.loads(path.read_text())


def test_all_fifteen_models_and_matching_periods(snapshot):
    EXPORT.validate_snapshot(snapshot)
    assert len(snapshot['models']) == 15
    assert snapshot['models'][0]['id'] == 'old_pair_1x'
    assert snapshot['default_period'] == 'full'
    assert sum(snapshot['source']['report_counts'].values()) == 147
    assert snapshot['live_ready'] is False


def test_snapshot_hash_matches_manifest(snapshot):
    import hashlib
    raw = (ROOT / 'frontend/data/research-evidence.json').read_bytes()
    manifest = json.loads((ROOT / 'frontend/data/research-manifest.json').read_text())
    assert hashlib.sha256(raw).hexdigest() == manifest['snapshot_sha256']
    assert manifest['trading_code_imported'] is False


def test_bad_result_never_becomes_dashboard_success(snapshot):
    broken = copy.deepcopy(snapshot)
    broken['models'][0]['periods']['full']['qualification']['qualified_historical_scenario'] = False
    with pytest.raises(ValueError): EXPORT.validate_snapshot(broken)


def test_losing_years_and_partial_2026_are_preserved(snapshot):
    model = next(m for m in snapshot['models'] if m['id'] == 'runner720_125x')
    annual = {a['year']: a for a in model['periods']['full']['annual']}
    assert annual[2023]['return_pct'] < 0 and annual[2024]['return_pct'] < 0
    assert annual[2026]['full_year'] is False
    assert model['periods']['later']['return_pct'] == pytest.approx(59.325956057584264)
    assert model['periods']['full']['cagr_pct'] == pytest.approx(11.969770827754056)


def test_unknown_stress_is_absent_not_zero(snapshot):
    model = next(m for m in snapshot['models'] if m['id'] == 'old_pair_15x')
    assert 'full_double_costs' not in model['stresses']


def test_only_one_navigation_entry_exists():
    html = (ROOT / 'frontend/live.html').read_text()
    assert html.count('href="./research.html"') == 1


def test_exporter_does_not_import_draft_trading_code():
    import ast
    tree = ast.parse((ROOT / 'scripts/build_research_showcase.py').read_text())
    for item in ast.walk(tree):
        if isinstance(item, ast.ImportFrom): assert not (item.module or '').startswith(('research.', 'fin.'))
        if isinstance(item, ast.Import): assert all(not a.name.startswith(('research.', 'fin.')) for a in item.names)
    assert not (ROOT / 'research/relative_futures/account.py').exists(), 'Do not release known-defective draft engine through this PR'


def test_ui_contains_no_html_injection_or_trading_api():
    js = (ROOT / 'frontend/research.js').read_text()
    for token in ('innerHTML', 'outerHTML', 'insertAdjacentHTML', 'eval(', 'WebSocket(', "method: 'POST'", '/fapi/', 'localStorage'):
        assert token not in js
    assert "credentials: 'omit'" in js

"""Finite browser acceptance checks; screenshots are real rendered pages, not mockups."""
from __future__ import annotations
import functools
import http.server
import json
import threading
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parents[1]
OUT = ROOT / 'showcase-evidence'


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def main():
    OUT.mkdir(exist_ok=True)
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(ROOT / 'frontend')))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    url = f'http://127.0.0.1:{server.server_port}/research.html'
    checks = []
    snapshot = json.loads((ROOT / 'frontend/data/research-evidence.json').read_text())
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={'width': 1440, 'height': 1000}, device_scale_factor=1)
            page = context.new_page()
            errors = []
            requests = []
            page.on('pageerror', lambda err: errors.append(str(err)))
            page.on('request', lambda req: requests.append((req.method, req.url)))
            page.goto(url, wait_until='networkidle')
            page.wait_for_selector('html[data-ready="true"]')
            assert page.locator('#model-select option').count() == 15
            assert page.locator('#model-select').input_value() == 'runner720_125x'
            assert page.locator('[data-period="full"]').get_attribute('aria-pressed') == 'true'
            assert page.locator('#comparison-rows tr').count() == 15
            assert page.locator('#comparison-rows tr').first.inner_text().startswith('Исходная пара')
            for model in snapshot['models']:
                page.select_option('#model-select', model['id'])
                for period in ('full', 'later', 'validation'):
                    page.locator(f'[data-period="{period}"]').click()
                    assert page.locator('#candidate-title').inner_text() == model['label']
                    assert page.locator('#equity-chart path').count() == 2
                    assert 'NaN' not in page.locator('#equity-chart').inner_html()
                    actual = page.locator('#metric-grid .metric-value').nth(0).inner_text()
                    expected = f"{model['periods'][period]['return_pct']:.2f}".replace('.', ',')
                    assert expected in actual.replace('\u00a0', '').replace('\u202f', ''), (actual, expected)
                    checks.append(f'{model["id"]}/{period}')
            page.select_option('#model-select', 'runner720_125x')
            page.locator('[data-period="full"]').click()
            assert page.locator('#annual-rows tr').filter(has_text='2023').locator('td').last.get_attribute('class') == 'negative'
            assert 'неполный год' in page.locator('#annual-rows').inner_text()
            assert '31,38%' in page.locator('#metric-grid').inner_text()
            page.screenshot(path=str(OUT / 'desktop-full.png'), full_page=True)
            page.locator('[data-period="later"]').click()
            assert '59,33%' in page.locator('#metric-grid').inner_text()
            page.locator('#show-dd').click()
            assert page.locator('#show-dd').get_attribute('aria-pressed') == 'true'
            page.screenshot(path=str(OUT / 'desktop-drawdown.png'), full_page=True)
            page.locator('#show-equity').click()
            page.select_option('#model-select', 'old_pair_15x')
            page.locator('[data-period="full"]').click()
            assert 'Не рассчитано' in page.locator('#stress-rows').inner_text()
            page.select_option('#model-select', 'multi_risk30_cap15')
            assert 'не прошёл' in page.locator('#candidate-kind').inner_text()
            page.select_option('#model-select', 'runner720_125x')
            for width in (390, 768):
                page.set_viewport_size({'width': width, 'height': 844})
                page.wait_for_timeout(150)
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'), width
                assert page.locator('#model-select').is_visible()
                assert page.locator('.boundary').is_visible()
                page.screenshot(path=str(OUT / f'width-{width}.png'), full_page=True)
            # Real keyboard interaction without a pointing device.
            page.locator('#model-select').focus()
            page.keyboard.press('ArrowDown'); page.keyboard.press('Enter')
            assert page.locator('#candidate-title').inner_text()
            assert not errors, errors
            assert all(method == 'GET' for method, _ in requests)
            assert all(address.startswith(f'http://127.0.0.1:{server.server_port}/') for _, address in requests), requests
            # Bad source must fail closed and never keep previous positive metrics visible.
            bad_page = context.new_page()
            bad_page.route('**/data/research-evidence.json', lambda route: route.fulfill(status=500, body='unavailable'))
            bad_page.goto(url, wait_until='networkidle')
            assert bad_page.locator('#load-error').is_visible()
            assert bad_page.locator('#content').is_hidden()
            bad_page.unroute('**/data/research-evidence.json')
            bad_page.locator('#retry').click()
            bad_page.wait_for_selector('html[data-ready="true"]')
            assert bad_page.locator('#load-error').is_hidden()
            invalid = context.new_page()
            invalid.route('**/data/research-evidence.json', lambda route: route.fulfill(status=200, content_type='application/json', body='{"schema_version":1,"live_ready":true}'))
            invalid.goto(url, wait_until='networkidle')
            assert invalid.locator('#load-error').is_visible()
            assert invalid.locator('#content').is_hidden()
            browser.close()
    finally:
        server.shutdown(); server.server_close(); worker.join(timeout=5)
    proof = {'comparisons_checked': len(checks), 'all_model_periods': checks, 'original_control_first': True,
             'missing_stress_not_zero': True, 'negative_years_visible': True, 'partial_year_label': True,
             'viewports': [1440, 768, 390], 'horizontal_document_overflow': False,
             'network': 'same-origin GET only', 'bad_source_fail_closed': True, 'retry_verified': True,
             'javascript_page_errors': errors, 'real_rendered_screenshots': 4, 'live_orders': False}
    (OUT / 'BROWSER_REVIEW.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2))
    print(json.dumps(proof, ensure_ascii=False))


if __name__ == '__main__':
    main()

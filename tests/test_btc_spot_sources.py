import pytest
from research.btc_spot_regime.data import files_for_month,days


def source():
    return dict(month='2026-08',frequency='daily',parts=[{'filename':f'BTCUSDT-1h-{d}.zip'} for d in days('2026-08')])


def test_daily_replacement_requires_all_31_dates():
    row=source();assert len(files_for_month(row))==31
    row['parts'].pop()
    with pytest.raises(ValueError):files_for_month(row)


def test_duplicate_date_is_not_complete_coverage():
    row=source();row['parts'][-1]=row['parts'][0]
    with pytest.raises(ValueError):files_for_month(row)


def test_foreign_period_or_path_is_not_accepted():
    row=source();row['parts'][-1]={'filename':'../BTCUSDT-1h-2026-08-31.zip'}
    with pytest.raises(ValueError):files_for_month(row)


def test_monthly_identity_exact():
    row=dict(month='2026-07',filename='BTCUSDT-1h-2026-07.zip')
    assert files_for_month(row)==[row]
    row['filename']='BTCUSDT-1h-2026-08.zip'
    with pytest.raises(ValueError):files_for_month(row)


def test_leap_month_days():
    assert len(days('2024-02'))==29 and len(days('2025-02'))==28

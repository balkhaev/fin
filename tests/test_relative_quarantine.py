"""These tests document a KNOWN UNPATCHED defect; they do not claim to fix it."""
import numpy as np
from test_relative_futures import data,replay
from research.relative_futures.study import qualify


def test_original_inactive_nan_defect_is_detected_and_quarantined():
    frames=data()
    frames['ETHUSDT']['mark_close']=np.nan
    frames['ETHUSDT']['mark_high']=np.nan
    frames['ETHUSDT']['mark_low']=np.nan
    weights=np.zeros((120,2));weights[:20,0]=1
    report,_,_,_,curve=replay(frames,weights)
    assert curve.equity.isna().any(), 'Expected documented defect no longer matches saved source'
    review=qualify(report,curve)
    assert not review['qualified_historical_scenario']
    assert 'nonfinite_equity_path_unrepaired' in review['issues']
    assert curve.equity.isna().any(), 'Review must not modify/repair evidence'


def test_complete_flat_control_passes_evidence_coverage_without_claiming_profit():
    report,_,_,_,curve=replay(data(),np.zeros((120,2)))
    assert qualify(report,curve)['qualified_historical_scenario']
    assert report['return_pct']==0

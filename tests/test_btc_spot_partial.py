import numpy as np
import pytest
from research.btc_spot_regime.data import timestamps


def test_off_grid_time_is_rejected_in_strict_mode():
    with pytest.raises(ValueError):timestamps([1518168494789])


def test_audit_mode_keeps_original_millisecond_not_nearest_hour():
    x=timestamps([1518168494789],require_hour=False)
    assert x[0]==1518168494789 and x[0]%3600000!=0


def test_off_grid_mask_does_not_change_valid_neighbor():
    x=timestamps([1518166800000,1518168494789,1518170400000],require_hour=False)
    np.testing.assert_array_equal(x[x%3600000==0],[1518166800000,1518170400000])


def test_audit_mode_still_rejects_mixed_units():
    with pytest.raises(ValueError):timestamps([1735689600000,1735689600000000],require_hour=False)

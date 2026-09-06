"""Causal heuristic qualification, not a statistical or live-trading certificate."""
from datetime import datetime, timezone
from statistics import mean, stdev
import math
from . import FAMILIES

DAY=86400000


def qualify(records, family, decision_ms, model_id):
    if family not in FAMILIES: raise ValueError('Unknown opportunity family')
    ids=set(); usable=[]
    for r in records:
        if r['trade_id'] in ids: raise ValueError('Duplicate calibration trade')
        ids.add(r['trade_id'])
        if r['model_id']!=model_id: raise ValueError('Calibration model identity changed')
        if r['family'] not in FAMILIES or r['entry_ms']>=r['exit_ms']:
            raise ValueError('Invalid calibration chronology')
        if not all(math.isfinite(r[k]) for k in ('net_fraction','fee_fraction')) or r['fee_fraction']<0:
            raise ValueError('Invalid calibration values')
        # Later observations never enter a prior decision, including closures at it.
        if (r['family']==family and decision_ms-365*DAY<=r['exit_ms']<decision_ms):
            usable.append(r)
    usable=sorted(usable,key=lambda x:(x['exit_ms'],x['trade_id']))[-60:]
    values=[r['net_fraction'] for r in usable]; n=len(values)
    wins=sum(x for x in values if x>0); losses=-sum(x for x in values if x<0)
    pf=wins/losses if losses>0 else (None if wins==0 else 1000000.)
    avg=mean(values) if n else 0.; bound=avg-1.28*stdev(values)/math.sqrt(n) if n>=2 else -1.
    double=mean([r['net_fraction']-r['fee_fraction'] for r in usable]) if n else 0.
    months=len({datetime.fromtimestamp(r['exit_ms']/1000,timezone.utc).strftime('%Y-%m') for r in usable})
    reasons=[]
    if n<12:reasons.append('insufficient_closed_trades')
    if months<2:reasons.append('insufficient_months')
    if (pf or 0)<1.10:reasons.append('profit_factor_below_gate')
    if bound<=0:reasons.append('conservative_mean_not_positive')
    if double<=0:reasons.append('double_commission_mean_not_positive')
    return dict(eligible=not reasons,n=n,months=months,profit_factor=pf,
                mean_net_fraction=avg,conservative_mean=bound,double_fee_mean=double,
                latest_exit_ms=max((r['exit_ms'] for r in usable),default=None),
                reasons=reasons,statistical_proof=False)

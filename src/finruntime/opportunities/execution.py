"""Adapter, not a new broker: every modeled fill uses finruntime execution.

This low-level paper adapter does not itself collect calibration or orchestrate
strategies. Those missing controller responsibilities must not be assumed.
"""
from datetime import datetime, timezone
from dataclasses import asdict
from decimal import Decimal, localcontext
from pathlib import Path
import json
import math
from finruntime.canonical import sha256_id
from finruntime.models import MarketSnapshot, StrategySnapshot, SourceObservation
from finruntime.execution import PaperQuote, PaperBrokerPolicy, PlannerPolicy, build_execution_plan, execute_paper_cycle
from finruntime.portfolio.risk import RiskLimits, apply_pretrade_risk, decimal_text
from finruntime.portfolio.accounting import PaperAccountState, mark_account
from finruntime.operations import PaperCycleRequest, PaperCyclePaths, run_paper_cycle
from . import STRATEGY_ID, VERSION
from .features import HOUR

D=Decimal


def utc(ms):return datetime.fromtimestamp(ms/1000,timezone.utc).isoformat().replace('+00:00','Z')

def request_for(account, frame, tick, desired_quantity, reason, fee_bps=10., slip_bps=5.):
    """Desired base quantity encoded into native target-weight contracts.

    Caller still owns statistical admission. Only paper/shadow is registered.
    Entry is fully funded, <=25% marked equity, and cannot add to an existing position.
    """
    account.validate();frame.validate()
    if account.strategy_id!=STRATEGY_ID or account.perp_positions or set(account.spot_positions)-{'BTCUSDT'}:
        raise ValueError('Wrong strategy or instrument exposure')
    for k in ('time_ms','observed_ms'):
        if type(tick.get(k)) is not int:raise ValueError('Integer quote timestamp required')
    if tick['time_ms']<frame.time_ms+HOUR:raise ValueError('One-hour signal delay required')
    if not 0<=tick['time_ms']-tick['observed_ms']<=30000 or tick.get('quality','ok')!='ok':
        raise ValueError('Quote is future, stale or unavailable')
    if not isinstance(tick.get('source'),str) or not tick['source']:raise ValueError('Source identity required')
    numbers=(desired_quantity,tick['price'],tick['capacity'],fee_bps,slip_bps)
    if not all(math.isfinite(float(v)) for v in numbers):raise ValueError('Nonfinite execution input')
    if tick['price']<=0 or tick['capacity']<0 or not 0<=fee_bps<=100 or not 0<=slip_bps<=100:
        raise ValueError('Invalid quote or cost scenario')
    now=utc(tick['time_ms']);observed=utc(tick['observed_ms']);price=D(str(tick['price']))
    refs={'spot':{'BTCUSDT':decimal_text(price)},'perp':{}}
    quantity=D(str(desired_quantity));current=D(account.spot_positions.get('BTCUSDT','0'))
    if quantity<0:raise ValueError('No spot shorting')
    if quantity>current+D('.000000001'):
        if current>0:raise ValueError('No averaging or multiple simultaneous BTC entries')
        if not frame.healthy:raise ValueError('Unhealthy frame cannot authorize exposure')
        if tick['time_ms']-frame.time_ms>3*HOUR:raise ValueError('Expired feature support')
        equity=D(account.cash)+current*price
        if quantity*price>equity*D('.25'):raise ValueError('Entry allocation cap exceeded')
        if quantity*price<10:raise ValueError('Below scenario minimum notional')
        lots=quantity/D('.00001')
        if lots!=lots.to_integral_value():raise ValueError('Entry quantity off scenario lot grid')
    with localcontext() as context:
        context.prec=50
        weight=quantity*price/D(account.equity)
    source_hash=sha256_id(tick)
    market=MarketSnapshot.create(as_of_utc=utc(frame.time_ms),decision_time_utc=now,
        sources={'bars':SourceObservation('bars',utc(frame.time_ms),utc(frame.time_ms),frame.identity,
                                         quality='ok' if frame.healthy else 'invalid'),
                 'quote':SourceObservation('quote',observed,observed,source_hash)},
        spot={'BTCUSDT':{'reference_price':decimal_text(price)}},
        quality_flags=('historical_execution_proxy',) if tick.get('archive_proxy') else ())
    strategy=StrategySnapshot.create(strategy_id=STRATEGY_ID,strategy_version=VERSION,
        decision_time_utc=now,market_snapshot_id=market.snapshot_id,state_sequence=int(tick['time_ms']),
        targets={'spot':{'BTCUSDT':decimal_text(weight)} if quantity else {},'perp':{}},
        gross_target=decimal_text(weight),cash_target=decimal_text(max(D(0),D(1)-weight)),
        risk={'gross_cap':'1','paper_only':True,'qualification_is_not_proof':True},reasons=(reason,))
    quote=PaperQuote('BTCUSDT','spot',observed,source_hash,None,None,decimal_text(price),
                     decimal_text(D(str(tick['capacity']))),quality='ok')
    broker=PaperBrokerPolicy(D(str(fee_bps)),D('6'),D(str(slip_bps)),D('0'),D('1'))
    return PaperCycleRequest(market,strategy,account,(quote,),refs,('bars','quote'),
        broker_policy=broker,risk_limits=RiskLimits(D('1'),D('1'),D('.01')),
        planner_policy=PlannerPolicy(D(str(slip_bps+1)),D('10'),900,D('.000000001')),
        modelled_slippage_bps=decimal_text(D(str(slip_bps))))


def execute_request(request, paths=None):
    """Same native planner/broker path for in-memory tests and durable operation."""
    if paths is not None:
        result=run_paper_cycle(request=request,paths=paths)
        root=Path(result.cycle_directory)
        account=PaperAccountState(**json.loads((root/'account_state.json').read_text()))
        account.validate()
        fills=json.loads((root/'fill_events.json').read_text())
        outcomes=json.loads((root/'fill_outcomes.json').read_text())
        return account,fills,outcomes
    request.validate()
    portfolio=request.starting_account.to_portfolio_state()
    risk=apply_pretrade_risk(strategy_snapshot=request.strategy_snapshot,portfolio_state=portfolio,
        market_snapshot=request.market_snapshot,reference_prices=request.reference_prices,
        critical_sources=request.critical_sources,limits=request.risk_limits)
    plan=build_execution_plan(strategy_snapshot=request.strategy_snapshot,portfolio_state=portfolio,
        market_snapshot=request.market_snapshot,risk_decision=risk,reference_prices=request.reference_prices,
        policy=request.planner_policy)
    result=execute_paper_cycle(plan=plan,account_state=request.starting_account,quotes=request.quotes,
        mark_prices=request.reference_prices,policy=request.broker_policy)
    return result.account_state,[asdict(f) for f in result.fill_events],[asdict(x) for x in result.outcomes]


def marked(account, tick):
    return mark_account(account,as_of_utc=utc(tick['time_ms']),
        reference_prices={'spot':{'BTCUSDT':decimal_text(D(str(tick['price'])))},'perp':{}})

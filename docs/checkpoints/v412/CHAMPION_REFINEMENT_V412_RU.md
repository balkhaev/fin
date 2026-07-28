# V405–V412 — champion refinement

## Frozen roles

- V75 remains the primary paper/shadow champion.
- V136 remains an exact execution-shadow; it is not promoted historically.
- V28 remains the mandatory control.
- V285 and V365 remain rejected-after-OOS anti-controls.

## Historical V75 vs V136

| Metric | V75 | V136 | Delta |
|---|---:|---:|---:|
| Full CAGR | 30.68% | 30.77% | +0.089 pp |
| Total return | 335.54% | 337.18% | +1.637 pp |
| Sharpe | 1.329 | 1.335 | +0.006 |
| Max DD | -21.59% | -21.82% | -0.232 pp |
| Turnover | 10.64x | 9.95x | 6.55% reduction |
| Modelled costs | $6,168.69 | $5,821.22 | $347.47 saving |

V136 improved full CAGR by only 0.089 percentage points and reduced turnover by 6.55%. It therefore missed the frozen 0.5 pp uplift and 10% turnover-reduction gates. This is too small to justify replacing V75 from historical evidence.

## Refinement target

The next evidence is forward execution quality, not another backtest grid:

1. minimum 180 calendar days;
2. at least 25 V136 target changes;
3. zero reconciliation breaks and 100% source-hash matches;
4. V136 turnover reduction at least 10%;
5. V136 net paper return not below V75;
6. V136 drawdown no more than 2% worse;
7. paper slippage no more than 1.5x the frozen model.

```text
historical_parameter_search = closed
V75 role                    = primary paper/shadow
V136 role                   = execution shadow only
new sleeve allocation       = 0%
live_ready                  = false
real_leverage_authorized    = false
```

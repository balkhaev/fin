# Active V131–V138 — regime overlays and execution control

This cycle keeps the frozen V75 ATLAS-NX signals unchanged and evaluates three additive layers:

- V131/V132: signed BTC/ETH perpetual overlay driven by nine-asset breadth;
- V133/V134: long-only risk-on hysteresis overlay;
- V135/V136: risk-first no-trade execution state machine;
- V137: immutable cost, latency, funding and margin audit;
- V138: checkpoint and decision.

Every comparison includes the exact yearly return of the original V75 control. No candidate was promoted.

## Reproduce

```bash
python research/active_v131_v138/source/run_frozen_breadth.py \
  --atlas-root research/active_v131_v138/dependencies/atlas \
  --output /tmp/v132

python research/active_v131_v138/source/run_hysteresis.py \
  --atlas-root research/active_v131_v138/dependencies/atlas \
  --output /tmp/v134

python research/active_v131_v138/source/run_execution_layer.py \
  --atlas-root research/active_v131_v138/dependencies/atlas \
  --output /tmp/v136
```

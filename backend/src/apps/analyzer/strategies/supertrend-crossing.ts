import { Candle } from "../../../types"
import { SupertrendOpts, getSupertrendSignal } from "../signals/supertrend"
import { checkSignalsCrossing } from "../utils"

/**
 * По умолчанию стратегия двойного супертренда
 */
const defaultOpts = [
  { period: 4, multiplier: 2 },
  { period: 25, multiplier: 5 },
]

export function getSupertrendCrossingSignal(
  candles: Candle[],
  opts: SupertrendOpts[] = defaultOpts
) {
  return checkSignalsCrossing(
    opts.map((cfg) => getSupertrendSignal(candles, cfg.period, cfg.multiplier))
  )
}

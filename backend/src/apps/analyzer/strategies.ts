import { Candle, Signal } from "../../types"
import { SupertrendOpts, getSupertrendSignal } from "./signals/supertrend"

/**
 * Функция проверки пересечения сигналов
 */
function checkSignalsCrossing(signals: Signal[]): Signal {
  const firstSignal = signals[0]
  const allEqual = signals.every((s) => s === firstSignal)

  if (!allEqual) return 0

  return firstSignal
}

export function getSupertrendCrossingSignal(
  candles: Candle[],
  opts: SupertrendOpts[]
) {
  return checkSignalsCrossing(
    opts.map((cfg) => getSupertrendSignal(candles, cfg.period, cfg.multiplier))
  )
}

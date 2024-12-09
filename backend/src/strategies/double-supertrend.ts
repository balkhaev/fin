import { supertrend } from "./indicators/supertrend"
import { Candlestick } from "../types"

type TrendOpts = {
  period?: number
  multiplier?: number
}

type Opts = {
  realtime?: TrendOpts
  global?: TrendOpts
}

export function doubleSupertrendStrategy(
  initialArray: Candlestick[],
  { realtime, global }: Opts = {}
) {
  // Calculate fast and slow supertrends
  const realtimeTrend = supertrend({
    initialArray,
    period: realtime?.period ?? 4,
    multiplier: realtime?.multiplier ?? 2,
  })

  const globalTrend = supertrend({
    initialArray,
    period: global?.period ?? 25,
    multiplier: global?.multiplier ?? 5,
  })

  // Trim to the same length to compare correctly
  const minLength = Math.min(
    realtimeTrend.superTrend.length,
    globalTrend.superTrend.length
  )

  // If too few data for analysis - no signals
  if (minLength < 2) {
    return { signal: 0, data: "min length :(" }
  }

  // Last three candles
  const [prevGlobal, currGlobal] = globalTrend.superTrend.slice(-2)
  const [prevStandard, currStandard] = realtimeTrend.superTrend.slice(-2)

  // Current conditions
  const isBuySignal = prevGlobal > prevStandard && currGlobal <= currStandard
  const isSellSignal = prevGlobal < prevStandard && currGlobal >= currStandard

  // Generate a new signal only if there was no signal in previous candles
  if (isBuySignal) {
    return {
      signal: 1,
      data: `${prevGlobal} > ${prevStandard} && ${currGlobal} <= ${currStandard}`,
    }
  }

  if (isSellSignal) {
    return {
      signal: -1,
      data: `${prevGlobal} < ${prevStandard} && ${currGlobal} >= ${currStandard}`,
    }
  }

  // If no signals, return neutral result
  return { signal: 0, data: "neutral" }
}

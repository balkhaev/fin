import { ATR } from "technicalindicators"
import { Candle, Signal } from "../../../types"

export type SupertrendOpts = {
  period: number
  multiplier: number
}

export function getSupertrendSignal(
  initialArray: Candle[],
  period: number,
  multiplier: number
): Signal {
  const high = initialArray.map((bar) => bar.high)
  const low = initialArray.map((bar) => bar.low)
  const close = initialArray.map((bar) => bar.close)

  const atrValues = ATR.calculate({ high, low, close, period })
  const r = initialArray.slice(period)

  const len = r.length
  if (len === 0) return 0

  const basicUpperBand = new Array<number>(len)
  const basicLowerBand = new Array<number>(len)

  for (let i = 0; i < len; i++) {
    const mid = (r[i].high + r[i].low) / 2
    basicUpperBand[i] = mid + multiplier * atrValues[i]
    basicLowerBand[i] = mid - multiplier * atrValues[i]
  }

  const finalUpperBand = new Array<number>(len)
  const finalLowerBand = new Array<number>(len)

  let prevFinalUpper = basicUpperBand[0]
  let prevFinalLower = basicLowerBand[0]

  finalUpperBand[0] = prevFinalUpper
  finalLowerBand[0] = prevFinalLower

  for (let i = 1; i < len; i++) {
    const prevClose = r[i - 1].close

    finalUpperBand[i] =
      basicUpperBand[i] < prevFinalUpper || prevClose > prevFinalUpper
        ? basicUpperBand[i]
        : prevFinalUpper

    finalLowerBand[i] =
      basicLowerBand[i] > prevFinalLower || prevClose < prevFinalLower
        ? basicLowerBand[i]
        : prevFinalLower

    prevFinalUpper = finalUpperBand[i]
    prevFinalLower = finalLowerBand[i]
  }

  const superTrend = new Array<number>(len)
  let signal: Signal = 0

  // Инициализация первого значения SuperTrend
  superTrend[0] =
    r[0].close > finalUpperBand[0] ? finalLowerBand[0] : finalUpperBand[0]

  for (let i = 1; i < len; i++) {
    const prevSTVal = superTrend[i - 1]
    const currClose = r[i].close

    if (prevSTVal === finalUpperBand[i - 1] && currClose <= finalUpperBand[i]) {
      superTrend[i] = finalUpperBand[i]
    } else if (
      prevSTVal === finalUpperBand[i - 1] &&
      currClose > finalUpperBand[i]
    ) {
      superTrend[i] = finalLowerBand[i]
      signal = 1 // Сигнал для начала бычьего тренда
    } else if (
      prevSTVal === finalLowerBand[i - 1] &&
      currClose >= finalLowerBand[i]
    ) {
      superTrend[i] = finalLowerBand[i]
    } else if (
      prevSTVal === finalLowerBand[i - 1] &&
      currClose < finalLowerBand[i]
    ) {
      superTrend[i] = finalUpperBand[i]
      signal = -1 // Сигнал для начала медвежьего тренда
    }
  }

  return signal
}

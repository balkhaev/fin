import { KlineIntervalV3 } from "bybit-api"
import { Analyze, Candle, Signal, Ticker } from "../../types"
import { getSupertrendSignal } from "./signals/supertrend"
import { checkSignalsCrossing, getSupertrendCrossingSignal } from "./strategies"

type IndicatorFunction = (
  currentPrice: number,
  analysis: Analyze,
  ticker: Ticker
) => number

const INDICATOR_WEIGHTS = {
  sma: 1,
  rsi: 1.5,
  macd: 1.2,
  bollingerBands: 1,
  stochasticRsi: 1,
  adx: 1.3,
  cci: 1,
  change24h: 0.1,
  openInterest: 0.1,
}

// Вспомогательная функция для безопасного деления
const safeDivide = (numerator: number, denominator: number): number =>
  denominator !== 0 ? numerator / denominator : 0

type CandlesOpts = {
  candles: Pick<Record<KlineIntervalV3, Candle[]>, "1" | "15" | "60" | "D">
  analysis: Analyze
  ticker: Ticker
}

export function analyzeCandles({ candles, analysis, ticker }: CandlesOpts): {
  tripleSupertrend: Signal
  doubleSupertrend: Signal
  macdSupertrend: Signal
  supertrend: Signal
  rating: number
} {
  const currentPrice = candles[15][candles[15].length - 1].close

  const indicators: IndicatorFunction[] = [
    // SMA
    (currentPrice, analysis) =>
      analysis.sma !== null
        ? ((currentPrice - analysis.sma) / analysis.sma) * INDICATOR_WEIGHTS.sma
        : 0,

    // RSI
    (_, analysis) =>
      analysis.rsi !== null
        ? ((analysis.rsi - 50) / 50) * INDICATOR_WEIGHTS.rsi
        : 0,

    // MACD
    (_, analysis) =>
      analysis.macd?.histogram !== undefined
        ? (analysis.macd.histogram / 100) * INDICATOR_WEIGHTS.macd
        : 0,

    // Bollinger Bands
    (currentPrice, analysis) => {
      const bands = analysis.bollingerBands
      if (bands) {
        const range = bands.upper - bands.lower
        const position = safeDivide(currentPrice - bands.lower, range)
        return (1 - 2 * position) * INDICATOR_WEIGHTS.bollingerBands
      }
      return 0
    },

    // Stochastic RSI
    (_, analysis) => {
      const stochastic = analysis.stochasticRsi
      if (stochastic) {
        const kNormalized = (stochastic.k - 50) / 50
        const dNormalized = (stochastic.d - 50) / 50
        return (
          ((kNormalized + dNormalized) / 2) * INDICATOR_WEIGHTS.stochasticRsi
        )
      }
      return 0
    },

    // ADX
    (_, analysis) => {
      const adx = analysis.adx
      if (adx && adx.adx > 25) {
        const diDifference = (adx.pdi - adx.mdi) / 100
        return diDifference * INDICATOR_WEIGHTS.adx
      }
      return 0
    },

    // CCI
    (_, analysis) =>
      analysis.cci !== null ? (analysis.cci / 200) * INDICATOR_WEIGHTS.cci : 0,

    // Price Change (основной фактор)
    (_, analysis) =>
      analysis.change24h !== undefined
        ? (analysis.change24h / 100) * INDICATOR_WEIGHTS.change24h
        : 0,

    // Open Interest (основной фактор)
    (_, __, ticker) => {
      if (ticker.openInterest !== undefined && ticker.volume24h !== undefined) {
        const percentage = safeDivide(ticker.openInterest, ticker.volume24h)

        return percentage * INDICATOR_WEIGHTS.openInterest
      }
      return 0
    },
  ]

  // Расчёт общего рейтинга
  const rating = indicators.reduce(
    (sum, indicator) => sum + indicator(currentPrice, analysis, ticker),
    0
  )

  const supertrend = getSupertrendSignal(candles[1], 10, 3)

  const tripleSupertrend = getSupertrendCrossingSignal(candles[60], [
    { period: 10, multiplier: 1 },
    { period: 11, multiplier: 2 },
    { period: 12, multiplier: 3 },
  ])

  const doubleSupertrend = getSupertrendCrossingSignal(candles[60], [
    { period: 10, multiplier: 3 },
    { period: 25, multiplier: 5 },
  ])

  const macdSupertrend = checkSignalsCrossing([
    analysis.macd?.histogram ? (analysis.macd.histogram > 0 ? 1 : 0) : 0,
    getSupertrendSignal(candles[60], 10, 3),
  ])

  return {
    tripleSupertrend,
    doubleSupertrend,
    macdSupertrend,
    supertrend,
    rating,
  }
}

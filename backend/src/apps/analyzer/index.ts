import { Analyze, Candle, Signal, Ticker } from "../../types"
import { getSupertrendCrossingSignal } from "./strategies"

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
  change24h: 1.5, // Наибольшее влияние
  openInterest: 1.5, // Наибольшее влияние
}

// Вспомогательная функция для безопасного деления
const safeDivide = (numerator: number, denominator: number): number =>
  denominator !== 0 ? numerator / denominator : 0

export function analyzeCandles(
  candles: Candle[],
  analysis: Analyze,
  ticker: Ticker
): { decision: Signal; rating: number } {
  const currentPrice = candles[candles.length - 1].close

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

  const signal = getSupertrendCrossingSignal(candles, [
    { period: 10, multiplier: 1 },
    { period: 11, multiplier: 2 },
    { period: 12, multiplier: 3 },
  ])

  return { decision: signal, rating }
}

import {
  ADX,
  ATR,
  BollingerBands,
  CCI,
  EMA,
  MACD,
  OBV,
  RSI,
  SMA,
  StochasticRSI,
} from "technicalindicators"
import { Analyze, Candle } from "../../types"
import { mom } from "./indicators/mom"

type IndicatorPeriods = {
  sma?: number
  rsi?: number
  macdFast?: number
  macdSlow?: number
  macdSignal?: number
  bollinger?: number
  bollingerStdDev?: number
  stochasticRsi?: number
  adx?: number
  cci?: number
  atr?: number
  momentum?: number
  ema?: number
}

const DEFAULT_PERIODS: Required<IndicatorPeriods> = {
  sma: 14,
  rsi: 14,
  macdFast: 12,
  macdSlow: 26,
  macdSignal: 9,
  bollinger: 20,
  bollingerStdDev: 2,
  stochasticRsi: 14,
  adx: 14,
  cci: 14,
  atr: 14,
  momentum: 10,
  ema: 5,
}

export function getTechnicalAnalyze(
  candles: Candle[],
  periods: IndicatorPeriods = {}
): Analyze {
  const {
    sma,
    rsi,
    macdFast,
    macdSlow,
    macdSignal,
    bollinger,
    bollingerStdDev,
    stochasticRsi,
    adx,
    cci,
    atr,
    momentum,
    ema,
  } = { ...DEFAULT_PERIODS, ...periods }

  const prices = candles.map((d) => d.close)
  const highs = candles.map((d) => d.high)
  const lows = candles.map((d) => d.low)
  const volumes = candles.map((d) => d.volume)

  // Однострочные вычисления с деструктуризацией последнего значения
  const [lastSMA] = SMA.calculate({ values: prices, period: sma }).slice(-1)
  const [lastRSI] =
    RSI.calculate({ values: prices, period: rsi }).slice(-1) ?? []
  const [lastMACD] =
    MACD.calculate({
      values: prices,
      fastPeriod: macdFast,
      slowPeriod: macdSlow,
      signalPeriod: macdSignal,
      SimpleMAOscillator: false,
      SimpleMASignal: false,
    }).slice(-1) ?? []
  const [lastBollinger] =
    BollingerBands.calculate({
      values: prices,
      period: bollinger,
      stdDev: bollingerStdDev,
    }).slice(-1) ?? []
  const [lastStochasticRsi] =
    StochasticRSI.calculate({
      values: prices,
      rsiPeriod: rsi,
      stochasticPeriod: stochasticRsi,
      kPeriod: 3,
      dPeriod: 3,
    }).slice(-1) ?? []
  const [lastADX] =
    ADX.calculate({ close: prices, high: highs, low: lows, period: adx }).slice(
      -1
    ) ?? []
  const [lastCCI] =
    CCI.calculate({ high: highs, low: lows, close: prices, period: cci }).slice(
      -1
    ) ?? []
  const [lastATR] =
    ATR.calculate({ high: highs, low: lows, close: prices, period: atr }).slice(
      -1
    ) ?? []
  const [lastOBV] =
    OBV.calculate({ close: prices, volume: volumes }).slice(-1) ?? []
  const [lastMomentum] = mom(prices, momentum).slice(-1) ?? []
  const [lastEma] =
    EMA.calculate({
      values: prices,
      period: ema,
    }).slice(-1) ?? []

  const lastPrice = prices[prices.length - 1]

  // Определение тренда
  let trend: "Bullish" | "Bearish" | "Neutral" = "Neutral"
  if (lastRSI !== undefined && lastMACD?.histogram !== undefined) {
    if (lastRSI > 50 && lastMACD.histogram > 0) {
      trend = "Bullish"
    } else if (lastRSI < 50 && lastMACD.histogram < 0) {
      trend = "Bearish"
    }
  }

  return {
    lastPrice,
    sma: lastSMA ?? null,
    rsi: lastRSI ?? null,
    stochasticRsi: lastStochasticRsi ?? null,
    adx: lastADX ?? null,
    macd: lastMACD ?? null,
    bollingerBands: lastBollinger ?? null,
    cci: lastCCI ?? null,
    atr: lastATR ?? null,
    obv: lastOBV ?? null,
    momentum: lastMomentum ?? null,
    ema: lastEma ?? null,
    trend,
  }
}

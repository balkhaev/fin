import {
  ADX,
  ATR,
  BollingerBands,
  CCI,
  MACD,
  OBV,
  RSI,
  SMA,
  StochasticRSI,
} from "technicalindicators"
import { Analyze, Candle } from "../../types"
import { mom } from "./indicators/mom"

export function getTechnicalAnalyze(historicalData: Candle[]): Analyze {
  const prices = historicalData.map((data) => data.close)
  const highs = historicalData.map((data) => data.high)
  const lows = historicalData.map((data) => data.low)
  const volumes = historicalData.map((data) => data.volume)

  const smaPeriod = 14
  const rsiPeriod = 14
  const macdFastPeriod = 12
  const macdSlowPeriod = 26
  const macdSignalPeriod = 9
  const bollingerPeriod = 20
  const bollingerStdDev = 2
  const stochasticRsiPeriod = 14
  const adxPeriod = 14
  const cciPeriod = 14
  const atrPeriod = 14
  const momentumPeriod = 10

  // Вычисление индикаторов без использования функций calculateSMA и calculateRSI
  const sma = SMA.calculate({ values: prices, period: smaPeriod })
  const rsi = RSI.calculate({ values: prices, period: rsiPeriod })
  const macd = MACD.calculate({
    values: prices,
    fastPeriod: macdFastPeriod,
    slowPeriod: macdSlowPeriod,
    signalPeriod: macdSignalPeriod,
    SimpleMAOscillator: false,
    SimpleMASignal: false,
  })
  const bollingerBands = BollingerBands.calculate({
    values: prices,
    period: bollingerPeriod,
    stdDev: bollingerStdDev,
  })
  const stochasticRsi = StochasticRSI.calculate({
    values: prices,
    rsiPeriod: rsiPeriod,
    stochasticPeriod: stochasticRsiPeriod,
    kPeriod: 3,
    dPeriod: 3,
  })
  const adx = ADX.calculate({
    close: prices,
    high: highs,
    low: lows,
    period: adxPeriod,
  })
  const cci = CCI.calculate({
    high: highs,
    low: lows,
    close: prices,
    period: cciPeriod,
  })
  const atr = ATR.calculate({
    high: highs,
    low: lows,
    close: prices,
    period: atrPeriod,
  })
  const obv = OBV.calculate({
    close: prices,
    volume: volumes,
  })
  const momentum = mom(prices, momentumPeriod)

  const lastPrice = prices[prices.length - 1]
  const lastSMA = sma[sma.length - 1] || null
  const lastRSI = rsi[rsi.length - 1] || null
  const lastMACD = macd[macd.length - 1] || null
  const lastBollinger = bollingerBands[bollingerBands.length - 1] || null
  const lastStochasticRsi = stochasticRsi[stochasticRsi.length - 1] || null
  const lastADX = adx[adx.length - 1] || null
  const lastCCI = cci[cci.length - 1] || null
  const lastATR = atr[atr.length - 1] || null
  const lastOBV = obv[obv.length - 1] || null
  const lastMomentum = momentum[momentum.length - 1] || null

  // Определение тренда
  let trend: "Bullish" | "Bearish" | "Neutral" = "Neutral"
  if (lastRSI !== null && lastMACD?.histogram !== undefined) {
    if (lastRSI > 50 && lastMACD.histogram > 0) {
      trend = "Bullish"
    } else if (lastRSI < 50 && lastMACD.histogram < 0) {
      trend = "Bearish"
    }
  }

  return {
    lastPrice,
    sma: lastSMA,
    rsi: lastRSI,
    stochasticRsi: lastStochasticRsi,
    adx: lastADX,
    macd: lastMACD,
    bollingerBands: lastBollinger,
    cci: lastCCI,
    atr: lastATR,
    obv: lastOBV,
    momentum: lastMomentum,
    trend,
  }
}

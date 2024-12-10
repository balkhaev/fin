import { StochasticRSIOutput } from "technicalindicators/declarations/momentum/StochasticRSI"
import { MACDOutput } from "technicalindicators/declarations/moving_averages/MACD"
import { BollingerBandsOutput } from "technicalindicators/declarations/volatility/BollingerBands"
import { ADXOutput } from "technicalindicators/declarations/directionalmovement/ADX"
import { KlineIntervalV3 } from "bybit-api"

export type Signal = -1 | 0 | 1

export type Intervals = KlineIntervalV3

export type Candle = {
  start?: number // Время начала интервала (в миллисекундах с 1970 года)
  end?: number // Время окончания интервала (в миллисекундах с 1970 года)
  interval?: number // Интервал свечи (например, '5' для 5 минут)
  open?: number // Цена открытия (в строковом формате)
  close: number // Цена закрытия (в строковом формате)
  high: number // Максимальная цена за интервал (в строковом формате)
  low: number // Минимальная цена за интервал (в строковом формате)
  volume: number // Объём торгов за интервал (в строковом формате)
  turnover?: number // Оборот в базовой валюте за интервал (в строковом формате)
  confirm?: boolean // Указывает, подтверждена ли свеча
  time?: number // Временная метка сообщения (в миллисекундах с 1970 года)
}
export type Ticker = {
  symbol: string
  lastPrice: number
  change24h: number
  volume24h: number
  openInterest: number
}
export type Analyze = {
  symbol?: string
  lastPrice: number
  sma: number | null
  rsi: number | null
  stochasticRsi: StochasticRSIOutput | null
  adx: ADXOutput | null
  macd: MACDOutput | null
  bollingerBands: BollingerBandsOutput | null
  cci: number | null
  atr: number | null
  obv: number | null
  ema: number | null
  momentum: number | null
  trend: "Bullish" | "Bearish" | "Neutral"
  openInterest?: number
  change24h?: number
  volume24h?: number
}

/**
 * Технические типы
 */

export type RecursiveRequired<T> = Required<{
  [P in keyof T]: T[P] extends object | undefined
    ? RecursiveRequired<Required<T[P]>>
    : T[P]
}>

export type SnakeCase<S extends string> = S extends `${infer T}${infer U}`
  ? T extends Lowercase<T>
    ? `${T}${SnakeCase<U>}`
    : `_${Lowercase<T>}${SnakeCase<U>}`
  : S

export type SnakeCasedKeys<T> = {
  [K in keyof T as K extends string ? SnakeCase<K> : K]: T[K]
}

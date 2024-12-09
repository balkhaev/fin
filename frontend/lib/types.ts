export type Candlestick = {
  start?: number // Время начала интервала (в миллисекундах с 1970 года)
  end?: number // Время окончания интервала (в миллисекундах с 1970 года)
  interval?: number // Интервал свечи (например, '5' для 5 минут)
  open: number // Цена открытия (в строковом формате)
  close: number // Цена закрытия (в строковом формате)
  high: number // Максимальная цена за интервал (в строковом формате)
  low: number // Минимальная цена за интервал (в строковом формате)
  volume: number // Объём торгов за интервал (в строковом формате)
  turnover?: number // Оборот в базовой валюте за интервал (в строковом формате)
  confirm?: boolean // Указывает, подтверждена ли свеча
  time?: number // Временная метка сообщения (в миллисекундах с 1970 года)
}
export type TransactionType = "buy" | "sell" | "create"

export interface BaseTransaction {
  signature: string
  mint: string
  traderPublicKey: string
  txType: TransactionType
  bondingCurveKey: string
  vTokensInBondingCurve: number
  vSolInBondingCurve: number
  marketCapSol: number
  name: string
}

export type Trend = {
  superTrend: Array<number>
  signals: Array<1 | -1 | 0>
}

export interface BuySellTransaction extends BaseTransaction {
  txType: "buy" | "sell"
  tokenAmount: number
  newTokenBalance: number
  signal: number
  data: string
}

export interface CreateTransaction extends BaseTransaction {
  txType: "create"
  initialBuy: number
  name: string
  symbol: string
  uri: string
}

export type Transaction = BuySellTransaction | CreateTransaction

export type Strategies = "supertrend" | "doubleSupertrend" | "sniper"

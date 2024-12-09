export type TxTypes = "sell" | "buy" | "create"

type TransactionType = "buy" | "sell" | "create"

interface BaseTransaction {
  signature: string // Уникальная подпись транзакции
  mint: string // Уникальный идентификатор токена
  traderPublicKey: string // Публичный ключ трейдера
  txType: TransactionType // Тип транзакции: 'buy', 'sell' или 'create'
  bondingCurveKey: string // Уникальный идентификатор кривой связывания
  vTokensInBondingCurve: number // Количество виртуальных токенов в кривой связывания
  vSolInBondingCurve: number // Количество виртуальной SOL в кривой связывания
  marketCapSol: number // Рыночная капитализация токена в SOL
}

export interface BuySellTransaction extends BaseTransaction {
  txType: "buy" | "sell" // Тип транзакции для покупки или продажи
  tokenAmount: number // Количество токенов, участвующих в транзакции
  newTokenBalance: number // Новый баланс токенов после транзакции
}

export interface CreateTransaction extends BaseTransaction {
  txType: "create" // Тип транзакции для создания токена
  initialBuy: number // Первоначальная покупка токенов
  name: string // Название токена
  symbol: string // Символ токена
  uri: string // URI метаданных токена
}

export type Transaction = BuySellTransaction | CreateTransaction

export type TransactionWithTs = Transaction & { timestamp: number }

export enum SellSignalTypes {
  sell = "sell",
  buy = "buy",
}

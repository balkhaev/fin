/**
 * Состояние приложения
 *
 * Пока что все хранится в памяти
 * Нужно перенести в редис
 */

import { BuySellTransaction, Transaction, TransactionWithTs } from "./types"

const waitSells: string[] = []
const waitBuys: string[] = []
const buyed: string[] = []
const selled: string[] = []

export function clearState() {
  waitSells.length = 0
  waitBuys.length = 0
  buyed.length = 0
  selled.length = 0
}

export function isHaveProfit() {
  return selled.length > 0 || buyed.length > 0
}

export function isRunning() {
  return waitBuys.length > 0 || waitSells.length > 0 || buyed.length > 0
}

export function addWaitBuy(mint: string) {
  waitBuys.push(mint)
}

export function removeWaitBuy(mint: string) {
  const i = waitBuys.indexOf(mint)
  if (i !== -1) waitBuys.splice(i, 1)
}

export function addBuyed(mint: string) {
  buyed.push(mint)
}

export function isBuyed(mint: string) {
  return buyed.includes(mint)
}

export function addWaitSell(mint: string) {
  waitSells.push(mint)
  return waitSells.length - 1
}

export function isWaitSell(mint: string) {
  return waitSells.indexOf(mint) !== -1
}

export function removeWaitSell(index: number) {
  waitSells.splice(index, 1)
}

export function addSelled(mint: string) {
  selled.push(mint)
}

/**
 * Состояние предыдущий транзакций
 */
const prevTxs: Record<string, TransactionWithTs[]> = {}

export function addPrevTx(mint: string, tx: TransactionWithTs) {
  if (!prevTxs[mint]) prevTxs[mint] = []

  prevTxs[mint].push(tx)
}

export function coinHaveHistory(mint: string) {
  return mint in prevTxs && prevTxs[mint].length > 0
}

export function getCoinPrevTxs(mint: string) {
  return prevTxs[mint] ?? []
}

export function txToMsTx(tx: Transaction) {
  return {
    ...tx,
    timestamp: Date.now(),
  }
}

/**
 * Состояние активных сделок
 */

const activeDeals: string[] = []

export function addActiveDeal(mint: string) {
  activeDeals.push(mint)
}

export function clearActiveDeals() {
  activeDeals.length = 0
}

export function isActive() {
  return activeDeals.length > 0
}

export function thisMintIsActive(mint: string) {
  return activeDeals.includes(mint)
}

/**
 * Состояние купленного маркет капа
 */
let buyedTx: BuySellTransaction | null = null

export function setBuyedTx(value: BuySellTransaction | null) {
  buyedTx = value
}

export function getBuyedTx() {
  return buyedTx
}

export function clearBuyedTx() {
  buyedTx = null
}

/**
 * Сохраняем все времена создания монет
 */
const coinCreationTimings: Record<string, number> = {}

export function setCoinCreatingTime(mint: string, time: number) {
  coinCreationTimings[mint] = time
}

export function getCoinCreatingTime(mint: string) {
  return coinCreationTimings[mint]
}

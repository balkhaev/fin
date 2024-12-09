import { transactionsToCandles } from "../../utils/candles"
import { io } from "../../server"
import { CreateTransaction, TransactionWithTs } from "./types"
import { doubleSupertrendStrategy } from "../../strategies/double-supertrend"
import { supertrend } from "../../strategies/indicators/supertrend"
import {
  getBuyedTx,
  getCoinCreatingTime,
  getCoinPrevTxs as getPrevTx,
  coinHaveHistory,
} from "./state"
import { RecursiveRequired } from "../../types"
import { pumpFunEvents } from "./listener"

export const MIN_MARKET_CAP_LIMIT_IN_SOL = 50
export const MAX_MARKET_CAP_LIMIT_IN_SOL = 150

let saferTimeout: NodeJS.Timeout

export type TrendConfig = {
  period?: number
  multiplier?: number
}

export type StrategyOpts = {
  mode?: "test" | "prod"
  strategy?: "doubleSupertrend" | "supertrend" | "sniper"
  takeProfit?: number
  stopLoss?: number
  candlesMs?: number
  maxCoinTime?: number
  global?: TrendConfig
  realtime?: TrendConfig
  fallback?: {
    periodSell?: number
    multiplierSell?: number
    periodBuy?: number
    multiplierBuy?: number
    minCandlesForDouble?: number
  }
}

const DEFAULT_OPTS: Required<StrategyOpts> = {
  mode: "test",
  strategy: "doubleSupertrend",
  takeProfit: 1.2,
  stopLoss: 0.1,
  candlesMs: 1000,
  maxCoinTime: 2000,
  global: { period: 11, multiplier: 4 },
  realtime: { period: 4, multiplier: 2 },
  fallback: {
    periodSell: 5,
    multiplierSell: 3,
    periodBuy: 3,
    multiplierBuy: 3,
    minCandlesForDouble: 10,
  },
}

/**
 * Функция оценки транзакции
 *
 * Отдаем сигнал -1 если продажа, 0 если холдим, 1 если продаем
 * Вместе с ним комментарий в data чтобы ориентироваться что сработало
 *
 * @param {object} tx - Транзакция со временем
 * @param {object} coin - Монета
 * @param {object} opts - Опции
 * @returns {object} - { signal: number, data: string }
 */
export function rateTx(
  tx: TransactionWithTs,
  coin: CreateTransaction,
  opts: StrategyOpts = {}
) {
  const {
    strategy,
    takeProfit,
    stopLoss,
    candlesMs,
    maxCoinTime,
    global,
    realtime,
    fallback,
  } = {
    ...DEFAULT_OPTS,
    ...opts,
    global: { ...DEFAULT_OPTS.global, ...opts.global },
    realtime: { ...DEFAULT_OPTS.realtime, ...opts.realtime },
    fallback: { ...DEFAULT_OPTS.fallback, ...opts.fallback },
  } as RecursiveRequired<StrategyOpts>

  // Игнорируем монеты с "ai" в названии
  if (coin.name.toLowerCase().includes("ai"))
    return { signal: 0, data: "[ignore] AI" }

  if (tx.txType === "create") return { signal: 0, data: "[create tx]" }

  /**
   * Take Profit (сейчас работает всегда)
   *
   * Смотрим маркет кап на момент покупки (не нашей транзакции, а на которую срегировали)
   * Сравниваем с маркет капом текущей транзакции
   */
  const currentMarketCap = tx.marketCapSol
  const buyedTx = getBuyedTx()

  if (buyedTx && currentMarketCap > buyedTx.marketCapSol * takeProfit) {
    return {
      signal: -1,
      data: `[take profit] ${(takeProfit - 1) * 100}%`,
    }
  }

  /**
   * Stop Loss (сейчас работает всегда)
   *
   * Смотрим маркет кап на момент покупки (не нашей транзакции, а на которую срегировали)
   * Сравниваем с маркет капом текущей транзакции
   */
  if (buyedTx && currentMarketCap < buyedTx.marketCapSol * (1 - stopLoss)) {
    return {
      signal: -1,
      data: `[stop loss] ${(stopLoss - 1) * 100}%`,
    }
  }

  /**
   * Не залетаем в монеты с большим маркет капом
   */
  if (!buyedTx && MAX_MARKET_CAP_LIMIT_IN_SOL > tx.marketCapSol) {
    return {
      signal: -1,
      data: `[market cap limit] ${MAX_MARKET_CAP_LIMIT_IN_SOL} SOL`,
    }
  }

  /**
   * Генерация свечей
   *
   * Собираем транзакции и делаем из них свечи по указанному таймингу
   * Желательно использовать свечи по 1 секунде
   * Если купили монету, то отправляем по ws на фронт
   */
  const prevTxs = getPrevTx(tx.mint)
  const candles = transactionsToCandles([...prevTxs, tx], candlesMs)

  if (buyedTx?.mint === tx.mint) {
    clearTimeout(saferTimeout)
    saferTimeout = setTimeout(() => {
      pumpFunEvents.emit("sell", { coin, tx })
      pumpFunEvents.emit("tx", {
        coin,
        tx,
        result: {
          signal: -1,
          data: "timeout safer sell",
        },
      })
    }, 5000)

    io.emit("candles", {
      mint: coin.mint,
      candles: candles.slice(-100),
    })
  }

  /**
   * Sniper strategy
   *
   * Требует хорошей комиссии за транзакцию!
   * Высокорисковый режим - залетаем на супертренд со средним фактором
   * Выходим если мало свечей по супертренду
   * Если много свечей, то doubleSupertrend
   */
  const coinCreationTime = getCoinCreatingTime(coin.mint)

  if (strategy === "sniper") {
    // Снайперский режим
    if (
      !coinHaveHistory(coin.mint) &&
      Date.now() > coinCreationTime + maxCoinTime
    ) {
      return {
        signal: 0,
        data: `sinper skip time ${
          Date.now() - (coinCreationTime + maxCoinTime)
        }`,
      }
    }

    if (candles.length > fallback.minCandlesForDouble) {
      // Достаточно свечей для doubleSupertrend
      const result = doubleSupertrendStrategy(candles, {
        global,
        realtime,
      })

      return { signal: result.signal, data: "[double supertrend] sniper" }
    }

    // Мало свечей, fallback на простой supertrend
    let period = fallback.periodBuy
    let multiplier = fallback.multiplierBuy

    if (tx.txType === "sell") {
      // Если это sell-транзакция, используем параметры для sell
      period = candles.length > 7 ? 8 : fallback.periodSell
      multiplier = candles.length > 7 ? 3 : fallback.multiplierSell
    }

    const result = supertrend({
      initialArray: candles,
      period,
      multiplier,
    })

    return {
      signal: result.signal,
      data: `[supertrend] sniper fallback`,
    }
  }

  /**
   * Не залетаем в небольшие монеты если они уже не мониторятся
   */
  if (
    !coinHaveHistory(coin.mint) &&
    MIN_MARKET_CAP_LIMIT_IN_SOL > tx.marketCapSol
  ) {
    return {
      signal: 0,
      data: `[ignore] market cap ${tx.marketCapSol} < ${MIN_MARKET_CAP_LIMIT_IN_SOL}`,
    }
  }

  /**
   * Supertrend strategy
   *
   * Вход по любому супертренду чтобы быстро протестить сигналы
   */
  if (strategy === "supertrend") {
    const result = supertrend({
      initialArray: candles,
      period: 4,
      multiplier: 3,
    })

    return {
      signal: result.signal,
      data: `[supertrend] strategy signal`,
    }
  }

  /**
   * Double supertrend strategy
   * Дефолтный режим
   *
   * Заходим по сильному двойному супертренду
   * Выходим по обычному супертренду
   */
  // if (tx.txType === "sell") {
  //   const result = supertrend({
  //     initialArray: candles,
  //     period: 8,
  //     multiplier: 4,
  //   })

  //   // Сигнал на продажу только если mode допускает продажи
  //   if (result.signal === -1) {
  //     return {
  //       signal: result.signal,
  //       data: `[supertrend] sell signal`,
  //     }
  //   }
  // }

  // В остальных случаях doubleSupertrend
  const dtResult = doubleSupertrendStrategy(candles, {
    global,
    realtime,
  })

  return {
    signal: dtResult.signal,
    data: "[double supertrend] strategy signal",
  }
}

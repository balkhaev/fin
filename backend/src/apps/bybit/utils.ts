import { Candle } from "../../types"

// Функция для преобразования массива транзакций в свечи
export function transactionsToCandles(
  transactions: TransactionWithTs[],
  candleIntervalMs: number = 60000 // 1 минута
): Candle[] {
  if (transactions.length === 0) return []

  const sortedTx = [...transactions].sort((a, b) => a.timestamp - b.timestamp)
  const candles: Candle[] = []

  let currentCandleStart =
    Math.floor(sortedTx[0].timestamp / candleIntervalMs) * candleIntervalMs
  let open = 0
  let high = -Infinity
  let low = Infinity
  let close = 0
  let volume = 0
  let isFirstTxForCandle = true

  for (const tx of sortedTx) {
    const candleStartTime =
      Math.floor(tx.timestamp / candleIntervalMs) * candleIntervalMs
    if (candleStartTime !== currentCandleStart) {
      // Закрываем предыдущую свечу
      if (!isFirstTxForCandle) {
        candles.push({
          time: currentCandleStart,
          open,
          close,
          high,
          low,
          volume,
        })
      }

      // Начинаем новую свечу
      currentCandleStart = candleStartTime
      open = tx.marketCapSol
      high = tx.marketCapSol
      low = tx.marketCapSol
      close = tx.marketCapSol
      volume = "tokenAmount" in tx ? tx.tokenAmount : 0
      isFirstTxForCandle = false
    } else {
      // В том же интервале
      if (isFirstTxForCandle) {
        open = tx.marketCapSol
        high = tx.marketCapSol
        low = tx.marketCapSol
        close = tx.marketCapSol
        volume = "tokenAmount" in tx ? tx.tokenAmount : 0
        isFirstTxForCandle = false
      } else {
        if (tx.marketCapSol > high) high = tx.marketCapSol
        if (tx.marketCapSol < low) low = tx.marketCapSol
        close = tx.marketCapSol

        if ("tokenAmount" in tx) {
          volume += tx.tokenAmount
        }
      }
    }
  }

  // Добавляем последнюю свечу, если она была
  if (!isFirstTxForCandle) {
    candles.push({
      time: currentCandleStart,
      open,
      close,
      high,
      low,
      volume,
    })
  }

  return candles
}

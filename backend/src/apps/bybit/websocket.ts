import { bybitWsClient } from "./sdk/clients"
import {
  KlineIntervalV3,
  PublicTradeV5,
  WS_KEY_MAP,
  isWsOrderbookEventV5,
} from "bybit-api"
import { io } from "../../server"
import { getTechnicalAnalyze } from "../blackbox/indicators"
import { Candle } from "../../types"
import { bybitEvents } from "./events"
import { analyzeCandles } from "../blackbox"
import { getSupertrendSignal } from "../blackbox/signals/supertrend"
import { createOrder } from "./sdk/methods"

const getSubscriptions = (symbol: string, exclude = false) =>
  [
    // "orderbook.50." + symbol,
    "tickers." + symbol,
    "kline.1." + symbol,
    "kline.3." + symbol,
    "kline.15." + symbol,
    "publicTrade." + symbol,
    // "kline.30." + symbol,
    // "kline.60." + symbol,
    // "kline.240." + symbol,
    // "kline.720." + symbol,
    // "kline.D." + symbol,
    // "kline.W." + symbol,
    // "kline.M." + symbol,
  ].filter((el) => {
    const result = bybitWsClient
      .getWsStore()
      .getTopics(WS_KEY_MAP.v5LinearPublic)
      .has(el)
    return !exclude ? !result : result
  })

type BybitWS = {
  topic: string
  data: any
  type: string
}

interface Trade {
  time: number // Unix timestamp в секундах
  price: number
  volume: number
}

type AllowedIntervals = "1" | "3" | "15"

const klines: Pick<Record<KlineIntervalV3, Candle[]>, AllowedIntervals> = {
  "1": [],
  "3": [],
  "15": [],
}

const symbolTrades: Record<string, Trade[]> = {}

const addKlines = (timeframe: AllowedIntervals, newLines: any) => {
  klines[timeframe] = klines[timeframe].concat(newLines)
}

function generateOneSecondCandles(trades: Trade[]): Candle[] {
  if (trades.length === 0) {
    return []
  }

  // Убедимся, что трейды отсортированы по времени
  trades.sort((a, b) => a.time - b.time)

  const startTime = trades[0].time
  const endTime = trades[trades.length - 1].time

  const candles: Candle[] = []
  let previousCandle: Candle | null = null

  // Индекс для прохода по трейдам
  let tradeIndex = 0

  for (let currentTime = startTime; currentTime <= endTime; currentTime++) {
    // Собираем все трейды за текущую секунду
    const tradesThisSecond: Trade[] = []

    while (
      tradeIndex < trades.length &&
      trades[tradeIndex].time === currentTime
    ) {
      tradesThisSecond.push(trades[tradeIndex])
      tradeIndex++
    }

    let candle: Candle

    if (tradesThisSecond.length > 0) {
      // Есть сделки в эту секунду
      const prices = tradesThisSecond.map((t) => t.price)
      const volumes = tradesThisSecond.map((t) => t.volume)

      const open = prices[0]
      const close = prices[prices.length - 1]
      const high = Math.max(...prices)
      const low = Math.min(...prices)
      const volume = volumes.reduce((acc, v) => acc + v, 0)

      candle = {
        time: currentTime,
        open,
        high,
        low,
        close,
        volume,
      }
    } else {
      // Нет сделок в эту секунду, копируем предыдущую свечу
      if (previousCandle) {
        candle = {
          time: currentTime,
          open: previousCandle.open,
          high: previousCandle.high,
          low: previousCandle.low,
          close: previousCandle.close,
          volume: 0, // объём нулевой, так как сделок нет
        }
      } else {
        // Если нет предыдущей свечи (например, первая секунда без сделок),
        // то можно установить свечу равной нулю или пропустить.
        // В данном случае просто пропустим.
        continue
      }
    }

    candles.push(candle)
    previousCandle = candle
  }

  return candles
}

bybitWsClient.on("update", async (data: BybitWS) => {
  if (data.topic?.startsWith("tickers")) {
    io.emit("ticker", JSON.stringify(data.data))
  }

  if (isWsOrderbookEventV5(data)) {
    io.emit("orderbook", JSON.stringify(data.data))
  }

  if (data.topic?.startsWith("kline")) {
    const timeframe = data.topic.split(".")[1] as AllowedIntervals
    const symbolKlines = klines[timeframe]

    addKlines(
      timeframe as AllowedIntervals,
      data.data.map((kline: any) => ({
        open: parseFloat(kline.open),
        close: parseFloat(kline.close),
        high: parseFloat(kline.high),
        low: parseFloat(kline.low),
        volume: parseFloat(kline.volume),
        turnover: parseFloat(kline.turnover),
      })) as Candle[]
    )

    io.emit("ta" + timeframe, JSON.stringify(getTechnicalAnalyze(symbolKlines)))
  }

  if (data.topic.includes("publicTrade.") && data.data) {
    const symbol = data.topic.split(".")[1]

    const trades: Trade[] = data.data.map((trade: any) => ({
      time: Math.floor(trade.T / 1000),
      price: parseFloat(trade.p),
      volume: parseFloat(trade.v),
    }))

    symbolTrades[symbol] = [...(symbolTrades[symbol] || []), ...trades]

    // console.log(data.data)

    // Каждую минуту опрашивать ai на сигнал от него
  }
})

let intervalId2: NodeJS.Timeout | null = null
export function listenBybit(symbol: string) {
  bybitWsClient.subscribeV5(getSubscriptions(symbol), "linear")

  intervalId2 = setInterval(() => {
    if (!symbolTrades[symbol]) return
    const candles = generateOneSecondCandles(symbolTrades[symbol])
    const supertrend = getSupertrendSignal(candles, 10, 5)

    io.emit("candles", candles.slice(-50), supertrend)
  }, 1000)
}

export function unlistenBybit(symbol: string) {
  bybitWsClient.unsubscribeV5(getSubscriptions(symbol, true), "linear")
  if (intervalId2) {
    clearInterval(intervalId2)
    intervalId2 = null
  }
}

let firstBuy = true
let buyed = false
let intervalId: NodeJS.Timeout | null = null

export function startBot(symbol: string) {
  console.log("!START BOT!")
  intervalId = setInterval(() => {
    if (!symbolTrades[symbol]) return

    const candles = generateOneSecondCandles(symbolTrades[symbol])

    if (candles.length < 50) {
      console.log(
        `candles: ${candles.length}, trades: ${symbolTrades[symbol].length}`
      )

      return
    }

    const supertrend = getSupertrendSignal(candles, 50, 5)

    console.log(supertrend)

    if (supertrend === 1 && !buyed) {
      if (firstBuy) {
        firstBuy = false
        return
      }
      buyed = true

      console.log("BUY")
      createOrder({
        symbol,
        side: "Buy",
        orderType: "Market",
        qty: "5",
      })
    }

    if (supertrend === -1 && buyed) {
      buyed = false
      console.log("SELL")
      createOrder({
        symbol,
        side: "Sell",
        orderType: "Market",
        qty: "5",
      })
    }
  }, 500)
}

export function stopBot() {
  if (intervalId) {
    clearInterval(intervalId)
    intervalId = null
  }
}

export function botWorking() {
  return intervalId !== null
}

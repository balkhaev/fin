import { bybitWsClient } from "./sdk/clients"
import { WS_KEY_MAP, isWsOrderbookEventV5 } from "bybit-api"
import { io } from "../../server"
import { getTechnicalAnalyze } from "../analyzer/indicators"
import { Candle } from "../../types"

const getSubscriptions = (symbol: string, exclude = false) =>
  [
    "orderbook.50." + symbol,
    "tickers." + symbol,
    "kline.1." + symbol,
    "kline.5." + symbol,
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
    return !exclude ? result : !result
  })

type bybitWS = {
  topic: string
  data: any
  type: string
}

let klines: any[] = []
const addKlines = (newLines: any) =>
  (klines = [...klines, ...newLines].slice(-50))

bybitWsClient.on("update", (data: bybitWS) => {
  if (data.topic?.startsWith("tickers")) {
    io.emit("ticker", JSON.stringify(data.data))
  }

  if (isWsOrderbookEventV5(data)) {
    io.emit("orderbook", JSON.stringify(data.data))
  }

  if (data.topic?.startsWith("kline")) {
    const timeframe = data.topic.split(".")[1]

    addKlines(
      data.data.map((kline: any) => ({
        open: parseFloat(kline.open),
        close: parseFloat(kline.close),
        high: parseFloat(kline.high),
        low: parseFloat(kline.low),
        volume: parseFloat(kline.volume),
        turnover: parseFloat(kline.turnover),
      })) as Candle[]
    )

    io.emit("ta" + timeframe, JSON.stringify(getTechnicalAnalyze(klines)))
  }
})

export function listenBybit(symbol: string) {
  bybitWsClient.subscribeV5(getSubscriptions(symbol), "linear")
}

export function unlistenBybit(symbol: string) {
  bybitWsClient.unsubscribeV5(getSubscriptions(symbol, true), "linear")
}

import { GetKlineParamsV5 } from "bybit-api"
import { bybitRestClient } from "./clients"
import { Candle, Ticker } from "../../../types"
import { klineAdapter, tickerAdapter } from "./adapters"

export async function fetchTickers(): Promise<Ticker[]> {
  const { result } = await bybitRestClient.getTickers({
    category: "linear",
    baseCoin: process.env.BASE_CURRENCY,
  })

  return result.list.map(tickerAdapter)
}

export async function fetchKline({
  symbol = "BTCUSDT",
  interval = "15",
}: Partial<GetKlineParamsV5>): Promise<Candle[]> {
  const res = await bybitRestClient.getKline({
    symbol,
    category: "linear",
    interval,
    limit: 50, // Количество свечей (ограничено API)
  })

  return res.result.list.map(klineAdapter)
}

export async function createOrder() {
  return bybitRestClient.submitOrder({
    symbol: "BTCUSDT",
    side: "Buy",
    qty: "0.01",
    orderType: "Market",
    category: "linear",
  })
}

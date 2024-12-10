import Queue from "bull"
import { getTechnicalAnalyze } from "../../blackbox/indicators"
import { supabase } from "../../../lib/supabase"
import snakecaseKeys from "snakecase-keys"
import { io } from "../../../server"
import { bybitRestClient } from "../sdk/clients"
import { tickerAdapter } from "../sdk/adapters"
import { fetchKline } from "../sdk/methods"
import { analyzeCandles } from "../../blackbox"
import { KlineIntervalV3 } from "bybit-api"

export const analyzeSymbolQueue = new Queue<{ symbol: string }>("bybit-analyze")

const CANDLES_TO_FETCH: KlineIntervalV3[] = ["1", "15", "30", "60"]

analyzeSymbolQueue.process(3, async (job) => {
  const [candles1, candles15, candles30, candles60] = await Promise.all(
    CANDLES_TO_FETCH.map((interval) =>
      fetchKline({ symbol: job.data.symbol, interval })
    )
  )

  const { result: tickers } = await bybitRestClient.getTickers({
    category: "linear",
    symbol: job.data.symbol,
  })

  const ticker = tickerAdapter(tickers.list[0])
  const analysis = getTechnicalAnalyze(candles15)
  const rating = analyzeCandles({
    candles: {
      1: candles1,
      15: candles15,
      30: candles30,
      60: candles60,
    },
    analysis,
    ticker,
  })

  return {
    ...analysis,
    ...rating,
    volume24h: parseInt(tickers.list[0].volume24h),
    openInterest: parseInt(tickers.list[0].openInterest),
    change24h: parseFloat(tickers.list[0].price24hPcnt),
    symbol: job.data.symbol,
  }
})

analyzeSymbolQueue.on("completed", async (job) => {
  const { error } = await supabase
    .from("analysis")
    .insert(snakecaseKeys(job.returnvalue, { deep: false }))

  if (error) {
    console.error(job, error)
    throw error
  }

  io.emit("job-count", await analyzeSymbolQueue.count())

  if (job.returnvalue.rating > 0) io.emit("signal", job.returnvalue)
})

analyzeSymbolQueue.on("waiting", async () => {
  io.emit("job-count", await analyzeSymbolQueue.count())
})

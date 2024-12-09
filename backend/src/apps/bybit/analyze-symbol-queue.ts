import Queue from "bull"
import { fetchKline } from "./sdk/methods"
import { getSymbolTA } from "./technical-indicators"
import { supabase } from "../../sdk/supabase"
import snakecaseKeys from "snakecase-keys"
import { calculateRating } from "./calculate-rating"
import { io } from "../../server"
import { bybitRestClient } from "./sdk/clients"
import { tickerAdapter } from "./sdk/adapters"

export const analyzeSymbolQueue = new Queue<{ symbol: string }>(
  "analyze symbol"
)

analyzeSymbolQueue.process(3, async (job) => {
  const historicalData = await fetchKline({ symbol: job.data.symbol })
  const { result: tickers } = await bybitRestClient.getTickers({
    category: "linear",
    symbol: job.data.symbol,
  })
  const analysis = getSymbolTA(historicalData)
  const rating = calculateRating(
    historicalData,
    analysis,
    tickerAdapter(tickers.list[0])
  )

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
  io.emit("completed-job", JSON.stringify(job))

  const { error } = await supabase
    .from("analysis")
    .insert(snakecaseKeys(job.returnvalue, { deep: false }))

  if (error) {
    console.error(job, error)
    throw error
  }
})

analyzeSymbolQueue.on("waiting", (job) => {
  io.emit("waiting-job", JSON.stringify(job))
})

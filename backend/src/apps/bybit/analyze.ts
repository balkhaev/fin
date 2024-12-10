import { analyzeSymbolQueue } from "./queue/analyze-symbol"
import { fetchTickers } from "./sdk/methods"

export async function getTrendTickers() {
  const tickers = await fetchTickers()

  const trending = tickers.filter(
    (ticker) => ticker.volume24h > 1_000_000 && ticker.lastPrice > 0.05
  )

  return {
    all: tickers,
    trending,
  }
}

export default async function analyzeBybit() {
  const tickers = await getTrendTickers()

  analyzeSymbolQueue.addBulk(
    tickers.trending.map((ticker) => ({
      data: ticker,
    }))
  )

  return { tickers }
}

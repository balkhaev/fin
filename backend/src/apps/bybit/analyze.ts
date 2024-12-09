import { analyzeSymbolQueue } from "./queue/analyze-symbol"
import { fetchTickers } from "./sdk/methods"

export async function getTrendTickers() {
  const tickers = await fetchTickers()

  const trending = tickers.filter(
    (ticker) => ticker.volume24h > 1000000 && ticker.lastPrice > 0.05
  )

  return {
    all: tickers,
    trending,
  }
}

export default async function analyzeBybit() {
  const tickers = await getTrendTickers()

  tickers.trending.forEach((ticker) => {
    analyzeSymbolQueue.add(ticker)
  })

  return { tickers }
}

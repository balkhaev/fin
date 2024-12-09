import { analyzeSymbolQueue } from "./analyze-symbol-queue"
import { fetchTickers } from "./sdk/methods"

export async function analyzeTickers() {
  const tickers = await fetchTickers()

  const trending = tickers.filter(
    (ticker) => ticker.volume24h > 1000000 && ticker.lastPrice > 0.05
  )

  return {
    all: tickers,
    trending,
  }
}

export default async function analyzeExchange() {
  const tickers = await analyzeTickers()

  tickers.trending.forEach((ticker) => {
    analyzeSymbolQueue.add(ticker)
  })

  return { tickers }
}

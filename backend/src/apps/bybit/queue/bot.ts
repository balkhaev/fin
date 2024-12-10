import Queue from "bull"
import { fetchKline } from "../sdk/methods"
import { getSupertrendCrossingSignal } from "../../blackbox/strategies"

export const botQueue = new Queue("bybit-bot")

botQueue.process(async (job) => {
  const { symbol } = job.data

  const candles = await fetchKline({ symbol, interval: "1" })

  const signal = getSupertrendCrossingSignal(candles, [
    { period: 10, multiplier: 1 },
    { period: 11, multiplier: 2 },
    { period: 12, multiplier: 3 },
  ])

  return {
    symbol,
    signal,
  }
})

botQueue.on("completed", (job) => {
  const { symbol, signal } = job.data

  console.log({ symbol, signal })
})

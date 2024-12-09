import { Candlestick } from "@/lib/types"

export const candlesToSeries = (
  candles: Candlestick[]
): ApexAxisChartSeries => [
  {
    data: candles.map((candle) => ({
      x: candle.time,
      y: [candle.open, candle.high, candle.low, candle.close],
    })),
  },
]

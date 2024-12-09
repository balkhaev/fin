import { OHLCVKlineV5, TickerLinearInverseV5 } from "bybit-api"
import { Candlestick, Ticker } from "../../../types"

export const tickerAdapter = (ticker: TickerLinearInverseV5): Ticker => ({
  symbol: ticker.symbol,
  lastPrice: parseFloat(ticker.lastPrice),
  volume24h: parseFloat(ticker.volume24h),
  change24h: parseFloat(ticker.price24hPcnt),
  openInterest: parseFloat(ticker.openInterestValue),
})

export const klineAdapter = (kline: OHLCVKlineV5): Candlestick => ({
  open: parseFloat(kline[1]),
  high: parseFloat(kline[2]),
  low: parseFloat(kline[3]),
  close: parseFloat(kline[4]),
  volume: parseFloat(kline[5]),
})

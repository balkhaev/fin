"use client"

import { AppChart } from "@/components/app/apex-charts"
import { Candlestick } from "@/lib/types"
import { candlesToSeries } from "@/lib/candles-to-series"

type ChartTabsProps = {
  title: string
  candlesData: Record<string, Candlestick[]>
  selectedCoin: string | null
}

export function ChartTabs({
  title,
  candlesData,
  selectedCoin,
}: ChartTabsProps) {
  const selectedCandles =
    selectedCoin && candlesData[selectedCoin] ? candlesData[selectedCoin] : []

  return (
    <AppChart
      options={{
        chart: {
          type: "candlestick",
          height: 350,
        },
        title: {
          text: title,
          align: "left",
        },
        xaxis: {
          type: "datetime",
        },
        yaxis: {
          tooltip: {
            enabled: true,
          },
        },
        plotOptions: {
          candlestick: {
            colors: {
              upward: "#3C90EB",
              downward: "#DF7D46",
            },
          },
        },
      }}
      type="candlestick"
      series={candlesToSeries(selectedCandles)}
      width="100%"
      height={600}
    />
  )
}

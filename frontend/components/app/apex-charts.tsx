"use client" // if you use app dir, don't forget this line

import { ApexOptions } from "apexcharts"
import dynamic from "next/dynamic"
const ApexChart = dynamic(() => import("react-apexcharts"), { ssr: false })

type Props = {
  options: ApexOptions
  series: ApexAxisChartSeries
  height?: number | string
  width?: number | string
  type:
    | "line"
    | "area"
    | "bar"
    | "pie"
    | "donut"
    | "radialBar"
    | "scatter"
    | "bubble"
    | "heatmap"
    | "candlestick"
    | "boxPlot"
    | "radar"
    | "polarArea"
    | "rangeBar"
    | "rangeArea"
    | "treemap"
}

export function AppChart({ options, series, height, width, type }: Props) {
  return (
    <>
      <ApexChart
        type={type}
        options={options}
        series={series}
        height={height}
        width={width}
      />
    </>
  )
}

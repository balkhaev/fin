import { AppChart } from "@/components/app/apex-charts"

type Props = {
  series: ApexAxisChartSeries
}

export function CandleChart({ series }: Props) {
  return (
    <AppChart
      options={{
        chart: {
          type: "candlestick",
          height: 350,
        },
        title: {
          text: "CandleStick Chart",
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
      }}
      type="candlestick"
      series={series}
      width="100%"
      height={500}
    />
  )
}

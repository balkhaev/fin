"use client"

import { ColumnDef } from "@tanstack/react-table"
import Link from "next/link"
import { AppTable } from "@/lib/helpers"
import { Button } from "@/components/ui/button"
import dayjs from "@/utils/date"

const formatCell = (
  value: number | null,
  trend?: number,
  additinal?: string | number
) => {
  if (value === null) {
    return "N/A"
  }

  const className =
    typeof trend === "number"
      ? trend === 1
        ? "text-green-500"
        : trend === -1
        ? "text-red-500"
        : ""
      : value > 0
      ? "text-green-500"
      : "text-red-500"

  return (
    <span className={className}>
      {value?.toFixed(2)}
      {additinal && ` ${additinal}`}
    </span>
  )
}

const SignalCell = ({
  signals,
}: {
  signals: { name: string; signal: number }[]
}) => {
  return (
    <div className="flex flex-col">
      {signals.map(({ name, signal }) => {
        let text = ""
        let color = ""
        switch (signal) {
          case 1:
            text = "Buy"
            color = "text-green-500"
            break
          case -1:
            text = "Sell"
            color = "text-red-500"
            break
          case 0:
          default:
            text = "Hold"
            color = "text-yellow-500"
            break
        }
        return (
          <span key={name} className={color}>
            {name}: {text}
          </span>
        )
      })}
    </div>
  )
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const TechnicalAnalysis = ({ row }: { row: any }) => {
  const rsiK = row.stochasticRsi?.k
  const rsiD = row.stochasticRsi?.d
  const adx = row.adx?.adx
  const pdi = row.adx?.pdi
  const mdi = row.adx?.mdi
  const macd = row.macd?.MACD
  const macdSignal = row.macd?.signal
  const macdHistogram = row.macd?.histogram
  const bbUpper = row.bollingerBands?.upper
  const bbMiddle = row.bollingerBands?.middle
  const bbLower = row.bollingerBands?.lower
  const currentPrice = row.lastPrice

  return (
    <div>
      <div className="text-gray-500">
        <span className="font-semibold">RSI: </span>
        <span
          className={
            rsiK > 80
              ? "text-green-500"
              : rsiK < 20
              ? "text-red-500"
              : "text-gray-500"
          }
        >
          {rsiK?.toFixed(2)} K
        </span>
        {" / "}
        <span
          className={
            rsiD > 80
              ? "text-green-500"
              : rsiD < 20
              ? "text-red-500"
              : "text-gray-500"
          }
        >
          {rsiD?.toFixed(2)} D
        </span>
      </div>
      <div className="text-gray-500">
        <span className="font-semibold">ADX: </span>
        <span
          className={
            adx > 25
              ? "text-green-500"
              : adx >= 20
              ? "text-yellow-500"
              : "text-gray-500"
          }
        >
          {adx?.toFixed(2)}
        </span>
        {" / "}
        <span className={pdi > mdi ? "text-green-500" : "text-gray-500"}>
          {pdi?.toFixed(2)} +DI
        </span>
        {" / "}
        <span className={mdi > pdi ? "text-red-500" : "text-gray-500"}>
          {mdi?.toFixed(2)} -DI
        </span>
      </div>
      <div className="text-gray-500">
        <span className="font-semibold">MACD: </span>
        <span className={macd > macdSignal ? "text-green-500" : "text-red-500"}>
          {macd?.toFixed(2)}
        </span>
        {" / "}
        <span className={macdSignal > macd ? "text-green-500" : "text-red-500"}>
          {macdSignal?.toFixed(2)} Signal
        </span>
        {" / "}
        <span
          className={
            macdHistogram > 0.2
              ? "text-green-500"
              : macdHistogram < -0.2
              ? "text-red-500"
              : "text-gray-500"
          }
        >
          {macdHistogram?.toFixed(2)} Hist
        </span>
      </div>
      <div className="text-gray-500">
        <span className="font-semibold">BB: </span>
        <span
          className={
            currentPrice >= bbUpper ? "text-yellow-500" : "text-gray-500"
          }
        >
          {bbUpper?.toFixed(2)} U
        </span>
        {" / "}
        <span
          className={
            currentPrice === bbMiddle ? "text-yellow-500" : "text-gray-500"
          }
        >
          {bbMiddle?.toFixed(2)} M
        </span>
        {" / "}
        <span
          className={
            currentPrice <= bbLower ? "text-green-500" : "text-gray-500"
          }
        >
          {bbLower?.toFixed(2)} L
        </span>
      </div>
    </div>
  )
}

export const columns: ColumnDef<AppTable<"analysis">>[] = [
  {
    accessorKey: "symbol",
    header: "Symbol",
    cell: ({ getValue }) => (
      <div className="flex items-center gap-2">
        <Link
          href={`https://www.bybit.com/trade/usdt/${getValue<string>()}`}
          target="_blank"
        >
          <Button variant={"outline"} size={"sm"}>
            Bybit
          </Button>
        </Link>
        <Link href={`/symbol/${getValue<string>()}`}>{getValue<string>()}</Link>
      </div>
    ),
  },
  {
    accessorKey: "createdAt",
    header: "Updated At",
    cell: ({ getValue }) => {
      const date = dayjs(new Date(getValue<string>())).add(3, "hours")
      return date ? (
        <div title={date.format("DD.MM.YYYY, HH:mm:ss")}>
          {date.format("HH:mm:ss")}
        </div>
      ) : (
        "N/A"
      )
    },
  },
  {
    accessorKey: "lastPrice",
    header: "Last Price",
    cell: ({ getValue, row }) => {
      if (!row.original.sma) {
        return formatCell(getValue<number>(), 1)
      }

      const ratio = row.original.sma / getValue<number>()
      const diff = row.original.sma - getValue<number>()

      const trend = (() => {
        if (Math.abs(diff) > 0.1) {
          return ratio > 1 ? -1 : 1
        }
        return 0
      })()

      return formatCell(getValue<number>(), trend)
    },
  },
  {
    accessorKey: "sma",
    header: "SMA",
    cell: ({ getValue }) => {
      return (
        <span className="text-gray-700">{getValue<number>()?.toFixed(2)}</span>
      )
    },
  },
  {
    accessorKey: "volume24H",
    header: "Volume 24h",
    cell: ({ getValue }) => (
      <span className="text-gray-700">
        {getValue<number>()?.toLocaleString()} USD
      </span>
    ),
  },
  {
    accessorKey: "openInterest",
    header: "Interest",
    cell: ({ getValue, row }) => {
      // @ts-expect-error asd
      const volume24h = row.original.volume24H
      const openInterest = getValue<number | undefined>()

      if (openInterest === undefined) {
        return "N/A"
      }

      const percentage = openInterest / volume24h

      const isHigh = percentage > 0.2
      const isLow = percentage < 0.05

      const color = isHigh
        ? "text-green-500"
        : isLow
        ? "text-red-500"
        : "text-yellow-500"

      return (
        <span className={color} title={`${openInterest?.toLocaleString()} USD`}>
          {percentage.toFixed(2)}%
        </span>
      )
    },
  },
  {
    accessorKey: "change24H",
    header: "Price %",
    cell: ({ getValue }) => {
      const value = getValue<number | undefined>()
      if (value === undefined) return "N/A"

      const isPositive = value > 0.1
      const isNegative = value < -0.1

      return (
        <span
          className={
            isPositive
              ? "text-green-500"
              : isNegative
              ? "text-red-500"
              : "text-gray-500"
          }
        >
          {value?.toFixed(2)}%
        </span>
      )
    },
  },
  {
    accessorKey: "technicalAnalysis",
    header: "Technical Analysis",
    cell: ({ row }) => <TechnicalAnalysis row={row.original} />,
  },
  {
    accessorKey: "additionalIndicators",
    header: "Indicators",
    cell: ({ row }) => {
      const cci = row.original.cci!
      const atr = row.original.atr!
      const momentum = row.original.momentum!

      return (
        <div>
          <div className="text-gray-500">
            CCI:{" "}
            <span
              className={
                cci > 100
                  ? "text-red-500"
                  : cci < -100
                  ? "text-green-500"
                  : cci < -60
                  ? "text-yellow-500"
                  : "text-gray-500"
              }
            >
              {cci?.toFixed(2)}
            </span>
          </div>
          <div className="text-gray-500">ATR: {atr?.toFixed(2)}</div>
          <div className="text-gray-500">
            MOM:{" "}
            <span
              className={
                momentum > 0
                  ? "text-green-500"
                  : momentum < 0
                  ? "text-red-500"
                  : "text-gray-500"
              }
            >
              {momentum?.toFixed(2)}
            </span>
          </div>
        </div>
      )
    },
  },
  {
    accessorKey: "trend",
    header: "Trend",
    cell: ({ getValue }) => {
      const value = getValue<string>()
      const color =
        value === "Bullish"
          ? "text-green-500"
          : value === "Bearish"
          ? "text-red-500"
          : "text-yellow-500"

      return <span className={color}>{value}</span>
    },
  },
  {
    accessorKey: "rating",
    header: "Rating",
    cell: ({ getValue }) => {
      const value = getValue<number>()
      return (
        <span
          className={
            value > 0
              ? "text-green-500"
              : value < 0
              ? "text-red-500"
              : "text-yellow-500"
          }
        >
          {value.toFixed(2)}
        </span>
      )
    },
  },
  {
    accessorKey: "signals",
    header: "Signals",
    cell: ({ getValue }) => (
      <SignalCell signals={getValue<{ name: string; signal: number }[]>()} />
    ),
  },
]

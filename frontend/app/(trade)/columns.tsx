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

const SignalCell = ({ value }: { value: number }) => {
  let text = ""
  let color = ""
  switch (value) {
    case 1:
      text = "Покупать"
      color = "text-green-500"
      break
    case -1:
      text = "Продавать"
      color = "text-red-500"
      break
    case 0:
    default:
      text = "Держать"
      color = "text-yellow-500"
      break
  }
  return <span className={color}>{text}</span>
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
      const date = getValue<string>()
      return date ? (
        <div title={dayjs(new Date(date)).format("DD.MM.YYYY, HH:mm:ss")}>
          {dayjs(new Date(date)).fromNow()}
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
  // Новый столбец: Open Interest
  {
    accessorKey: "openInterest",
    header: "Interest",
    cell: ({ getValue, row }) => {
      // @ts-expect-error easd
      const volume24h = row.original.volume24H
      const openInterest = getValue<number | undefined>()

      // Проверка на наличие значений
      if (openInterest === undefined) {
        return "N/A" // Защита от деления на 0 или отсутствующих данных
      }

      const percentage = openInterest / volume24h

      // Логика интерпретации открытого интереса
      const isHigh = percentage > 0.2 // Примерный порог для высокого открытого интереса
      const isLow = percentage < 0.05 // Примерный порог для низкого открытого интереса

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
  // Новый столбец: Change 24h
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
    accessorKey: "stochasticRsi",
    header: "RSI",
    cell: ({ getValue }) => {
      const value = getValue<{ k: number; d: number } | null>()
      if (!value) return "N/A"

      const { k, d } = value

      // Интерпретация сигналов:
      // "Хороший положительный тренд" (бычий): когда K > D и K > 80
      // "Отрицательный" (медвежий): когда K < D и K < 20
      const isBullish = k > d && k > 80
      const isBearish = k < d && k < 20

      // Если не бычий и не медвежий, считаем нейтральным.
      // При нейтральном сигнале окраска будет серой.
      const kColor = isBullish
        ? "text-green-500"
        : isBearish
        ? "text-red-500"
        : "text-gray-500"

      const dColor = isBullish
        ? "text-green-500"
        : isBearish
        ? "text-red-500"
        : "text-gray-500"

      return (
        <div>
          <span className={kColor}>K: {k.toFixed(2)}</span>,{" "}
          <span className={dColor}>D: {d.toFixed(2)}</span>
        </div>
      )
    },
  },

  {
    accessorKey: "adx",
    header: "ADX",
    cell: ({ getValue }) => {
      const value = getValue<{ adx: number; pdi: number; mdi: number } | null>()
      if (!value) return "N/A"

      const { adx, pdi, mdi } = value

      // Интерпретация тренда
      const isStrongTrend = adx > 25
      const isModerateTrend = adx >= 20 && adx <= 25
      const isTrend = isStrongTrend || isModerateTrend

      const isBullish = pdi > mdi
      const isBearish = mdi > pdi
      const ratio = mdi / pdi

      // Цветовая индикация для ADX
      const adxColor = isStrongTrend
        ? "text-green-500"
        : isModerateTrend
        ? "text-yellow-500"
        : "text-gray-500"

      // Цветовая индикация для +DI и -DI
      const pdiColor =
        isTrend && isBullish && ratio > 0.2 ? "text-green-500" : "text-gray-500"
      const mdiColor =
        isTrend && isBearish && ratio > 1.1 ? "text-red-500" : "text-gray-500"

      return (
        <div>
          <span className={adxColor}>ADX: {adx?.toFixed(2)}</span>,{" "}
          <span className={pdiColor}>+DI: {pdi?.toFixed(2)}</span>,{" "}
          <span className={mdiColor}>-DI: {mdi?.toFixed(2)}</span>
        </div>
      )
    },
  },
  {
    accessorKey: "macd",
    header: "MACD",
    enableSorting: true,
    sortingFn: (rowA, rowB) => {
      // @ts-expect-error asd
      const macdA = rowA.original.macd?.signal ?? 0
      // @ts-expect-error asd
      const macdB = rowB.original.macd?.signal ?? 0
      return macdA - macdB
    },
    cell: ({ getValue }) => {
      const value = getValue<{
        MACD: number
        signal: number
        histogram: number
      } | null>()
      if (!value) return "N/A"

      const isNeutral = Math.abs(value.histogram) < 0.1
      const isBullish = !isNeutral && value.histogram > 0
      const isBearish = !isNeutral && value.histogram < 0
      const className = isBullish
        ? "text-green-500"
        : isBearish
        ? "text-red-500"
        : "text-gray-500"

      return (
        <>
          <span className={className}>MACD: {value.MACD.toFixed(2)}</span>,{" "}
          <span className={className}>Signal: {value.signal?.toFixed(2)}</span>,{" "}
          <span className={className}>
            Histogram: {value.histogram?.toFixed(2)}
          </span>
        </>
      )
    },
  },
  {
    accessorKey: "bollingerBands",
    header: "BB",
    enableSorting: true,
    sortingFn: (rowA, rowB) => {
      // @ts-expect-error asd
      const upperA = rowA.original.bollingerBands?.upper ?? 0
      // @ts-expect-error asd
      const upperB = rowB.original.bollingerBands?.upper ?? 0
      return upperA - upperB
    },
    cell: ({ row, getValue }) => {
      const value = getValue<{
        upper: number
        lower: number
        middle: number
      } | null>()
      const currentPrice = row.original.lastPrice

      if (!value || !currentPrice) return "N/A"

      const isNearUpper = currentPrice >= value.upper
      const isNearLower = currentPrice <= value.lower

      return (
        <div>
          <span className={isNearUpper ? "text-yellow-500" : "text-gray-500"}>
            Upper: {value.upper.toFixed(2)}
          </span>
          ,{" "}
          <span className={isNearLower ? "text-green-500" : "text-gray-500"}>
            Lower: {value.lower.toFixed(2)}
          </span>
          ,{" "}
          <span
            className={
              currentPrice === value.middle
                ? "text-yellow-500"
                : "text-gray-500"
            }
          >
            Middle: {value.middle.toFixed(2)}
          </span>
        </div>
      )
    },
  },
  {
    accessorKey: "cci",
    header: "CCI",
    cell: ({ getValue }) => {
      const value = getValue<number | null>()
      if (!value) return "N/A"

      const isOverbought = value > 100
      const isOversold = value < -100

      return (
        <span
          className={
            isOverbought
              ? "text-red-500"
              : isOversold
              ? "text-green-500"
              : "text-gray-500"
          }
        >
          {value.toFixed(2)}
        </span>
      )
    },
  },
  {
    accessorKey: "atr",
    header: "ATR",
    cell: ({ getValue }) => (
      <span className="text-gray-700">
        {getValue<number | null>()?.toFixed(2) || "N/A"}
      </span>
    ),
  },
  {
    accessorKey: "momentum",
    header: "MOM",
    cell: ({ getValue }) => {
      const value = getValue<number | null>()
      if (!value) return "N/A"

      const isPositive = value > 0
      const isNegative = value < 0

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
          {value.toFixed(2)}
        </span>
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
    accessorKey: "tripleSupertrend",
    header: "TST",
    cell: ({ getValue }) => <SignalCell value={getValue<number>()} />,
  },
  {
    accessorKey: "doubleSupertrend",
    header: "DST",
    cell: ({ getValue }) => <SignalCell value={getValue<number>()} />,
  },
  {
    accessorKey: "macdSupertrend",
    header: "MST",
    cell: ({ getValue }) => <SignalCell value={getValue<number>()} />,
  },
  {
    accessorKey: "supertrend",
    header: "ST",
    cell: ({ getValue }) => <SignalCell value={getValue<number>()} />,
  },
]

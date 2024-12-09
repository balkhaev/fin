"use client"

import { ColumnDef } from "@tanstack/react-table"
import Link from "next/link"
import { format } from "date-fns"
import { AppTable } from "@/lib/helpers"

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

export const columns: ColumnDef<AppTable<"analysis">>[] = [
  {
    accessorKey: "symbol",
    header: "Symbol",
    cell: ({ getValue }) => (
      <Link href={`/trade/${getValue<string>()}`}>{getValue<string>()}</Link>
    ),
  },
  {
    accessorKey: "createdAt",
    header: "Updated At",
    cell: ({ getValue }) => {
      const date = getValue<string>()
      return date ? format(new Date(date), "dd MMM yyyy, HH:mm:ss") : "N/A"
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
    header: "Open Interest",
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
      const isHigh = percentage > 0.01 // Примерный порог для высокого открытого интереса
      const isLow = percentage < 0.01 // Примерный порог для низкого открытого интереса

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
    header: "Change 24h (%)",
    cell: ({ getValue }) => {
      const value = getValue<number | undefined>()
      if (value === undefined) return "N/A"

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
          {value?.toFixed(2)}%
        </span>
      )
    },
  },
  {
    accessorKey: "stochasticRsi",
    header: "Stochastic RSI",
    cell: ({ getValue }) => {
      const value = getValue<{ k: number; d: number } | null>()
      if (!value) return "N/A"

      const { k, d } = value

      // Интерпретация сигналов
      const isBullish = k > d && k > 80
      const isBearish = k < d && k < 20
      const isNeutral = !isBullish && !isBearish // Если не подходит ни под одну зону, значит нейтрально.

      // Цветовая индикация
      const kColor = isBullish
        ? "text-green-500"
        : isBearish
        ? "text-red-500"
        : isNeutral
        ? "text-yellow-500"
        : "text-gray-500"

      const dColor = isBullish
        ? "text-green-500"
        : isBearish
        ? "text-red-500"
        : isNeutral
        ? "text-yellow-500"
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
        isBullish && ratio > 0.8 ? "text-green-500" : "text-gray-500"
      const mdiColor =
        isBearish && ratio > 1.1 ? "text-red-500" : "text-gray-500"

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

      const isBullish = value.histogram > 0
      const isBearish = value.histogram < 0

      return (
        <>
          <span
            className={
              isBullish
                ? "text-green-500"
                : isBearish
                ? "text-red-500"
                : "text-gray-500"
            }
          >
            MACD: {value.MACD.toFixed(2)}
          </span>
          ,{" "}
          <span
            className={value?.signal > 0 ? "text-green-500" : "text-red-500"}
          >
            Signal: {value.signal?.toFixed(2)}
          </span>
          ,{" "}
          <span
            className={
              isBullish
                ? "text-green-500"
                : isBearish
                ? "text-red-500"
                : "text-gray-500"
            }
          >
            Histogram: {value.histogram?.toFixed(2)}
          </span>
        </>
      )
    },
  },
  {
    accessorKey: "bollingerBands",
    header: "Bollinger Bands",
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
          <span className={isNearUpper ? "text-red-500" : "text-gray-500"}>
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
    accessorKey: "obv",
    header: "OBV",
    cell: ({ getValue }) => {
      const value = getValue<number | null>()
      if (!value) return "N/A"

      const isPositive = value > 0

      return (
        <span className={isPositive ? "text-green-500" : "text-red-500"}>
          {value.toFixed(2)}
        </span>
      )
    },
  },
  {
    accessorKey: "momentum",
    header: "Momentum",
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
    accessorKey: "decision",
    header: "Decision",
    cell: ({ getValue }) => {
      const value = getValue<1 | 0 | -1>()
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
    },
  },
]

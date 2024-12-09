"use client"

import { Button } from "@/components/ui/button"
import { Transaction } from "@/lib/types"
import { ColumnDef } from "@tanstack/react-table"
import Link from "next/link"

// Определение колонок
export const columns: ColumnDef<Transaction & { name: string }>[] = [
  {
    accessorKey: "signature",
    header: "Подпись",
    cell: ({ row }) => (
      <Link
        href={`https://explorer.solana.com/tx/${row.original.signature}`}
        target="_blank"
      >
        {row.getValue<string>("signature")?.slice(0, 10)}
      </Link>
    ),
  },
  {
    accessorKey: "mint",
    header: "Название",
    cell: ({ row }) => (
      <div className="flex items-center gap-2">
        <Link
          href={`https://photon-sol.tinyastro.io/en/lp/${row.original.mint}`}
          target="_blank"
        >
          <Button
            onClick={() =>
              navigator.clipboard.writeText(row.getValue<string>("mint"))
            }
            variant="outline"
            size="sm"
          >
            photon
          </Button>
        </Link>
        <Link
          href={`https://pump.fun/coin/${row.original.mint}`}
          target="_blank"
        >
          <Button
            onClick={() =>
              navigator.clipboard.writeText(row.getValue<string>("mint"))
            }
            variant="outline"
            size="sm"
          >
            pump
          </Button>
        </Link>
        {row.original.name}
      </div>
    ),
  },
  {
    accessorKey: "traderPublicKey",
    header: "Публичный ключ трейдера",
  },
  {
    accessorKey: "marketCapSol",
    header: "Рыночная капитализация (SOL)",
    cell: ({ row }) => {
      const amount = parseFloat(row.getValue("marketCapSol"))
      return (
        <div
          className={
            amount < 50
              ? "text-gray-500"
              : amount > 100
              ? "text-purple-500"
              : "text-orange-500"
          }
        >
          {new Intl.NumberFormat("ru-RU", {
            style: "currency",
            currency: "SOL",
          }).format(amount)}
        </div>
      )
    },
  },
  {
    accessorKey: "txType",
    header: "Тип транзакции",
    cell: ({ row }) => {
      const isBuy = row.original.txType === "buy"
      const isCreate = row.original.txType === "create"
      return (
        <div
          className={
            isBuy
              ? "text-green-500"
              : isCreate
              ? "text-gray-500"
              : "text-red-500"
          }
        >
          {row.getValue("txType")}
        </div>
      )
    },
  },
  {
    accessorKey: "newTokenBalance",
    header: "Новый баланс токенов",
    cell: ({ row }) => {
      const amount = parseFloat(row.getValue("newTokenBalance"))
      return new Intl.NumberFormat("ru-RU").format(amount)
    },
  },
  {
    accessorKey: "tokenAmount",
    header: "Кол-во токенов",
    cell: ({ row }) => {
      const amount = parseFloat(row.getValue("tokenAmount"))
      return new Intl.NumberFormat("ru-RU").format(amount)
    },
  },
  {
    accessorKey: "signal",
    header: "Сигнал",
    cell: ({ row }) => {
      const signal = "signal" in row.original ? row.original.signal : 0
      return <div>{signal === 1 ? "BUY" : signal === -1 ? "SELL" : "HOLD"}</div>
    },
  },
  {
    accessorKey: "data",
    header: "Данные",
  },
]

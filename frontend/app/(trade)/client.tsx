"use client"

import { useEffect, useState } from "react"
import { DataTable } from "./trade-table"
import { columns } from "./trade-columns"
import { createClient } from "@/lib/supabase/client"
import camelcaseKeys from "camelcase-keys"
import { AppTable } from "@/lib/helpers"

export type NormalizedAnalysis = AppTable<"analysis">

type Props = {
  data: NormalizedAnalysis[]
}

export default function TradePageClient({ data }: Props) {
  const client = createClient()
  const [items, setItems] = useState<NormalizedAnalysis[]>(data ?? [])

  useEffect(() => {
    const channel = client
      .channel("website_listener")
      .on(
        "postgres_changes",
        {
          event: "INSERT",
          schema: "public",
          table: "analysis",
        },
        (payload) => {
          setItems((prev) => {
            const symbolIndex = prev.findIndex(
              (item) => item.symbol === payload.new.symbol
            )
            const analyz = camelcaseKeys(payload.new, {
              deep: false,
            }) as NormalizedAnalysis

            if (symbolIndex === -1) {
              return [...prev, analyz]
            }

            return prev.map((item) =>
              item.symbol === payload.new.symbol ? analyz : item
            )
          })
        }
      )
      .subscribe()

    return () => {
      channel.unsubscribe()
    }
  }, [])

  return (
    <DataTable
      columns={columns}
      data={items}
      sortingState={[{ id: "rating", desc: true }]}
    />
  )
}

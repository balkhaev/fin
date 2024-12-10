"use client"

import { apiClient } from "@/lib/api"
import { useActionState, useEffect, useState } from "react"
import { DataTable } from "./data-table"
import { columns } from "./columns"
import { Button } from "@/components/ui/button"
import { createClient } from "@/lib/supabase/client"
import camelcaseKeys from "camelcase-keys"
import { AppTable } from "@/lib/helpers"
import { JobsPanel } from "../../components/app/jobs-panel"

export type NormalizedAnalysis = AppTable<"analysis">

type Props = {
  working?: boolean
  data: NormalizedAnalysis[]
}

export default function TradePageClient({ data, working }: Props) {
  const client = createClient()
  const [items, setItems] = useState<NormalizedAnalysis[]>(data ?? [])
  const [isWorking, setWorking] = useState(working)

  const [error, submitAction, isPending] = useActionState(async () => {
    setWorking((prev) => !prev)

    const method = isWorking ? "delete" : "post"
    const { data } = await apiClient[method]("/analysis")

    if (data.status !== "ok") {
      return data.result
    }

    return null
  }, null)

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
    <>
      <div className="flex gap-4 items-center">
        <h1 className="text-2xl">Анализ валютных пар</h1>
        <div className="flex gap-2 items-center">
          <form action={submitAction} className="flex gap-2 items-center">
            <Button size="sm" disabled={isPending}>
              {isWorking ? "Остановить" : "Начать"}
            </Button>
            {error}
          </form>
          <JobsPanel />
        </div>
      </div>
      <div className="overflow-hidden flex flex-col">
        <DataTable
          columns={columns}
          data={items}
          sortingState={[{ id: "rating", desc: true }]}
        />
      </div>
    </>
  )
}

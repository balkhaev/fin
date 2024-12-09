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
  data: NormalizedAnalysis[]
}

export default function TradePageClient({ data }: Props) {
  const client = createClient()
  const [items, setItems] = useState<NormalizedAnalysis[]>(data ?? [])

  const [error, submitAction, isPending] = useActionState(async () => {
    const { data } = await apiClient.post("/analysis")

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
          setItems((prev) =>
            prev.map((item) =>
              item.symbol === payload.new.symbol
                ? (camelcaseKeys(payload.new, {
                    deep: false,
                  }) as NormalizedAnalysis)
                : item
            )
          )
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
        <form action={submitAction} className="flex gap-2 items-center">
          <Button size="sm" disabled={isPending}>
            Начать
          </Button>
          {error}
          <JobsPanel />
        </form>
      </div>
      <DataTable
        columns={columns}
        data={items}
        sortingState={[{ id: "rating", desc: true }]}
      />
    </>
  )
}

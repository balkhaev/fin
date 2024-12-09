import camelcaseKeys from "camelcase-keys"
import TradeSymbolPageClient from "./client"
import { apiClient } from "@/lib/api"
import { Tables } from "@/database.types"

export default async function TradeSymbolPage({
  params,
}: {
  params: Promise<{ symbol: string }>
}) {
  const symbol = (await params).symbol
  const { data } = await apiClient.get(`/analysis/${symbol}`)

  return (
    <TradeSymbolPageClient
      symbol={symbol}
      data={data.result.map((item: Tables<"analysis">) =>
        camelcaseKeys(item, { deep: false })
      )}
    />
  )
}

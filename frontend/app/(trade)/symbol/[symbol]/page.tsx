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
  const { data: dataBot } = await apiClient.get(`/market/${symbol}/bot`)

  return (
    <TradeSymbolPageClient
      botWorking={dataBot.result}
      symbol={symbol}
      data={data.result.map((item: Tables<"analysis">) =>
        camelcaseKeys(item, { deep: false })
      )}
    />
  )
}

import { AppTable } from "@/lib/helpers"
import TradePageClient from "./client"
import { createClient } from "@/lib/supabase/server"
import camelcaseKeys from "camelcase-keys"

export default async function TradePage() {
  const client = await createClient()
  const { data: symbols, error } = await client.rpc(
    "get_most_recent_unique_symbols"
  )

  if (error) console.log("in trade page", error)

  if (!symbols) {
    return <TradePageClient data={[]} />
  }

  const items = symbols.map(
    // @ts-expect-error 123
    (item) => camelcaseKeys(item, { deep: false }) as AppTable<"analysis">
  )

  return <TradePageClient data={items} />
}

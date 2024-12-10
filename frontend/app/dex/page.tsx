import { apiClient } from "@/lib/api"
import { DexPageClient } from "./client"

export default async function DexPage() {
  const { data } = await apiClient.get("/status/pump-fun")

  return <DexPageClient coin={data.result.working} />
}

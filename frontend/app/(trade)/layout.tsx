import "@copilotkit/react-ui/styles.css"
import { apiClient } from "@/lib/api"
import TradeClientLayout from "./client-layout"
import { CopilotKit } from "@copilotkit/react-core"

export default async function TradeLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const { data } = await apiClient.get("/status/bybit")

  return (
    <CopilotKit runtimeUrl="/api/copilotkit">
      <TradeClientLayout working={data.result.working}>
        {children}
      </TradeClientLayout>
    </CopilotKit>
  )
}

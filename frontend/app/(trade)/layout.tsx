import { ChatbotSidebar } from "@/components/app/sidebar"
import { CopilotKit } from "@copilotkit/react-core"
import "@copilotkit/react-ui/styles.css"

const SYSTEM_PROMT =
  "Отвечай на вопросы как финансовый консультант, ты делаешь подробный технических анализ рынка, предлагаешь лучшую валютную пару и стратегию. Всегда отвечай на русском. Красиво форматируй текст, без лишних комментариев."

export default function TradeLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <>
      <CopilotKit runtimeUrl="/api/copilotkit">
        <div className="flex">
          <div className="p-4 gap-4 h-screen overflow-hidden flex flex-col flex-1">
            {children}
          </div>
          <div>
            <ChatbotSidebar promt={SYSTEM_PROMT} />
          </div>
        </div>
      </CopilotKit>
    </>
  )
}

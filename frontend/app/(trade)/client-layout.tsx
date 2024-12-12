"use client"

// @ts-expect-error react 19 bug
import { useActionState, useState } from "react"

import { ChatbotSidebar } from "@/components/app/sidebar"
import { useCopilotReadable } from "@copilotkit/react-core"
import "@copilotkit/react-ui/styles.css"
import Link from "next/link"
import { apiClient } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { JobsPanel } from "@/components/app/jobs-panel"
import { Separator } from "@/components/ui/separator"
import { ChevronLeft, ChevronRight } from "lucide-react"

type Props = {
  children: React.ReactNode
  working: boolean
}

const SYSTEM_PROMT =
  "Отвечай на вопросы как финансовый консультант, ты делаешь подробный технических анализ рынка, предлагаешь лучшую валютную пару и стратегию. Всегда отвечай на русском. Красиво форматируй текст, без лишних комментариев."

export default function TradeClientLayout({ children, working }: Props) {
  const [isWorking, setWorking] = useState(working)
  const [sidebarVisible, setSidebarVisible] = useState(false)
  const [error, submitAction, isPending] = useActionState(async () => {
    setWorking((prev) => !prev)

    const method = isWorking ? "delete" : "post"
    const { data } = await apiClient[method]("/analysis")

    if (data.status !== "ok") {
      return data.result
    }

    return null
  }, null)

  useCopilotReadable({
    description: "Текущее состояние работы приложения",
    value: isWorking,
  })

  return (
    <>
      <div className="flex h-screen">
        <div className="flex flex-col flex-1 overflow-hidden">
          <div className="min-h-[60px] border-b flex items-center px-4 gap-4">
            <Link href="/">
              <div className="text-2xl">Fin</div>
            </Link>

            <Separator orientation="vertical" />

            <div className="flex gap-2 items-center justify-between flex-1">
              <form action={submitAction} className="flex gap-2 items-center">
                <Button
                  size="sm"
                  disabled={isPending}
                  variant="link"
                  className=""
                >
                  {isWorking ? "Остановить" : "Начать"}
                </Button>
                {error}
              </form>
              <div className="flex gap-2 items-center">
                <JobsPanel />
                <Button
                  onClick={() => setSidebarVisible((prev) => !prev)}
                  variant={"outline"}
                  size="icon"
                >
                  {sidebarVisible ? <ChevronLeft /> : <ChevronRight />}
                </Button>
              </div>
            </div>
          </div>
          <div className="flex flex-col flex-1 overflow-hidden">{children}</div>
        </div>
        <div className="w-[500px]">
          <ChatbotSidebar promt={SYSTEM_PROMT} />
        </div>
      </div>
    </>
  )
}

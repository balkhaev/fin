"use client"

import { useState } from "react"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Send, StopCircle } from "lucide-react"
import { useCopilotChat } from "@copilotkit/react-core"
import { Role, TextMessage } from "@copilotkit/runtime-client-gql"
import dayjs from "@/utils/date"
import { cn } from "@/lib/utils"

type Opts = {
  promt: string
}

export function ChatbotSidebar({ promt }: Opts) {
  const { visibleMessages, appendMessage, stopGeneration, isLoading } =
    useCopilotChat({
      makeSystemMessage: (
        contextString: string,
        additionalInstructions?: string
      ) => {
        return `promt: ${promt} context: ${contextString} additionalInstructions: ${additionalInstructions}`
      },
    })

  const [inputMessage, setInputMessage] = useState("")

  const handleSendMessage = async (e: React.FormEvent) => {
    e.preventDefault()

    if (isLoading) {
      stopGeneration()
      return
    }

    if (inputMessage.trim()) {
      appendMessage(new TextMessage({ content: inputMessage, role: Role.User }))
      setInputMessage("")
    }
  }

  return (
    <div className="flex flex-col h-screen w-[400px] border-l bg-background">
      <div className="p-4 font-semibold border-b">AI финансовый ассистент</div>
      <ScrollArea className="flex-1 p-4">
        {visibleMessages.map((message) => {
          if (message.isTextMessage()) {
            return (
              <div
                key={message.id}
                className={`mb-4 ${
                  message.role === "user" ? "text-right" : ""
                }`}
              >
                <div
                  className={`inline-block p-2 rounded-lg ${
                    message.role === "user"
                      ? "bg-primary text-primary-foreground"
                      : "bg-muted"
                  }`}
                >
                  <div>
                    <div className={cn("text-sm", "font-semibold")}>
                      {message.role === "user" ? "Вы" : "AI"}
                    </div>

                    <div
                      className={cn(
                        "text-sm text-muted-foreground mt-1 whitespace-pre-wrap",
                        message.role === "user" && "text-background"
                      )}
                    >
                      {message.content}
                    </div>
                  </div>
                </div>
                <div className="text-xs text-muted-foreground mt-1">
                  {dayjs(message.createdAt).fromNow()}
                </div>
              </div>
            )
          }

          if (message.isResultMessage()) {
            return (
              <div key={message.id} className={`mb-4`}>
                <div className="text-xs text-muted-foreground mt-1">
                  {message.result}
                </div>
              </div>
            )
          }

          return null
        })}
      </ScrollArea>

      <form onSubmit={handleSendMessage} className="p-4 border-t flex gap-2">
        <Input
          value={inputMessage}
          onChange={(e) => setInputMessage(e.target.value)}
          placeholder="Введите сообщение..."
          className="flex-1"
          disabled={isLoading}
        />
        <Button type="submit" size="icon">
          {isLoading ? (
            <StopCircle className="h-4 w-4" />
          ) : (
            <Send className="h-4 w-4" />
          )}
        </Button>
      </form>
    </div>
  )
}

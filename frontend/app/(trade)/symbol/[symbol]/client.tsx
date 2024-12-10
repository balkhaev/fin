"use client"

import { apiClient } from "@/lib/api"
import { useEffect, useState, useTransition } from "react"
import { DataTable } from "../../data-table"
import { columns } from "../../columns"
import { Button } from "@/components/ui/button"
import { createClient } from "@/lib/supabase/client"
import Link from "next/link"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import Tradingview from "../../../../components/app/tradingview-chart"
import { AppTable, adaptKeys } from "@/lib/helpers"
import { cn } from "@/lib/utils"
import { socket } from "@/lib/socket"
import TechnicalAnalysisDisplay from "./ta"
import { useCopilotReadable } from "@copilotkit/react-core"

type Props = {
  symbol: string
  data: AppTable<"analysis">[]
}

type Ticker = {
  symbol: string
  lastPrice: string | null
  price24hPcnt: string | null
  turnover24h: string | null
  volume24h: string | null
  bid1Price: string | null
  bid1Size: string | null
  ask1Price: string | null
  ask1Size: string | null
  openInterest: string | null
  tickDirection:
    | "ZeroPlusTick"
    | "ZeroMinusTick"
    | "PlusTick"
    | "MinusTick"
    | null
}

const taSockets = ["1", "5"]

export default function TradeSymbolPageClient({ symbol, data }: Props) {
  const client = createClient()
  const [items, setItems] = useState<AppTable<"analysis">[]>(data ?? [])
  const [ta1, setTa1] = useState()
  const [ta5, setTa5] = useState()
  const [ticker, setTicker] = useState<Ticker>({
    symbol: "ETHUSDT",
    lastPrice: null,
    price24hPcnt: null,
    turnover24h: null,
    volume24h: null,
    bid1Price: null,
    bid1Size: null,
    ask1Price: null,
    ask1Size: null,
    openInterest: null,
    tickDirection: null,
  })

  useCopilotReadable({
    description: "Тикер валютной пары",
    value: ticker,
  })
  useCopilotReadable({
    description: "Технический анализ на основе 1 минутных свечей",
    value: ta1,
  })
  useCopilotReadable({
    description: "Технический анализ на основе 5 минутных свечей",
    value: ta5,
  })
  useCopilotReadable({
    description: "История технических анализов",
    value: items,
  })

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const settersMap: Record<string, (data: any) => void> = {
    "1": setTa1,
    "5": setTa5,
  }

  const [isPending, startTransition] = useTransition()

  const handleStartClick = () => {
    startTransition(() => {
      apiClient.post("/analysis/" + symbol)
    })
  }

  useEffect(() => {
    socket.on("ticker", (data) => {
      setTicker((prev) => ({ ...prev, ...JSON.parse(data) }))
    })

    taSockets.forEach((timeframe) => {
      socket.on("ta" + timeframe, (data) =>
        settersMap[timeframe]?.(JSON.parse(data))
      )
    })

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
            payload.new.symbol === symbol
              ? [adaptKeys(payload.new) as AppTable<"analysis">, ...prev]
              : prev
          )
        }
      )
      .subscribe()

    apiClient.post(`/market/${symbol}/listen`)

    return () => {
      channel.unsubscribe()
      apiClient.post(`/market/${symbol}/unlisten`)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const parseValue = (value: string | null) => (value ? parseFloat(value) : 0)

  const volume24h = parseValue(ticker.volume24h)
  const price24hPcnt = parseValue(ticker.price24hPcnt) * 100
  const turnover24h = parseValue(ticker.turnover24h)
  const lastPrice = parseValue(ticker.bid1Price)

  return (
    <>
      <div className="flex gap-4 items-center">
        <h1 className="text-2xl">Анализ валютной пары {symbol}</h1>
        <div className="flex gap-2">
          <Link href="/">
            <Button size="sm">Назад</Button>
          </Link>
          <Button
            variant={"outline"}
            size="sm"
            disabled={isPending}
            onClick={handleStartClick}
          >
            Скан
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-4 text-center py-4">
        <div>
          <div
            className={cn(
              ticker.tickDirection?.toLocaleLowerCase().includes("plus")
                ? "text-green-500"
                : "text-red-500",
              "font-bold"
            )}
          >
            {lastPrice.toLocaleString("ru-RU")} USD
          </div>
          <div>Last price</div>
        </div>
        <div>
          <div className="font-bold">
            {volume24h.toLocaleString("ru-RU")} USD
          </div>
          <div>Volume (24h)</div>
        </div>
        <div>
          <div className="font-bold">
            {turnover24h.toLocaleString("ru-RU")} USD
          </div>
          <div>объём торгов (24h)</div>
        </div>
        <div>
          <div
            className={cn(
              ticker.tickDirection?.toLocaleLowerCase().includes("plus")
                ? "text-green-500"
                : "text-red-500",
              "font-bold"
            )}
          >
            {price24hPcnt.toFixed(2)}%
          </div>
          <div>Change (24h)</div>
        </div>
      </div>
      <div className="h-[600px] overflow-hidden grid grid-cols-4">
        <div className="col-span-3">
          <Tradingview symbol={symbol} />
        </div>
        <div className="bg-[#131722] border-[#363a45] border border-l-0 p-2">
          <Tabs defaultValue="1">
            <TabsList className="grid w-full grid-cols-2">
              <TabsTrigger value="1">1m</TabsTrigger>
              <TabsTrigger value="5">5m</TabsTrigger>
            </TabsList>
            <TabsContent value="1">
              <TechnicalAnalysisDisplay ta={ta1} />
            </TabsContent>
            <TabsContent value="5">
              <TechnicalAnalysisDisplay ta={ta5} />
            </TabsContent>
          </Tabs>
        </div>
      </div>
      <Tabs
        defaultValue="market"
        className="flex-1 overflow-hidden flex flex-col"
      >
        {/* <TabsList>
          <TabsTrigger value="market">Маркет</TabsTrigger>
        </TabsList> */}
        <TabsContent value="market" className="space-y-4 overflow-auto">
          <DataTable columns={columns} data={items} />
        </TabsContent>
      </Tabs>
    </>
  )
}

"use client"

import { apiClient } from "@/lib/api"
import { useEffect, useState } from "react"
import { DataTable } from "../../trade-table"
import { columns } from "../../trade-columns"
import { createClient } from "@/lib/supabase/client"
import { Tabs, TabsContent } from "@/components/ui/tabs"
import { AppTable, adaptKeys } from "@/lib/helpers"
import { socket } from "@/lib/socket"
import TechnicalAnalysisDisplay from "./ta"
import { useCopilotReadable } from "@copilotkit/react-core"
import { omit } from "remeda"
import { AppChart } from "@/components/app/apex-charts"
import { Candlestick } from "@/lib/types"
import { candlesToSeries } from "@/lib/candles-to-series"
import { Button } from "@/components/ui/button"

type Props = {
  symbol: string
  data: AppTable<"analysis">[]
  botWorking: boolean
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

export default function TradeSymbolPageClient({
  symbol,
  data,
  botWorking,
}: Props) {
  const client = createClient()
  const [items, setItems] = useState<AppTable<"analysis">[]>(data ?? [])
  const [ta1, setTa1] = useState<AppTable<"analysis">>()
  const [ta5, setTa5] = useState<AppTable<"analysis">>()
  const [candles, setCandles] = useState<Candlestick[]>([])
  const [signal, setSignal] = useState<number | null>(null)
  const [working, setWorking] = useState(botWorking)
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
    value: ta1 ? omit(ta1, ["trend"]) : {},
  })
  useCopilotReadable({
    description: "Технический анализ на основе 5 минутных свечей",
    value: ta5 ? omit(ta5, ["trend"]) : {},
  })
  useCopilotReadable({
    description: "История технических анализов",
    value: items.slice(0, 5),
  })

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const settersMap: Record<string, (data: any) => void> = {
    "1": setTa1,
    "5": setTa5,
  }

  useEffect(() => {
    socket.on("ticker", (data) => {
      setTicker((prev) => ({ ...prev, ...JSON.parse(data) }))
    })

    socket.on("candles", (data, signal) => {
      setCandles(data)
      setSignal(signal)
    })

    taSockets.forEach((timeframe) => {
      socket.on("ta" + timeframe, (data) => {
        settersMap[timeframe]?.(JSON.parse(data))
      })
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

  const handleBot = async () => {
    setWorking((prev) => !prev)

    const method = working ? "delete" : "post"

    await apiClient[method](`/market/${symbol}/bot`)
  }

  return (
    <>
      <div className="grid grid-cols-4 text-center py-4">{signal}</div>
      <div className="overflow-hidden grid grid-cols-4">
        <div className="col-span-3">
          <AppChart
            options={{
              chart: {
                type: "candlestick",
                height: 350,
              },
              title: {
                text: "CandleStick Chart",
                align: "left",
              },
              xaxis: {
                type: "datetime",
              },
              yaxis: {
                tooltip: {
                  enabled: true,
                },
              },
              plotOptions: {
                candlestick: {
                  colors: {
                    upward: "#3C90EB",
                    downward: "#DF7D46",
                  },
                },
              },
            }}
            type="candlestick"
            series={candlesToSeries(candles)}
            width="100%"
            height={600}
          />
          {/* <Tradingview symbol={symbol} /> */}
        </div>
        <div className="bg-[#131722] border-[#363a45] border border-l-0 flex flex-col justify-between">
          <TechnicalAnalysisDisplay ta1={ta1} ta5={ta5} />
          <Button className="rounded-none" onClick={handleBot}>
            {working ? "Стоп" : "Начать"}
          </Button>
        </div>
      </div>
      <Tabs
        defaultValue="market"
        className="flex-1 overflow-hidden flex flex-col"
      >
        {/* <TabsList>
          <TabsTrigger value="market">Маркет</TabsTrigger>
        </TabsList> */}
        <TabsContent
          value="market"
          className="space-y-4 overflow-hidden mt-0 flex flex-col"
        >
          <DataTable columns={columns} data={items} />
        </TabsContent>
      </Tabs>
    </>
  )
}

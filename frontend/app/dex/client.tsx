"use client"

import Link from "next/link"
import { useEffect, useState } from "react"
import useLocalStorageState from "use-local-storage-state"

import { apiClient } from "@/lib/api"
import { socket } from "@/lib/socket"
import { AppTimeline, TimelineEvent } from "@/components/app/timeline"
import { TxTable } from "./tx-table"
import {
  BuySellTransaction,
  Candlestick,
  CreateTransaction,
  Transaction,
} from "@/lib/types"

import { ChartTabs } from "./chart-tabs"
import { TradeControls } from "./trade-controls"
import { Button } from "@/components/ui/button"

import photonImg from "./photon.webp"
import solanaImg from "./solana.png"
import pumpImg from "./pump.png"
import Image from "next/image"
import {
  StrategyOptionsForm,
  StrategyOptionsFormValues,
} from "@/components/app/strategy-form/strategy-options-form"

export type BuySellState = {
  tx: Transaction
  coin: CreateTransaction
  signature: string
}

const TransactionItem = ({ state }: { state: BuySellState }) => (
  <div className="flex items-center gap-2">
    <Link
      href={`https://explorer.solana.com/tx/${state.signature}`}
      target="_blank"
    >
      <Button size="sm">
        <Image src={solanaImg} alt="solana" width={16} height={16} /> Solana
      </Button>
    </Link>
    <div>
      <div>Trader: {state.tx.traderPublicKey}</div>
      <div>
        [{state.tx.txType}] MC: {state.tx.marketCapSol} SOL
      </div>
    </div>
  </div>
)

type Props = {
  coin: CreateTransaction
}

export function DexPageClient({ coin }: Props) {
  const [txs, setTxs] = useState<BuySellTransaction[]>([])
  const [loading, setLoading] = useState(false)
  const [currentCoin, setCurrentCoin] = useState<CreateTransaction | null>(coin)
  const [events, setEvents] = useState<TimelineEvent[]>([])
  const [coins, setCoins] = useState<CreateTransaction[]>([])
  const [working, setWorking] = useState(currentCoin !== null)
  const [strategyOptions, setStrategyOptions] =
    useLocalStorageState<StrategyOptionsFormValues>("strategy-options", {
      storageSync: true,
      defaultValue: {
        mode: "test",
        strategy: "doubleSupertrend",
        takeProfit: 1.22,
        candlesMs: 1000,
        maxCoinTime: 2000,
        global: { period: 11, multiplier: 4 },
        realtime: { period: 4, multiplier: 2 },
        fallback: {
          periodSell: 5,
          multiplierSell: 3,
          periodBuy: 3,
          multiplierBuy: 3,
          minCandlesForDouble: 10,
        },
      },
    })

  const [candlesData, setCandlesData] = useState<Record<string, Candlestick[]>>(
    {}
  )
  const [selectedCoin, setSelectedCoin] = useState<CreateTransaction | null>(
    null
  )

  const reset = () => {
    setTxs([])
    setCoins([])
    setCurrentCoin(null)
    setSelectedCoin(null)
    setCandlesData({})
    setEvents([])
    setWorking(false)
  }
  const addEvent = (event: TimelineEvent) => {
    setEvents((prev) => [...prev, event])
  }

  useEffect(() => {
    socket.on("buy", (data) => {
      const swoshSound = new Audio(
        "https://cdn.freesound.org/previews/683/683101_6253486-lq.mp3"
      )
      swoshSound.volume = 0.2
      swoshSound.play()
      const tx = JSON.parse(data) as CreateTransaction

      setCurrentCoin(tx)
      addEvent({
        date: new Date(),
        title: "Поймали монету",
        content: (
          <div className="flex items-center gap-2">
            <Link
              href={`https://photon-sol.tinyastro.io/en/lp/${tx?.mint}`}
              target="_blank"
            >
              <Button size="sm">
                <Image src={photonImg} alt="photon" width={16} height={16} />{" "}
                Photon
              </Button>
            </Link>
            <Link href={`https://pump.fun/coin/${tx?.mint}`} target="_blank">
              <Button size="sm">
                <Image src={pumpImg} alt="pump" width={16} height={16} />{" "}
                Pump.fun
              </Button>
            </Link>
            <Link href={`https://photon-sol.tinyastro.io/en/lp/${tx.mint}`}>
              {tx.name}
            </Link>
          </div>
        ),
      })
    })
    socket.on("buyed", (data) => {
      const fartSound = new Audio(
        "https://cdn.freesound.org/previews/498/498167_9986848-lq.mp3"
      )
      fartSound.volume = 0.2
      fartSound.play()
      const state = JSON.parse(data) as BuySellState

      addEvent({
        date: new Date(),
        title: "Купили",
        content: <TransactionItem state={state} />,
      })
    })
    socket.on("selled", (data) => {
      const coinSound = new Audio(
        "https://cdn.freesound.org/previews/435/435584_5907491-lq.mp3"
      )
      coinSound.volume = 0.2
      coinSound.play()

      const state = JSON.parse(data) as BuySellState
      addEvent({
        date: new Date(),
        title: "Продали",
        content: <TransactionItem state={state} />,
      })
      setWorking(false)
    })
    socket.on("tx", (data) => {
      setTxs((prev) => [data, ...prev].slice(0, 100))
    })
    socket.on("exit", () => reset())
    socket.on("new-coin", (data: CreateTransaction) => {
      if (coins.findIndex((c) => c.mint === data.mint) !== -1) return

      setCoins((prev) => [...prev, data])

      socket.on("candles", (cdata) => {
        setCandlesData((prev) => ({
          ...prev,
          [data.mint]: cdata.candles,
        }))
      })
    })

    return () => {
      socket.off("buy")
      socket.off("buyed")
      socket.off("selled")
      socket.off("tx")
      socket.off("exit")
      socket.off("new-coin")
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleStart = async (test = false) => {
    reset()
    setWorking(true)
    setLoading(true)
    await apiClient.post("/sniper", strategyOptions, { params: { test } })
    setLoading(false)
  }

  const handleStop = async (stopAll: boolean) => {
    setWorking(false)
    setLoading(true)
    await apiClient.post("/sniper/stop", { stopAll })
    setLoading(false)
    setCurrentCoin(null)
  }

  const handleClear = () => {
    reset()
  }

  const handleSell = async () => {
    if (!currentCoin) return
    setWorking(true)
    await apiClient.post("/sniper/sell")
    setWorking(false)
  }

  return (
    <>
      <div className="grid grid-cols-[2fr,1fr] gap-2">
        <div>
          <ChartTabs
            title={selectedCoin?.name ?? ""}
            candlesData={candlesData}
            selectedCoin={selectedCoin?.mint ?? ""}
          />
        </div>
        <div>
          <div className="border rounded-lg flex-1 h-full flex flex-col justify-between">
            {currentCoin ? (
              <div>
                <div className="text-2xl text-center mt-2 p-2">
                  FonTbFXnR3eFrfXgwbcBcFYbdpqcwa3McLeJweGnjHXN
                </div>
                <AppTimeline events={events} />
              </div>
            ) : (
              <div className="p-2">
                <StrategyOptionsForm
                  values={strategyOptions}
                  onChange={setStrategyOptions}
                />
              </div>
            )}
            <TradeControls
              working={working}
              loading={loading}
              active={currentCoin !== null}
              onStop={handleStop}
              onClear={handleClear}
              onStart={handleStart}
              onSell={handleSell}
            />
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-auto">
        <TxTable data={txs} />
      </div>
    </>
  )
}

import analyzeExchange from "../apps/bybit/analyze"
import { supabase } from "../sdk/supabase"
import { bybitRestClient } from "../apps/bybit/sdk/clients"
import { analyzeSymbolQueue } from "../apps/bybit/analyze-symbol-queue"

import express from "express"
import { listenBybit, unlistenBybit } from "../apps/bybit/websocket"
import { listenMoneyEvents } from "../apps/pump-fun"
import { pumpFunEvents } from "../apps/pump-fun/listener"
import { DEVNET_POOL, QUICKNODE_POLL } from "../sdk/solana"
import { clearActiveDeals, getBuyedTx, isRunning } from "../apps/pump-fun/state"
import { sendPumpTransaction } from "../apps/pump-fun/send-transaction"

const router = express.Router()

router.get("/analysis/:symbol", async (req, res) => {
  const { data } = await supabase
    .from("analysis")
    .select("*")
    .filter("symbol", "eq", req.params.symbol)
    .order("created_at", { ascending: false })

  res.json({
    status: "ok",
    result: data,
  })
})

router.post("/analysis", async (req, res) => {
  const result = await analyzeExchange()

  res.json({
    status: "ok",
    result,
  })
})

router.delete("/analysis", async (req, res) => {
  await analyzeSymbolQueue.removeJobs("*")

  res.json({
    status: "ok",
  })
})

router.get("/analysis", async (req, res) => {
  const { data } = await supabase.from("analysis").select("*")

  res.json({
    status: "ok",
    result: data,
  })
})

router.post("/analysis/:symbol", async (req, res) => {
  analyzeSymbolQueue.add({ symbol: req.params.symbol })

  res.json({
    status: "ok",
  })
})

router.get("/market/:symbol", async (req, res) => {
  const data = await bybitRestClient.getTickers({
    symbol: req.params.symbol,
    category: "linear",
  })

  res.json(data.result)
})

router.post("/market/:symbol/listen", async (req, res) => {
  listenBybit(req.params.symbol)

  res.json({
    status: "ok",
  })
})

router.post("/market/:symbol/unlisten", async (req, res) => {
  unlistenBybit(req.params.symbol)

  res.json({
    status: "ok",
  })
})

router.post("/sniper", async (req, res) => {
  console.log("STARTING")
  console.log(JSON.stringify(req.body, null, 2))
  console.log("============")

  clearActiveDeals()
  listenMoneyEvents(req.body)

  res.json({
    status: "ok",
  })
})

router.post("/sniper/stop", async (req, res) => {
  const { stopAll } = req.body || {}

  pumpFunEvents.emit("stop", stopAll)

  res.json({
    status: "ok",
  })
})

router.post("/sniper/sell", async (req, res) => {
  const buyedTx = getBuyedTx()

  if (!buyedTx) {
    res.json({
      status: "error",
      error: {
        message: "no buyed tx",
      },
    })

    return
  }

  const signature = await sendPumpTransaction({
    action: "sell",
    mint: buyedTx.mint,
    amount: "100%",
    pool: QUICKNODE_POLL,
  })

  res.json({
    status: "ok",
    result: {
      signature,
    },
  })
})

router.get("/status", async (req, res) => {
  res.json({
    status: "ok",
    result: {
      working: getBuyedTx(),
    },
  })
})

export default router

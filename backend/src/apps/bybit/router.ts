import analyzeBybit from "./analyze"
import { supabase } from "../../lib/supabase"
import { bybitRestClient } from "./sdk/clients"

import express from "express"
import {
  botWorking,
  listenBybit,
  startBot,
  stopBot,
  unlistenBybit,
} from "./websocket"
import { analyzeSymbolQueue } from "./queue/analyze-symbol"
import { botQueue } from "./queue/bot"
import { analyzeBybitCron } from "./crons"

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
  analyzeBybitCron.start()

  const result = await analyzeBybit()

  res.json({
    status: "ok",
    result,
  })
})

router.get("/status/bybit", async (req, res) => {
  res.json({
    status: "ok",
    result: {
      working: analyzeBybitCron.running,
    },
  })
})

router.delete("/analysis", async (req, res) => {
  analyzeBybitCron.stop()
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
  analyzeSymbolQueue.add({ symbol: req.params.symbol }, { lifo: true })

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

router.get("/market/:symbol/bot", async (req, res) => {
  res.json({
    status: "ok",
    result: botWorking(),
  })
})

router.post("/market/:symbol/bot", async (req, res) => {
  console.log("START BOT")
  startBot(req.params.symbol)

  res.json({
    status: "ok",
  })
})

router.delete("/market/:symbol/bot", async (req, res) => {
  console.log("STOP BOT")
  stopBot()

  res.json({
    status: "ok",
  })
})

export default router

import express from "express"
import { listenMoneyEvents } from "."
import { pumpFunEvents } from "./listener"
import { QUICKNODE_POLL } from "../../lib/solana"
import { clearActiveDeals, getBuyedTx } from "./state"
import { sendPumpTransaction } from "./send-transaction"

const router = express.Router()

router.post("/sniper", async (req, res) => {
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

router.get("/status/pump-fun", async (req, res) => {
  res.json({
    status: "ok",
    result: {
      working: getBuyedTx(),
    },
  })
})

export default router

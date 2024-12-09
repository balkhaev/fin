import WebSocket from "ws"
import EventEmitter from "node:events"
import { Transaction } from "./types"
import { SECONDS_TO_LISTEN_COIN } from "."
import { MIN_MARKET_CAP_LIMIT_IN_SOL, StrategyOpts, rateTx } from "./strategy"
import {
  addPrevTx,
  coinHaveHistory,
  isActive,
  thisMintIsActive,
  txToMsTx,
} from "./state"

export const pumpFunEvents = new EventEmitter()

pumpFunEvents.setMaxListeners(100)

export function listenPumpFun(opts: StrategyOpts) {
  const newTokenWs = new WebSocket("wss://pumpportal.fun/api/data")
  const tradesWs = new WebSocket("wss://pumpportal.fun/api/data")

  newTokenWs.on("open", function open() {
    newTokenWs.send(
      JSON.stringify({
        method: "subscribeNewToken",
      })
    )
  })

  newTokenWs.on("message", function message(data) {
    const coin = JSON.parse(data as unknown as string)

    if (coin.message) return

    const unsubscribeTokenTrade = () => {
      tradesWs.send(
        JSON.stringify({
          method: "unsubscribeTokenTrade",
          keys: [coin.mint],
        })
      )

      clearTimeout(timeoutId)
    }

    const timeoutId = setTimeout(
      unsubscribeTokenTrade,
      SECONDS_TO_LISTEN_COIN * 1000
    )

    tradesWs.send(
      JSON.stringify({
        method: "subscribeTokenTrade",
        keys: [coin.mint],
      })
    )

    tradesWs.on("message", function message(data) {
      const msg = JSON.parse(data as unknown as string)

      if (msg.message || msg.mint !== coin.mint) return
      if (isActive() && !thisMintIsActive(msg.mint)) return

      const tx = msg as Transaction

      if (
        !coinHaveHistory(tx.mint) &&
        MIN_MARKET_CAP_LIMIT_IN_SOL > tx.marketCapSol
      ) {
        pumpFunEvents.emit("new-token", coin)
      }

      const txWithTs = txToMsTx(tx)
      const result = rateTx(txWithTs, coin, opts)

      addPrevTx(tx.mint, txWithTs)

      pumpFunEvents.emit("tx", { coin, tx, result })

      if (result.signal === 1) {
        pumpFunEvents.emit("buy", { coin, tx, result })
        clearTimeout(timeoutId)
      }

      if (result.signal === -1) {
        pumpFunEvents.emit("sell", { coin, tx, result })
        unsubscribeTokenTrade()
      }
    })
  })

  return {
    destroy() {
      pumpFunEvents.removeAllListeners()
      newTokenWs.close()
      tradesWs.close()
    },
  }
}

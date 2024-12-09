import { BUY_AMOUNT_IN_SOL } from "."
import { io } from "../../server"
import { sendPumpTransaction } from "./send-transaction"
import {
  addActiveDeal,
  addBuyed,
  addSelled,
  addWaitBuy,
  addWaitSell,
  clearBuyedTx,
  isActive,
  isBuyed,
  isWaitSell,
  removeWaitBuy,
  removeWaitSell,
  setBuyedTx,
} from "./state"
import { CreateTransaction, Transaction } from "./types"

export async function handleBuy(
  data: any,
  blacklist: string[],
  blacklistedMints: string[],
  pool: string
) {
  const { coin, tx } = data

  if (isActive() || isBuyed(coin.mint)) return
  if (blacklistedMints.includes(coin.mint)) return
  if (
    blacklist.includes(coin.traderPublicKey) ||
    blacklist.includes(tx.traderPublicKey)
  ) {
    blacklistedMints.push(coin.mint)
    return
  }

  io.emit("buy", JSON.stringify(coin))
  addWaitBuy(coin.mint)
  addActiveDeal(tx.mint)

  const signature = await sendPumpTransaction({
    action: "buy",
    mint: coin.mint,
    amount: BUY_AMOUNT_IN_SOL,
    pool,
  })

  setBuyedTx(tx)
  addBuyed(coin.mint)
  io.emit("buyed", JSON.stringify({ tx, coin, signature }))

  removeWaitBuy(coin.mint)
}

export async function handleSell(
  coin: CreateTransaction,
  tx: Transaction | undefined,
  pool: string
) {
  let waitSellIndex = -1

  if (isWaitSell(coin.mint)) {
    return false
  }

  if (!isBuyed(coin.mint)) {
    return false
  }

  waitSellIndex = addWaitSell(coin.mint)

  const signature = await sendPumpTransaction({
    action: "sell",
    mint: coin.mint,
    amount: "100%",
    pool,
  })

  clearBuyedTx()
  addSelled(coin.mint)
  removeWaitSell(waitSellIndex)
  io.emit("selled", JSON.stringify({ tx, coin, signature }))

  return true
}

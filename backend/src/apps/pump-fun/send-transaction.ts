import axios, { ResponseType } from "axios"
import {
  QUICKNODE_POLL,
  WALLET_PUBLIC_KEY,
  signAndSend,
} from "../../sdk/solana"

type Opts = {
  mint: string
  action: "buy" | "sell"
  amount: number | "100%"
  pool?: string
  responseType?: ResponseType
}

export async function sendPumpTransaction({
  action,
  mint,
  amount = 0.1,
  pool = QUICKNODE_POLL,
}: Opts) {
  const { data } = await axios(`https://pumpportal.fun/api/trade-local`, {
    method: "POST",
    data: {
      publicKey: WALLET_PUBLIC_KEY,
      action,
      mint,
      denominatedInSol: "true",
      amount,
      slippage: action === "sell" ? 50 : 20,
      priorityFee: 0.0042,
      pool: "pump",
    },
    responseType: "arraybuffer",
  })

  return signAndSend(new Uint8Array(data), pool)
}

let retries = 5
export async function recursivSendPumpTransaction(opts: Opts) {
  try {
    const result = await sendPumpTransaction(opts)
    retries = 5
    return result
  } catch (e) {
    retries--
    if (retries === 0) throw e
    return recursivSendPumpTransaction(opts)
  }
}

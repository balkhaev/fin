import {
  createSolanaRpc,
  getBase64EncodedWireTransaction,
  getTransactionDecoder,
  signTransaction,
} from "@solana/web3.js"
import { createKeyPairFromBytes } from "@solana/keys"
import base58 from "bs58"

export const WALLET_PUBLIC_KEY = process.env.PHANTOM_SOLANA_WALLET!
export const WALLET_PRIVATE_KEY = process.env.PHANTOM_SOLANA_PRIVATE_KEY!

export const DEVNET_POOL = "https://api.devnet.solana.com"
export const MAIN_POOL = "https://api.mainnet-beta.solana.com"
export const QUICKNODE_POLL =
  "https://tiniest-capable-ensemble.solana-mainnet.quiknode.pro/3ee99c62c9f2f5c7671a3841141b75086e7376c9"

export async function signAndSend(
  unsignedSerializedTx: Uint8Array,
  pool = QUICKNODE_POLL
) {
  const rpc = createSolanaRpc(pool)

  try {
    const tx = getTransactionDecoder().decode(unsignedSerializedTx)
    const keypair = await createKeyPairFromBytes(
      base58.decode(WALLET_PRIVATE_KEY),
      true
    )

    const signedTx = await signTransaction([keypair], tx)
    const signature = await rpc
      .sendTransaction(getBase64EncodedWireTransaction(signedTx), {
        encoding: "base64",
        skipPreflight: true,
        preflightCommitment: "confirmed",
      })
      .send()

    return signature
  } catch (error) {
    console.error("Error during signAndSend:", error)
    throw error
  }
}

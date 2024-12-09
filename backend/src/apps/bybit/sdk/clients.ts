import { WebsocketClient, RestClientV5 } from "bybit-api"

const key = process.env.BYBIT_API_KEY!
const secret = process.env.BYBIT_API_SECRET!

export const bybitRestClient = new RestClientV5({ key, secret })

export const bybitWsClient = new WebsocketClient({
  market: "v5",
  testnet: false,
})

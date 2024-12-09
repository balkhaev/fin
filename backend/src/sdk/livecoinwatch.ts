import axios from "axios"
import { addDays } from "date-fns"
import { configDotenv } from "dotenv"

configDotenv()

const livecoinClient = axios.create({
  baseURL: "https://api.livecoinwatch.com",
  headers: {
    "x-api-key": process.env.LIVECOINWATCH_API_KEY,
  },
})

export async function listTopCoins({ currency = "USD" } = {}) {
  const { data } = await livecoinClient.post("/coins/list", {
    currency,
    sort: "rank",
    order: "ascending",
    offset: 0,
    limit: 10,
    meta: false,
  })

  return data
}

export async function overview({ currency = "USD" } = {}) {
  const { data } = await livecoinClient.post("/overview", {
    currency,
  })

  return data
}

export async function overviewHistory({ currency = "USD" } = {}) {
  const { data } = await livecoinClient.post("/overview/history", {
    currency,
    start: addDays(new Date(), -1).getTime(),
    end: new Date().getTime(),
  })

  return data
}

export async function coinOverview({ code = "BTC", currency = "USD" } = {}) {
  const { data } = await livecoinClient.post("/coins/single", {
    currency,
    code,
    meta: true,
  })

  return data
}

export async function coinHistory({ code = "BTC", currency = "USD" } = {}) {
  const { data } = await livecoinClient.post("/coins/single/history", {
    currency,
    code,
    start: addDays(new Date(), -1).getTime(),
    end: new Date().getTime(),
  })

  return data
}

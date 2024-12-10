import express from "express"

import { createBullBoard } from "@bull-board/api"
import { BullAdapter } from "@bull-board/api/bullAdapter"
import { ExpressAdapter } from "@bull-board/express"

import { analyzeSymbolQueue } from "../apps/bybit/queue/analyze-symbol"
import { botQueue } from "../apps/bybit/queue/bot"

export const BULL_BOARD_ROUTE = "/queues-ui"

export function createBullServer(app: express.Application) {
  const serverAdapter = new ExpressAdapter()
  serverAdapter.setBasePath(BULL_BOARD_ROUTE)

  createBullBoard({
    queues: [new BullAdapter(analyzeSymbolQueue), new BullAdapter(botQueue)],
    serverAdapter,
  })

  app.use(BULL_BOARD_ROUTE, serverAdapter.getRouter())

  return serverAdapter
}

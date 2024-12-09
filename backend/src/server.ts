import express from "express"
import http from "http"
import cors from "cors"
import { Server } from "socket.io"

import { createBullBoard } from "@bull-board/api"
import { BullAdapter } from "@bull-board/api/bullAdapter"
import { ExpressAdapter } from "@bull-board/express"

import { analyzeSymbolQueue } from "./apps/bybit/queue/analyze-symbol"

import pumpRouter from "./apps/pump-fun/router"
import bybitRouter from "./apps/bybit/router"
import { botQueue } from "./apps/bybit/queue/bot"

export const BULL_BOARD_ROUTE = "/queues-ui"

const routers = [pumpRouter, bybitRouter]

const serverAdapter = new ExpressAdapter()
serverAdapter.setBasePath(BULL_BOARD_ROUTE)

createBullBoard({
  queues: [new BullAdapter(analyzeSymbolQueue), new BullAdapter(botQueue)],
  serverAdapter,
})

export const app = express()
export const server = http.createServer(app)
export const io = new Server(server, {
  cors: {
    origin: true,
    credentials: true,
  },
})

app.use(express.json())
app.use(BULL_BOARD_ROUTE, serverAdapter.getRouter())
app.use(cors({ origin: true }))

routers.forEach((router) => app.use(router))

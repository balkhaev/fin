import { Server } from "socket.io"
import http from "http"

export function createWebSocketServer(server: http.Server) {
  return new Server(server, {
    cors: {
      origin: true,
      credentials: true,
    },
  })
}

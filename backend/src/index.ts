import dotenv from "dotenv"

dotenv.config()

import { server, io } from "./server"

const PORT = process.env.PORT || 8000

async function main() {
  server.listen(PORT, () => {
    console.log("Сервер запущен на порту", PORT)
  })
}

main().catch((err) => {
  io.emit("exit", JSON.stringify(err))
  console.error(err)
  process.exit(1)
})

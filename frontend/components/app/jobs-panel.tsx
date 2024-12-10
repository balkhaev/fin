"use client"

import { socket } from "@/lib/socket"
import { useEffect, useState } from "react"

export function JobsPanel() {
  const [jobCount, setJobCount] = useState<number>(0)

  useEffect(() => {
    socket.on("job-count", (count) => {
      setJobCount(count)
    })
  }, [])

  if (jobCount === 0) {
    return null
  }

  return `Задач ${jobCount}`
}

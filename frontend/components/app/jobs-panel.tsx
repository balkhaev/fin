"use client"

import { socket } from "@/lib/socket"
import { useEffect, useState } from "react"

export function JobsPanel() {
  const [activeJobs, setActiveJobs] = useState<string[]>([])

  // socket.on("active-job", (data) => {
  //   const job = JSON.parse(data)
  // })

  useEffect(() => {
    socket.on("waiting-job", (jobId) => {
      setActiveJobs((prev) => [...prev, jobId])
    })

    socket.on("completed-job", () => {
      setActiveJobs((prev) => prev.slice(0, -1))
    })
  }, [])

  if (activeJobs.length === 0) {
    return null
  }

  return `Задач ${activeJobs.length}`
}

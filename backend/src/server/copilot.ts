import express from "express"
import {
  CopilotRuntime,
  GroqAdapter,
  copilotRuntimeNodeHttpEndpoint,
} from "@copilotkit/runtime"

const MODEL_NAME = "llama3-70b-8192"
// const MODEL_NAME = "llama3-groq-70b-8192-tool-use-preview"

const serviceAdapter = new GroqAdapter({ model: MODEL_NAME })

export function createCopilot(app: express.Application) {
  app.use("/copilotkit", (req, res, next) => {
    const runtime = new CopilotRuntime()
    const handler = copilotRuntimeNodeHttpEndpoint({
      endpoint: "/copilotkit",
      runtime,
      serviceAdapter,
    })

    // @ts-ignore
    return handler(req, res, next)
  })
}

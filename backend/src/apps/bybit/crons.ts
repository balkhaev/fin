import { CronJob } from "cron"
import analyzeBybit from "./analyze"

export const analyzeBybitCron = new CronJob(
  "*/3 * * * *",
  () => {
    analyzeBybit()
  },
  null
)

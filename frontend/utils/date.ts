import dayjsOrig from "dayjs"
import utc from "dayjs/plugin/utc"
import timezone from "dayjs/plugin/timezone"
import "dayjs/locale/fr"
import relativeTime from "dayjs/plugin/relativeTime"
import ru from "dayjs/locale/ru"

dayjsOrig.extend(relativeTime)
dayjsOrig.extend(utc)
dayjsOrig.extend(timezone)

dayjsOrig.tz.setDefault("Europe/Moscow")
dayjsOrig.locale(ru)

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const dayjs = (date: Date) => {
  return dayjsOrig(date).tz()
}

const timezonedUnix = (value: number) => {
  return dayjsOrig.unix(value).tz()
}

dayjs.unix = timezonedUnix

export default dayjs

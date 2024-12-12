import EventEmitter from "node:events"

export const bybitEvents = new EventEmitter()

bybitEvents.setMaxListeners(100)

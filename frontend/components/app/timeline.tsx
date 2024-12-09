"use client"

import React, { ReactNode } from "react"
import { motion } from "framer-motion"

export interface TimelineEvent {
  date: Date
  title: string
  content: ReactNode
}

interface CompactAnimatedTimelineProps {
  events: TimelineEvent[]
}

export const AppTimeline: React.FC<CompactAnimatedTimelineProps> = ({
  events,
}) => {
  return (
    <div className="container mx-auto px-4 py-8">
      <div className="relative ml-3">
        {events.map((event, index) => (
          <TimelineItem key={index} event={event} index={index} />
        ))}
      </div>
    </div>
  )
}

const TimelineItem: React.FC<{ event: TimelineEvent; index: number }> = ({
  event,
  index,
}) => {
  return (
    <div className="mb-6 ml-6">
      <motion.div
        className="pt-1"
        initial={{ opacity: 0, x: 50 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{
          type: "spring",
          stiffness: 100,
          damping: 20,
          delay: index * 0.1,
        }}
      >
        <h3 className="text-lg font-semibold text-gray-200 mb-2">
          {event.title}{" "}
          <span className="text-sm text-gray-500 mb-1">
            {event.date.toLocaleTimeString()}
          </span>
        </h3>
        <div className="text-base text-gray-500">{event.content}</div>
      </motion.div>
    </div>
  )
}

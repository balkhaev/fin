"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useForm } from "react-hook-form"
import * as z from "zod"
import { Form } from "@/components/ui/form"
import { useEffect } from "react"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { DoubleSupertrend } from "./double-supertrend"
import { Supertrend } from "./supertrend"
import { Sniper } from "./sniper"
import { Strategies } from "@/lib/types"

const trendConfigSchema = z.object({
  period: z.number().int().positive().optional(),
  multiplier: z.number().positive().optional(),
})

const formSchema = z.object({
  strategy: z
    .enum(["doubleSupertrend", "supertrend", "sniper"])
    .default("doubleSupertrend"),
  mode: z.enum(["test", "prod"]).default("test"),
  takeProfit: z.number().positive().default(1.22),
  candlesMs: z.number().int().positive().default(1000),
  maxCoinTime: z.number().int().positive().default(2000),
  global: trendConfigSchema.optional(),
  realtime: trendConfigSchema.optional(),
  fallback: z
    .object({
      periodSell: z.number().int().positive().optional(),
      multiplierSell: z.number().positive().optional(),
      periodBuy: z.number().int().positive().optional(),
      multiplierBuy: z.number().positive().optional(),
      minCandlesForDouble: z.number().int().positive().optional(),
    })
    .optional(),
})

export type StrategyOptionsFormValues = z.infer<typeof formSchema>

type Props = {
  onChange: (values: StrategyOptionsFormValues) => void
  values?: StrategyOptionsFormValues
  children?: React.ReactNode
}

export function StrategyOptionsForm({ onChange, values, children }: Props) {
  const form = useForm<StrategyOptionsFormValues>({
    resolver: zodResolver(formSchema),
    values,
  })

  useEffect(() => {
    const sub = form.watch((values) =>
      onChange(values as Required<StrategyOptionsFormValues>)
    )
    return () => sub.unsubscribe()
  }, [form, onChange])

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onChange)} className="space-y-2">
        <div className="p-2">
          <Tabs
            defaultValue={values?.strategy || "doubleSupertrend"}
            className="w-full"
            onValueChange={(val) =>
              form.setValue("strategy", val as Strategies)
            }
          >
            <TabsList className="grid w-full grid-cols-3">
              <TabsTrigger value="doubleSupertrend">
                Double Supertrend
              </TabsTrigger>
              <TabsTrigger value="supertrend">Supertrend</TabsTrigger>
              <TabsTrigger value="sniper">Sniper</TabsTrigger>
            </TabsList>
            <TabsContent value="doubleSupertrend">
              <DoubleSupertrend control={form.control} />
            </TabsContent>
            <TabsContent value="supertrend">
              <Supertrend control={form.control} />
            </TabsContent>
            <TabsContent value="sniper">
              <Sniper control={form.control} />
            </TabsContent>
          </Tabs>
        </div>
        {children}
      </form>
    </Form>
  )
}

import {
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Control } from "react-hook-form"
import { StrategyOptionsFormValues } from "./strategy-options-form"

type Props = {
  control: Control<StrategyOptionsFormValues>
}

export function GlobalTrendConfig({ control }: Props) {
  return (
    <div className="space-y-6">
      <h2 className="text-lg font-semibold">Global Trend Configuration</h2>
      <p className="text-sm text-muted-foreground">
        Параметры супер-тренда для более долгосрочной оценки тренда.
      </p>
      <div className="grid grid-cols-2 gap-4">
        <FormField
          control={control}
          name="global.period"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Period</FormLabel>
              <FormControl>
                <Input
                  type="number"
                  {...field}
                  onChange={(e) => field.onChange(parseInt(e.target.value))}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={control}
          name="global.multiplier"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Multiplier</FormLabel>
              <FormControl>
                <Input
                  type="number"
                  step="1"
                  {...field}
                  onChange={(e) => field.onChange(parseFloat(e.target.value))}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
      </div>
    </div>
  )
}
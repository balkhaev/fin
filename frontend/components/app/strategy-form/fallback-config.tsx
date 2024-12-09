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

export function FallbackConfig({ control }: Props) {
  return (
    <div className="space-y-6">
      <h2 className="text-lg font-semibold">Fallback Configuration</h2>
      <p className="text-sm text-muted-foreground">
        Настройки на случай недостаточного количества данных (свечей).
      </p>
      <div className="grid grid-cols-2 gap-4">
        <FormField
          control={control}
          name="fallback.periodSell"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Period (Sell)</FormLabel>
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
          name="fallback.multiplierSell"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Multiplier (Sell)</FormLabel>
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
        <FormField
          control={control}
          name="fallback.periodBuy"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Period (Buy)</FormLabel>
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
          name="fallback.multiplierBuy"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Multiplier (Buy)</FormLabel>
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
        <FormField
          control={control}
          name="fallback.minCandlesForDouble"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Min Candles for Double Strategy</FormLabel>
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
      </div>
    </div>
  )
}

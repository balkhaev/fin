import {
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { Control } from "react-hook-form"
import { StrategyOptionsFormValues } from "./strategy-options-form"

type Props = {
  control: Control<StrategyOptionsFormValues>
}

export function StrategySettings({ control }: Props) {
  return (
    <div className="space-y-6">
      <p className="border rounded-lg text-sm text-muted-foreground mt-6 p-3">
        Основные параметры стратегии, включая режимы, время обработки и условия
        выхода.
      </p>
      <FormField
        control={control}
        name="mode"
        render={({ field }) => (
          <FormItem className="space-y-3">
            <FormLabel>Mode</FormLabel>
            <FormControl>
              <RadioGroup
                onValueChange={field.onChange}
                defaultValue={field.value}
                className="flex flex-col space-y-1"
              >
                <FormItem className="flex items-center space-x-3 space-y-0">
                  <FormControl>
                    <RadioGroupItem value="sniper" />
                  </FormControl>
                  <FormLabel className="font-normal">Sniper</FormLabel>
                </FormItem>
                <FormItem className="flex items-center space-x-3 space-y-0">
                  <FormControl>
                    <RadioGroupItem value="test" />
                  </FormControl>
                  <FormLabel className="font-normal">Test</FormLabel>
                </FormItem>
                <FormItem className="flex items-center space-x-3 space-y-0">
                  <FormControl>
                    <RadioGroupItem value="prod" />
                  </FormControl>
                  <FormLabel className="font-normal">Production</FormLabel>
                </FormItem>
              </RadioGroup>
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />

      <div className="grid grid-cols-2 gap-4">
        <FormField
          control={control}
          name="takeProfit"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Take Profit (коэффициент)</FormLabel>
              <FormControl>
                <Input
                  type="number"
                  step="0.01"
                  {...field}
                  onChange={(e) => field.onChange(parseFloat(e.target.value))}
                />
              </FormControl>
              <FormMessage />
              <p className="text-xs text-muted-foreground">
                Значение 1.22 означает рост на 22% до фиксации прибыли.
              </p>
            </FormItem>
          )}
        />
        <FormField
          control={control}
          name="candlesMs"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Candle Duration (ms)</FormLabel>
              <FormControl>
                <Input
                  type="number"
                  {...field}
                  onChange={(e) => field.onChange(parseInt(e.target.value))}
                />
              </FormControl>
              <FormMessage />
              <p className="text-xs text-muted-foreground">
                Продолжительность одной свечи в миллисекундах.
              </p>
            </FormItem>
          )}
        />
        <FormField
          control={control}
          name="maxCoinTime"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Max Coin Time (ms)</FormLabel>
              <FormControl>
                <Input
                  type="number"
                  {...field}
                  onChange={(e) => field.onChange(parseInt(e.target.value))}
                />
              </FormControl>
              <FormMessage />
              <p className="text-xs text-muted-foreground">
                Максимальное время для реакции после появления монеты.
              </p>
            </FormItem>
          )}
        />
      </div>
    </div>
  )
}

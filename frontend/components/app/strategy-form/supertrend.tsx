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

export function Supertrend({ control }: Props) {
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-4">
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
                  className="flex gap-2"
                >
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

      <div className="space-y-4">
        <h3 className="text-lg font-semibold">Trend Configuration</h3>
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
    </div>
  )
}

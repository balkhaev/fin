import { Signal } from "../../types"

/**
 * Функция проверки пересечения сигналов
 */
export function checkSignalsCrossing(signals: Signal[]): Signal {
  const firstSignal = signals[0]
  const allEqual = signals.every((s) => s === firstSignal)

  if (!allEqual) return 0

  return firstSignal
}

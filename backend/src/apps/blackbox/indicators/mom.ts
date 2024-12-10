/**
 * Calculates the momentum indicator.
 *
 * Momentum indicator measures the rate of change of a particular asset's price over a specified period of time.
 * It is used to identify the direction and strength of a trend and signal potential trend reversals or
 * continuations.
 *
 * @param {number[]} source - The input array.
 * @param {number} period - The number of periods.
 * @param {number} [size=source.length] - The number of elements to process.
 * @returns {number[]} - The momentum values.
 */
export function mom(
  source: number[],
  period: number,
  size: number = source.length
): number[] {
  const output: number[] = []

  for (let i = period; i < size; ++i) {
    /**
     * Calculate the momentum by subtracting the value of the previous period
     * from the current value.
     */
    output.push(source[i] - source[i - period])
  }

  return output
}

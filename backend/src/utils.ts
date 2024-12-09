import { SnakeCase, SnakeCasedKeys } from "./types"

function toSnakeCase<S extends string>(str: S): SnakeCase<S> {
  return str
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .replace(/([A-Z])([A-Z][a-z])/g, "$1_$2")
    .replace(/([a-zA-Z])(\d+)/g, "$1_$2")
    .toLowerCase() as SnakeCase<S>
}

export function adaptKeys<T extends Record<string, any>>(
  obj: T
): SnakeCasedKeys<T> {
  const result = {} as any // Use 'any' to avoid TypeScript errors in assignment

  for (const key in obj) {
    if (Object.prototype.hasOwnProperty.call(obj, key)) {
      const newKey = toSnakeCase(key as string)
      result[newKey] = obj[key]
    }
  }

  return result as SnakeCasedKeys<T> // Cast the result to the correct type
}

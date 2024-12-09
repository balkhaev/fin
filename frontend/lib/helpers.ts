/* eslint-disable @typescript-eslint/no-explicit-any */
import { Database, Tables } from "@/database.types"

export type CamelCase<S extends string> =
  S extends `${infer P1}_${infer P2}${infer P3}`
    ? `${Lowercase<P1>}${Uppercase<P2>}${CamelCase<P3>}`
    : Lowercase<S>

type PublicSchema = Database[Extract<keyof Database, "public">]

type CamelCasedKeys<T> = {
  [K in keyof T as K extends string ? CamelCase<K> : K]: T[K]
}

export type AppTable<
  T extends keyof (PublicSchema["Tables"] & PublicSchema["Views"])
> = CamelCasedKeys<Tables<T>>

function toCamelCase<S extends string>(str: S): CamelCase<S> {
  return str.replace(/_([a-zA-Z0-9])/g, (_, letter) =>
    letter.toUpperCase()
  ) as CamelCase<S>
}

export function adaptKeys<T extends Record<string, any>>(
  obj: T
): CamelCasedKeys<T> {
  const result = {} as any
  for (const key in obj) {
    if (Object.prototype.hasOwnProperty.call(obj, key)) {
      const newKey = toCamelCase(key as string)
      result[newKey] = obj[key]
    }
  }
  return result as CamelCasedKeys<T>
}

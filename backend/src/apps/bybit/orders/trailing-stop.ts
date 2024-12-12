import { fetchCurrentPrice, createOrder } from "../sdk/methods"

interface Position {
  symbol: string
  side: "Buy" | "Sell"
  entryPrice: number
  qty: string // количество контрактов
}

// Пример реализации трейлинг стоп логики
async function trailingStopBot(position: Position, trailPercent: number) {
  let maxPrice = position.entryPrice
  let stopPrice = position.entryPrice * (1 - trailPercent) // если в лонге, стоп ниже, если в шорте - наоборот

  // Бесконечный цикл, где мы периодически обновляем цены
  while (true) {
    const currentPrice = await fetchCurrentPrice({ symbol: position.symbol })

    if (position.side === "Buy") {
      // Лонг позиция: если цена растёт, повышаем maxPrice
      if (currentPrice > maxPrice) {
        maxPrice = currentPrice
        stopPrice = maxPrice * (1 - trailPercent)
        console.log(`New max price: ${maxPrice}, stop moved to: ${stopPrice}`)
      }

      // Если текущая цена упала до стопа – закрываем позицию
      if (currentPrice <= stopPrice) {
        await createOrder({
          symbol: position.symbol,
          side: position.side === "Buy" ? "Sell" : "Buy",
          qty: position.qty,
          price: undefined,
          orderType: "Market",
        })
        break
      }
    } else {
      // Шорт позиция: логика обратная
      if (currentPrice < maxPrice) {
        // Для шорта maxPrice можно интерпретировать как минимальную достигнутую цену
        // Или можно переименовать в max/minPrice для ясности
        // Допустим maxPrice здесь - это наоборот minPrice для шорта
        // Тогда trailPercent надо применять как (1 + trailPercent) для шорта
      }
    }

    // Задержка перед следующей проверкой
    await new Promise((resolve) => setTimeout(resolve, 5000))
  }
}

const position = {
  symbol: "BTCUSDT",
  side: "Buy",
  entryPrice: 20000,
  qty: "1",
}

// Например, трейлинг 1% (0.01)
trailingStopBot(position, 0.01).catch(console.error)

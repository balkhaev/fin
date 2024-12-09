"use client"

import { Button } from "@/components/ui/button"
import { ButtonGroup } from "@/components/ui/button-group"

type TradeControlsProps = {
  working: boolean
  loading: boolean
  active: boolean
  onStop: (stopAll: boolean) => void
  onClear: () => void
  onStart: () => void
  onSell: () => void
}

export function TradeControls({
  working,
  loading,
  active,
  onStop,
  onClear,
  onStart,
  onSell,
}: TradeControlsProps) {
  return (
    <ButtonGroup>
      {working && (
        <Button
          variant={"secondary"}
          onClick={() => onStop(false)}
          disabled={loading}
        >
          Стоп
        </Button>
      )}
      <Button variant="outline" onClick={onClear} disabled={loading}>
        Очистить
      </Button>
      {working && (
        <Button variant="destructive" onClick={onSell} disabled={!active}>
          Продать!
        </Button>
      )}
      {!working && (
        <Button variant={"default"} onClick={onStart} disabled={loading}>
          Начать
        </Button>
      )}
    </ButtonGroup>
  )
}

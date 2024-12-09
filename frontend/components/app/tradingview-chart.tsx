// TradingViewWidget.jsx
import React, { useEffect, useRef, memo } from "react"

type Props = {
  symbol: string
}

function TradingViewChart({ symbol }: Props) {
  const container = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const script = document.createElement("script")
    script.src =
      "https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js"
    script.type = "text/javascript"
    script.async = true
    script.innerHTML = `
        {
          "autosize": true,
          "symbol": "${symbol}",
          "interval": "1",
          "timezone": "Etc/UTC",
          "theme": "dark",
          "style": "1",
          "locale": "ru",
          "allow_symbol_change": true,
          "save_image": false,
          "details": true,
          "calendar": false,
          "studies": [
            "STD;Awesome_Oscillator",
            "STD;Supertrend"
          ],
          "support_host": "https://www.tradingview.com"
        }`
    container.current?.appendChild(script)
  }, [])

  return (
    <div
      className="tradingview-widget-container"
      ref={container}
      style={{ height: "100%", width: "100%" }}
    >
      <div
        className="tradingview-widget-container__widget"
        style={{ height: "calc(100% - 32px)", width: "100%" }}
      ></div>
    </div>
  )
}

export default memo(TradingViewChart)

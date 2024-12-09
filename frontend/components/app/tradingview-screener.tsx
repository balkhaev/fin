import React, { useEffect, useRef, memo } from "react"

function TradingViewScreener() {
  const container = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const script = document.createElement("script")
    script.src =
      "https://s3.tradingview.com/external-embedding/embed-widget-screener.js"
    script.type = "text/javascript"
    script.async = true
    script.innerHTML = `
    {
      "width": "100%",
      "height": "100%",
      "defaultColumn": "overview",
      "defaultScreen": "top_gainers",
      "showToolbar": true,
      "locale": "en",
      "market": "crypto",
      "colorTheme": "light"
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

export default memo(TradingViewScreener)

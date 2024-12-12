/* eslint-disable @typescript-eslint/no-explicit-any */
import React from "react"
import { Table, TableBody, TableCell, TableRow } from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"

interface TechnicalAnalysisDisplayProps {
  ta1: any | undefined
  ta5: any | undefined
}

export default function TechnicalAnalysisDisplay({
  ta1,
  ta5,
}: TechnicalAnalysisDisplayProps) {
  if (!ta1 && !ta5) {
    return "Нет данных для анализа"
  }

  const formatValue = (value: number | null | undefined) =>
    typeof value === "number" ? value.toFixed(2) : "N/A"

  const getTrendBadge = (trend: string | undefined) => {
    const color =
      trend === "Bullish"
        ? "bg-green-500"
        : trend === "Bearish"
        ? "bg-red-500"
        : "bg-yellow-500"

    const text =
      trend === "Bullish" ? "Buy" : trend === "Bearish" ? "Sell" : "Hold"

    return <Badge className={color}>{text}</Badge>
  }

  return (
    <Table>
      <TableBody>
        <TableRow>
          <TableCell className="font-medium">Last Price</TableCell>
          <TableCell>{formatValue(ta1.lastPrice)}</TableCell>
          <TableCell>{formatValue(ta5?.lastPrice)}</TableCell>
        </TableRow>
        <TableRow>
          <TableCell className="font-medium">SMA</TableCell>
          <TableCell>{formatValue(ta1.sma)}</TableCell>
          <TableCell>{formatValue(ta5?.sma)}</TableCell>
        </TableRow>
        <TableRow>
          <TableCell className="font-medium">RSI</TableCell>
          <TableCell>{formatValue(ta1.rsi)}</TableCell>
          <TableCell>{formatValue(ta5?.rsi)}</TableCell>
        </TableRow>
        <TableRow>
          <TableCell className="font-medium">MACD</TableCell>
          <TableCell>
            {ta1.macd ? (
              <>
                <div>MACD: {formatValue(ta1.macd?.MACD)}</div>
                <div>Signal: {formatValue(ta1.macd?.signal)}</div>
                <div>Histogram: {formatValue(ta1.macd?.histogram)}</div>
              </>
            ) : (
              "N/A"
            )}
          </TableCell>
          <TableCell>
            {ta5?.macd ? (
              <>
                <div>MACD: {formatValue(ta5.macd?.MACD)}</div>
                <div>Signal: {formatValue(ta5.macd?.signal)}</div>
                <div>Histogram: {formatValue(ta5.macd?.histogram)}</div>
              </>
            ) : (
              "N/A"
            )}
          </TableCell>
        </TableRow>
        <TableRow>
          <TableCell className="font-medium">Bollinger Bands</TableCell>
          <TableCell>
            {ta1.bollingerBands ? (
              <>
                <div>Upper: {formatValue(ta1.bollingerBands?.upper)}</div>
                <div>Middle: {formatValue(ta1.bollingerBands?.middle)}</div>
                <div>Lower: {formatValue(ta1.bollingerBands?.lower)}</div>
              </>
            ) : (
              "N/A"
            )}
          </TableCell>
          <TableCell>
            {ta5?.bollingerBands ? (
              <>
                <div>Upper: {formatValue(ta5.bollingerBands?.upper)}</div>
                <div>Middle: {formatValue(ta5.bollingerBands?.middle)}</div>
                <div>Lower: {formatValue(ta5.bollingerBands?.lower)}</div>
              </>
            ) : (
              "N/A"
            )}
          </TableCell>
        </TableRow>
        <TableRow>
          <TableCell className="font-medium">Stochastic RSI</TableCell>
          <TableCell>
            {ta1.stochasticRsi ? (
              <>
                <div>K: {formatValue(ta1.stochasticRsi?.k)}</div>
                <div>D: {formatValue(ta1.stochasticRsi?.d)}</div>
              </>
            ) : (
              "N/A"
            )}
          </TableCell>
          <TableCell>
            {ta5?.stochasticRsi ? (
              <>
                <div>K: {formatValue(ta5.stochasticRsi?.k)}</div>
                <div>D: {formatValue(ta5.stochasticRsi?.d)}</div>
              </>
            ) : (
              "N/A"
            )}
          </TableCell>
        </TableRow>
        <TableRow>
          <TableCell className="font-medium">ADX</TableCell>
          <TableCell>{formatValue(ta1.adx)}</TableCell>
          <TableCell>{formatValue(ta5?.adx)}</TableCell>
        </TableRow>
        <TableRow>
          <TableCell className="font-medium">CCI</TableCell>
          <TableCell>{formatValue(ta1.cci)}</TableCell>
          <TableCell>{formatValue(ta5?.cci)}</TableCell>
        </TableRow>
        <TableRow>
          <TableCell className="font-medium">ATR</TableCell>
          <TableCell>{formatValue(ta1.atr)}</TableCell>
          <TableCell>{formatValue(ta5?.atr)}</TableCell>
        </TableRow>
        <TableRow>
          <TableCell className="font-medium">OBV</TableCell>
          <TableCell>{formatValue(ta1.obv)}</TableCell>
          <TableCell>{formatValue(ta5?.obv)}</TableCell>
        </TableRow>
        <TableRow>
          <TableCell className="font-medium">Momentum</TableCell>
          <TableCell>{formatValue(ta1.momentum)}</TableCell>
          <TableCell>{formatValue(ta5?.momentum)}</TableCell>
        </TableRow>
        <TableRow>
          <TableCell className="font-medium">Trend</TableCell>
          <TableCell>{getTrendBadge(ta1.trend)}</TableCell>
          <TableCell>{getTrendBadge(ta5?.trend)}</TableCell>
        </TableRow>
      </TableBody>
    </Table>
  )
}

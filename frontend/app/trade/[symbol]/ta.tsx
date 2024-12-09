import React from "react"
import { Card, CardContent } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableRow } from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"

interface TechnicalAnalysisDisplayProps {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  ta: any | undefined
}

export default function TechnicalAnalysisDisplay({
  ta,
}: TechnicalAnalysisDisplayProps) {
  if (!ta) {
    return (
      <Card className="w-full max-w-md">
        <CardContent>Нет данных для анализа</CardContent>
      </Card>
    )
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
    return <Badge className={color}>{trend || "Unknown"}</Badge>
  }

  return (
    <Table>
      <TableBody>
        <TableRow>
          <TableCell className="font-medium">Last Price</TableCell>
          <TableCell>{formatValue(ta.lastPrice)}</TableCell>
        </TableRow>
        <TableRow>
          <TableCell className="font-medium">SMA</TableCell>
          <TableCell>{formatValue(ta.sma)}</TableCell>
        </TableRow>
        <TableRow>
          <TableCell className="font-medium">RSI</TableCell>
          <TableCell>{formatValue(ta.rsi)}</TableCell>
        </TableRow>
        <TableRow>
          <TableCell className="font-medium">MACD</TableCell>
          <TableCell>
            {ta.macd ? (
              <>
                <div>MACD: {formatValue(ta.macd?.MACD)}</div>
                <div>Signal: {formatValue(ta.macd?.signal)}</div>
                <div>Histogram: {formatValue(ta.macd?.histogram)}</div>
              </>
            ) : (
              "N/A"
            )}
          </TableCell>
        </TableRow>
        <TableRow>
          <TableCell className="font-medium">Bollinger Bands</TableCell>
          <TableCell>
            {ta.bollingerBands ? (
              <>
                <div>Upper: {formatValue(ta.bollingerBands?.upper)}</div>
                <div>Middle: {formatValue(ta.bollingerBands?.middle)}</div>
                <div>Lower: {formatValue(ta.bollingerBands?.lower)}</div>
              </>
            ) : (
              "N/A"
            )}
          </TableCell>
        </TableRow>
        <TableRow>
          <TableCell className="font-medium">Stochastic RSI</TableCell>
          <TableCell>
            {ta.stochasticRsi ? (
              <>
                <div>K: {formatValue(ta.stochasticRsi?.k)}</div>
                <div>D: {formatValue(ta.stochasticRsi?.d)}</div>
              </>
            ) : (
              "N/A"
            )}
          </TableCell>
        </TableRow>
        <TableRow>
          <TableCell className="font-medium">ADX</TableCell>
          <TableCell>{formatValue(ta.adx)}</TableCell>
        </TableRow>
        <TableRow>
          <TableCell className="font-medium">CCI</TableCell>
          <TableCell>{formatValue(ta.cci)}</TableCell>
        </TableRow>
        <TableRow>
          <TableCell className="font-medium">ATR</TableCell>
          <TableCell>{formatValue(ta.atr)}</TableCell>
        </TableRow>
        <TableRow>
          <TableCell className="font-medium">OBV</TableCell>
          <TableCell>{formatValue(ta.obv)}</TableCell>
        </TableRow>
        <TableRow>
          <TableCell className="font-medium">Momentum</TableCell>
          <TableCell>{formatValue(ta.momentum)}</TableCell>
        </TableRow>
        <TableRow>
          <TableCell className="font-medium">Trend</TableCell>
          <TableCell>{getTrendBadge(ta.trend)}</TableCell>
        </TableRow>
      </TableBody>
    </Table>
  )
}

// Large candlestick chart with live price + entry/TP/SL horizontal level lines.
import {
  createChart,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  LineStyle,
} from "lightweight-charts";
import { useEffect, useRef } from "react";

export interface Candle {
  t: number; // unix seconds
  o: number;
  h: number;
  l: number;
  c: number;
}

export interface ChartLevels {
  price?: number;
  entry?: number;
  takeProfits?: number[];
  stops?: number[];
}

export default function BigChart({
  candles,
  levels,
  height = 420,
}: {
  candles: Candle[];
  levels?: ChartLevels;
  height?: number;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      height,
      layout: { background: { color: "transparent" }, textColor: "#7c8aa0" },
      grid: { vertLines: { color: "#1b2230" }, horzLines: { color: "#1b2230" } },
      rightPriceScale: { borderColor: "#222a36" },
      timeScale: { borderColor: "#222a36", timeVisible: true, secondsVisible: false },
      crosshair: { horzLine: { style: LineStyle.Dashed }, vertLine: { style: LineStyle.Dashed } },
    });
    const series = chart.addCandlestickSeries({
      upColor: "#16c784",
      downColor: "#ea3943",
      borderVisible: false,
      wickUpColor: "#16c784",
      wickDownColor: "#ea3943",
    });
    chartRef.current = chart;
    seriesRef.current = series;

    const onResize = () => chart.applyOptions({ width: containerRef.current?.clientWidth });
    onResize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      priceLinesRef.current = [];
    };
  }, [height]);

  useEffect(() => {
    if (!seriesRef.current || !candles.length) return;
    const dedup = new Map<number, Candle>();
    for (const b of candles) dedup.set(b.t, b);
    const data = [...dedup.values()]
      .sort((a, b) => a.t - b.t)
      .map((b) => ({ time: b.t as any, open: b.o, high: b.h, low: b.l, close: b.c }));
    seriesRef.current.setData(data);
    chartRef.current?.timeScale().fitContent();
  }, [candles]);

  // Horizontal level lines (price / entry / TP / SL).
  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;
    for (const pl of priceLinesRef.current) series.removePriceLine(pl);
    priceLinesRef.current = [];

    const add = (price: number | undefined, color: string, title: string, dashed = true) => {
      if (price == null || !isFinite(price)) return;
      const line = series.createPriceLine({
        price,
        color,
        lineWidth: 1,
        lineStyle: dashed ? LineStyle.Dashed : LineStyle.Solid,
        axisLabelVisible: true,
        title,
      });
      priceLinesRef.current.push(line);
    };

    add(levels?.price, "#9aa7b8", "PRICE", true);
    add(levels?.entry, "#3b82f6", "ENTRY", false);
    const tps = levels?.takeProfits ?? [];
    tps.forEach((tp, i) =>
      add(tp, "#16c784", tps.length > 1 ? `TP${i + 1}` : "TP", true),
    );
    const stops = levels?.stops ?? [];
    stops.forEach((sl, i) =>
      add(sl, "#ea3943", stops.length > 1 ? `SL${i + 1}` : "SL", true),
    );
  }, [
    levels?.price,
    levels?.entry,
    JSON.stringify(levels?.takeProfits ?? []),
    JSON.stringify(levels?.stops ?? []),
  ]);

  return <div ref={containerRef} className="w-full" />;
}

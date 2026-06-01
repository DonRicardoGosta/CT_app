// Equity / price line chart using TradingView lightweight-charts.
import { createChart, type IChartApi, type ISeriesApi, LineStyle } from "lightweight-charts";
import { useEffect, useRef } from "react";

export interface Point {
  time: number; // unix seconds
  value: number;
}

export default function EquityChart({ data, height = 280 }: { data: Point[]; height?: number }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Area"> | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      height,
      layout: { background: { color: "transparent" }, textColor: "#7c8aa0" },
      grid: {
        vertLines: { color: "#1b2230" },
        horzLines: { color: "#1b2230" },
      },
      rightPriceScale: { borderColor: "#222a36" },
      timeScale: { borderColor: "#222a36", timeVisible: true, secondsVisible: false },
      crosshair: { horzLine: { style: LineStyle.Dashed }, vertLine: { style: LineStyle.Dashed } },
    });
    const series = chart.addAreaSeries({
      lineColor: "#3b82f6",
      topColor: "rgba(59,130,246,0.4)",
      bottomColor: "rgba(59,130,246,0.02)",
      lineWidth: 2,
    });
    chartRef.current = chart;
    seriesRef.current = series;

    const handleResize = () => chart.applyOptions({ width: containerRef.current?.clientWidth });
    handleResize();
    window.addEventListener("resize", handleResize);
    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
    };
  }, [height]);

  useEffect(() => {
    if (!seriesRef.current) return;
    const dedup = new Map<number, number>();
    for (const p of data) dedup.set(p.time, p.value);
    const sorted = [...dedup.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([time, value]) => ({ time: time as any, value }));
    seriesRef.current.setData(sorted);
    chartRef.current?.timeScale().fitContent();
  }, [data]);

  return <div ref={containerRef} className="w-full" />;
}

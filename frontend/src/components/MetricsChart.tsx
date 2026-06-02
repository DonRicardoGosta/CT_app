// Multi-series line chart (one line per service) for CPU/RAM history.
import { createChart, type IChartApi, LineStyle } from "lightweight-charts";
import { useEffect, useRef } from "react";

export interface Series {
  name: string;
  color: string;
  points: { time: number; value: number }[];
}

export default function MetricsChart({
  series,
  height = 220,
  suffix = "",
}: {
  series: Series[];
  height?: number;
  suffix?: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      height,
      layout: { background: { color: "transparent" }, textColor: "#7c8aa0" },
      grid: { vertLines: { color: "#1b2230" }, horzLines: { color: "#1b2230" } },
      rightPriceScale: { borderColor: "#222a36" },
      timeScale: { borderColor: "#222a36", timeVisible: true, secondsVisible: false },
      crosshair: { horzLine: { style: LineStyle.Dashed }, vertLine: { style: LineStyle.Dashed } },
      localization: { priceFormatter: (p: number) => `${p.toFixed(0)}${suffix}` },
    });
    chartRef.current = chart;
    const handleResize = () => chart.applyOptions({ width: containerRef.current?.clientWidth });
    handleResize();
    window.addEventListener("resize", handleResize);
    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
      chartRef.current = null;
    };
  }, [height, suffix]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const created = series.map((s) => {
      const line = chart.addLineSeries({ color: s.color, lineWidth: 2 });
      const dedup = new Map<number, number>();
      for (const p of s.points) dedup.set(p.time, p.value);
      line.setData(
        [...dedup.entries()]
          .sort((a, b) => a[0] - b[0])
          .map(([time, value]) => ({ time: time as any, value })),
      );
      return line;
    });
    chart.timeScale().fitContent();
    return () => {
      for (const line of created) chart.removeSeries(line);
    };
  }, [series]);

  return <div ref={containerRef} className="w-full" />;
}

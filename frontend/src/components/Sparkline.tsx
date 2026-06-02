// Tiny line chart for the coin cards (no axes, just a trend line).
import { createChart, type IChartApi, type ISeriesApi } from "lightweight-charts";
import { useEffect, useRef } from "react";

export default function Sparkline({
  values,
  up = true,
  height = 44,
}: {
  values: number[];
  up?: boolean;
  height?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Line"> | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = createChart(ref.current, {
      height,
      layout: { background: { color: "transparent" }, textColor: "transparent" },
      grid: { vertLines: { visible: false }, horzLines: { visible: false } },
      rightPriceScale: { visible: false },
      leftPriceScale: { visible: false },
      timeScale: { visible: false },
      crosshair: { horzLine: { visible: false }, vertLine: { visible: false } },
      handleScroll: false,
      handleScale: false,
    });
    const series = chart.addLineSeries({ lineWidth: 2 });
    chartRef.current = chart;
    seriesRef.current = series;
    const onResize = () => chart.applyOptions({ width: ref.current?.clientWidth });
    onResize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.remove();
    };
  }, [height]);

  useEffect(() => {
    if (!seriesRef.current) return;
    seriesRef.current.applyOptions({ color: up ? "#16c784" : "#ea3943" });
    const data = values
      .map((v, i) => ({ time: (i + 1) as any, value: v }))
      .filter((p) => isFinite(p.value));
    seriesRef.current.setData(data);
    chartRef.current?.timeScale().fitContent();
  }, [values, up]);

  return <div ref={ref} className="w-full" />;
}

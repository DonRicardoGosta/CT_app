// Per-coin price chart with TP/SL horizontal lines (lightweight-charts).
import { createChart, type IChartApi, type ISeriesApi, LineStyle } from "lightweight-charts";
import { useEffect, useRef } from "react";

export interface OhlcBar {
  t: number;
  o: number;
  h: number;
  l: number;
  c: number;
}

export default function CoinChart({
  bars,
  stopLoss,
  takeProfit,
  height = 200,
}: {
  bars: OhlcBar[];
  stopLoss?: number;
  takeProfit?: number;
  height?: number;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const slRef = useRef<ISeriesApi<"Line"> | null>(null);
  const tpRef = useRef<ISeriesApi<"Line"> | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      height,
      layout: { background: { color: "transparent" }, textColor: "#7c8aa0" },
      grid: { vertLines: { color: "#1b2230" }, horzLines: { color: "#1b2230" } },
      timeScale: { timeVisible: true, secondsVisible: false },
    });
    const series = chart.addCandlestickSeries({
      upColor: "#16c784",
      downColor: "#ea3943",
      borderVisible: false,
      wickUpColor: "#16c784",
      wickDownColor: "#ea3943",
    });
    const sl = chart.addLineSeries({ color: "#ea3943", lineWidth: 1, lineStyle: LineStyle.Dashed });
    const tp = chart.addLineSeries({ color: "#16c784", lineWidth: 1, lineStyle: LineStyle.Dashed });
    chartRef.current = chart;
    seriesRef.current = series;
    slRef.current = sl;
    tpRef.current = tp;

    const onResize = () => chart.applyOptions({ width: containerRef.current?.clientWidth });
    onResize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.remove();
    };
  }, [height]);

  useEffect(() => {
    if (!seriesRef.current || !bars.length) return;
    const dedup = new Map<number, OhlcBar>();
    for (const b of bars) dedup.set(b.t, b);
    const data = [...dedup.values()]
      .sort((a, b) => a.t - b.t)
      .map((b) => ({
        time: b.t as any,
        open: b.o,
        high: b.h,
        low: b.l,
        close: b.c,
      }));
    seriesRef.current.setData(data);
    chartRef.current?.timeScale().fitContent();

    const t0 = data[0]?.time;
    const t1 = data[data.length - 1]?.time;
    if (t0 != null && t1 != null && stopLoss != null && slRef.current) {
      slRef.current.setData([
        { time: t0, value: stopLoss },
        { time: t1, value: stopLoss },
      ]);
    }
    if (t0 != null && t1 != null && takeProfit != null && tpRef.current) {
      tpRef.current.setData([
        { time: t0, value: takeProfit },
        { time: t1, value: takeProfit },
      ]);
    }
  }, [bars, stopLoss, takeProfit]);

  return <div ref={containerRef} className="w-full" />;
}

// Shows the 5 selected coins and per-coin plan (TP/SL, ladder, chart).
import { useRealtime, type CoinPlanRow } from "@/store/realtime";
import { Badge, Card, CardTitle, Empty } from "@/components/ui";
import CoinChart, { type OhlcBar } from "@/components/CoinChart";
import { num, usd } from "@/lib/format";

function parseBars(raw: unknown): OhlcBar[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((b: any) => ({
      t: Number(b.t),
      o: parseFloat(b.o),
      h: parseFloat(b.h),
      l: parseFloat(b.l),
      c: parseFloat(b.c),
    }))
    .filter((b) => b.t > 0 && isFinite(b.c));
}

function CoinCard({ coin, leverage }: { coin: CoinPlanRow; leverage: number }) {
  const bars = parseBars(coin.bars);
  const sl = parseFloat(String(coin.stop_loss_price));
  const tp = parseFloat(String(coin.take_profit_price));
  const tone = coin.trend === "bull" ? "up" : coin.trend === "bear" ? "down" : "muted";
  const openTone =
    coin.open_status === "open_now"
      ? "up"
      : coin.open_status === "wait_spacing"
        ? "warn"
        : "muted";

  return (
    <Card className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-lg font-semibold">{coin.symbol}</span>
          <Badge tone={tone}>{coin.trend}</Badge>
          <Badge tone="accent">{coin.direction}</Badge>
        </div>
        <span className="num text-xl font-semibold">{usd(coin.price)}</span>
      </div>

      <CoinChart
        bars={bars}
        stopLoss={isFinite(sl) ? sl : undefined}
        takeProfit={isFinite(tp) ? tp : undefined}
        height={220}
      />

      <div className="grid grid-cols-2 gap-2 text-sm lg:grid-cols-4">
        <div>
          <div className="text-xs text-muted">Stop loss (price)</div>
          <div className="num text-down">{usd(coin.stop_loss_price)}</div>
          <div className="text-xs text-muted">{coin.stop_loss_pct_margin}% on margin @ {leverage}x</div>
        </div>
        <div>
          <div className="text-xs text-muted">Take profit (price)</div>
          <div className="num text-up">{usd(coin.take_profit_price)}</div>
          <div className="text-xs text-muted">{coin.take_profit_pct_margin}% on margin @ {leverage}x</div>
        </div>
        <div>
          <div className="text-xs text-muted">Ladder</div>
          <div className="num">
            {coin.ladder_step} / {coin.ladder_max}
          </div>
        </div>
        <div>
          <div className="text-xs text-muted">When to open</div>
          <Badge tone={openTone}>{coin.open_status}</Badge>
          <div className="mt-1 text-xs text-muted">{coin.next_open_reason}</div>
          {coin.open_status === "wait_spacing" && (
            <div className="num text-xs">trigger @ {usd(coin.next_open_price)}</div>
          )}
        </div>
      </div>

      {parseFloat(String(coin.position_qty)) > 0 && (
        <div className="rounded-lg border border-border bg-panel2 px-3 py-2 text-sm">
          Open position: {num(coin.position_qty, 4)} @ {usd(coin.entry_price)} (entry)
        </div>
      )}
    </Card>
  );
}

export default function StrategyPlanView() {
  const plan = useRealtime((s) => s.strategyPlan);
  if (!plan) {
    return (
      <Card>
        <CardTitle>Coin plan</CardTitle>
        <Empty>Start a dry-run or live run to see selected coins, TP/SL and charts.</Empty>
      </Card>
    );
  }

  const symbols = plan.selected_symbols ?? [];
  const coins = plan.coins ?? [];
  const lev = plan.leverage ?? 1;

  return (
    <div className="space-y-4">
      <Card>
        <CardTitle
          right={
            <Badge tone="accent">
              {symbols.length} coins · {lev}x
            </Badge>
          }
        >
          Selected coins
        </CardTitle>
        <div className="flex flex-wrap gap-2">
          {symbols.map((s) => (
            <Badge key={s} tone="accent">
              {s}
            </Badge>
          ))}
        </div>
        <p className="mt-2 text-xs text-muted">
          TP/SL % are on margin (ROE). At {lev}x leverage the price distance is divided by {lev}.
          SL {plan.stop_loss_pct_margin}% · TP {plan.take_profit_pct_margin}% on margin.
        </p>
      </Card>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {coins.map((c) => (
          <CoinCard key={c.symbol} coin={c} leverage={lev} />
        ))}
      </div>
    </div>
  );
}

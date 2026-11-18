// The overview's deeper charts (GET /api/insights): when people play, the house's result, where the credits came
// from, players per slot, going broke, bet sizes, how equal the wealth is, and record boards.
import * as React from "react"
import { useQuery } from "@tanstack/react-query"
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  LineChart,
  ReferenceLine,
  XAxis,
  YAxis,
} from "recharts"

import { api, type Guild, type Insights, type People, type Period, type PlayerTotal, type Round } from "@/app/api"
import { ago, compact, credits, gameColor, gameName, moneyName, pct, signed } from "@/app/format"
import { Empty, Heatmap, share, slotTick, slotTitle, times, Tip, TipRow, Who } from "@/app/stats-kit"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { ChartContainer, ChartTooltip, type ChartConfig } from "@/components/ui/chart"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { cn } from "@/lib/utils"

const PERIOD_WORDS: Record<Period, string> = { "24h": "the last 24 hours", "7d": "the last 7 days", "30d": "the last 30 days", all: "all time" }

export function OverviewExtras({ guild, period }: { guild: Guild; period: Period }) {
  const q = useQuery({
    queryKey: ["insights", guild.id, period],
    queryFn: () => api.get<Insights>(`/api/insights?guild=${guild.id}&period=${period}`),
    refetchInterval: 60_000,
  })
  const d = q.data
  const per = d?.bucket === "hour" ? "hour" : "day"

  return (
    <>
      {q.isError ? <p className="text-destructive text-sm">{String(q.error.message)}</p> : null}

      <section className="grid items-start gap-4 xl:grid-cols-[1.4fr_1fr] 3xl:grid-cols-[1.4fr_1fr_1fr]">
        <Card>
          <HouseHeader per={per} />
          <CardContent>{d ? <HouseChart d={d} /> : <Skeleton className="h-[260px] w-full" />}</CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Where the credits came from</CardTitle>
            <CardDescription>From the total at the start of the period to the total now, step by step.</CardDescription>
          </CardHeader>
          <CardContent>{d ? <FlowChart flow={d.flow} /> : <Skeleton className="h-[260px] w-full" />}</CardContent>
        </Card>
        <Card className="xl:col-span-2 3xl:col-span-1">
          <CardHeader>
            <CardTitle>When people play</CardTitle>
            <CardDescription>
              Games by weekday and hour,{" "}
              {d ? (period === "24h" || period === "7d" ? "over the last 7 days" : `over ${PERIOD_WORDS[period]}`) : "…"}.
            </CardDescription>
          </CardHeader>
          <CardContent>{d ? <HeatmapCard cells={d.heatmap.cells} /> : <Skeleton className="h-[200px] w-full" />}</CardContent>
        </Card>
      </section>

      <section className="grid items-start gap-4 lg:grid-cols-2 3xl:grid-cols-4">
        <Card>
          <CardHeader>
            <CardTitle>Players per {per}</CardTitle>
            <CardDescription>Players who played, with first-timers on top.</CardDescription>
          </CardHeader>
          <CardContent>{d ? <PlayersChart d={d} /> : <Skeleton className="h-[220px] w-full" />}</CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Going broke</CardTitle>
            <CardDescription>Restarts per {per}: a restart is only possible with an empty balance.</CardDescription>
          </CardHeader>
          <CardContent>{d ? <BrokeCard d={d} /> : <Skeleton className="h-[220px] w-full" />}</CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Bet sizes</CardTitle>
            <CardDescription>How many bets of each size, and how much of the money they make up.</CardDescription>
          </CardHeader>
          <CardContent>{d ? <BetsChart bets={d.bets} /> : <Skeleton className="h-[220px] w-full" />}</CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>How equal the wealth is</CardTitle>
            <CardDescription>The richest players' share of all credits held, per {per}.</CardDescription>
          </CardHeader>
          <CardContent>{d ? <WealthChart d={d} /> : <Skeleton className="h-[220px] w-full" />}</CardContent>
        </Card>
      </section>

      <Card>
        <CardHeader>
          <CardTitle>Records</CardTitle>
          <CardDescription>The biggest wins and losses and the best and worst players of {PERIOD_WORDS[period]}.</CardDescription>
        </CardHeader>
        <CardContent>{d ? <Records d={d} /> : <Skeleton className="h-[260px] w-full" />}</CardContent>
      </Card>
    </>
  )
}

// ------------------------------------------------------------------ house result

function HouseHeader({ per }: { per: string }) {
  return (
    <CardHeader>
      <CardTitle>House result</CardTitle>
      <CardDescription>
        What the casino won (green) or paid out more than it took in (red) per {per}, and the running total.
      </CardDescription>
    </CardHeader>
  )
}

const houseConfig = {
  house: { label: "House", color: "var(--success)" },
  total: { label: "Running total", color: "var(--foreground)" },
} satisfies ChartConfig

function HouseChart({ d }: { d: Insights }) {
  const [split, setSplit] = React.useState(false)
  const games = React.useMemo(() => {
    const sum: Record<string, number> = {}
    for (const s of d.timeline) for (const [g, v] of Object.entries(s.by_game)) sum[g] = (sum[g] ?? 0) + Math.abs(v)
    return Object.keys(sum).sort((a, b) => sum[b] - sum[a])
  }, [d])
  const data = React.useMemo(() => {
    let total = 0
    return d.timeline.map((s) => {
      const house = s.wagered - s.paid
      total += house
      const row: Record<string, number> = { t: s.t, house, total, wagered: s.wagered, paid: s.paid, games: s.games }
      for (const g of games) row[`g_${g}`] = s.by_game[g] ?? 0
      return row
    })
  }, [d, games])
  const sum = data.length ? data[data.length - 1].total : 0
  if (!d.timeline.some((s) => s.wagered)) return <Empty>No games in this period.</Empty>
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-xs">
          <span className="text-muted-foreground">Total: </span>
          <span className={cn("tabular font-medium", sum >= 0 ? "text-success" : "text-error")}>{signed(sum)}</span>
        </div>
        <ToggleGroup type="single" size="sm" variant="outline" value={split ? "game" : "total"} onValueChange={(v) => v && setSplit(v === "game")}>
          <ToggleGroupItem value="total" className="h-6 px-2 text-[11px]">Total</ToggleGroupItem>
          <ToggleGroupItem value="game" className="h-6 px-2 text-[11px]">By game</ToggleGroupItem>
        </ToggleGroup>
      </div>
      <ChartContainer config={houseConfig} className="aspect-auto h-[240px] w-full">
        <ComposedChart data={data} stackOffset="sign" margin={{ left: 0, right: 8, top: 8 }}>
          <CartesianGrid vertical={false} strokeDasharray="3 3" />
          <XAxis dataKey="t" tickFormatter={(t) => slotTick(t, d.bucket)} tickLine={false} axisLine={false} minTickGap={28} fontSize={11} />
          <YAxis yAxisId="bars" tickFormatter={(v) => compact(v)} tickLine={false} axisLine={false} width={48} fontSize={11} />
          <YAxis yAxisId="total" orientation="right" hide />
          <ReferenceLine yAxisId="bars" y={0} stroke="var(--border)" />
          <ChartTooltip
            cursor={{ fill: "var(--muted)", opacity: 0.4 }}
            content={({ payload }) => {
              const r = payload?.[0]?.payload as Record<string, number> | undefined
              if (!r) return null
              const parts = split ? games.filter((g) => r[`g_${g}`]).sort((a, b) => Math.abs(r[`g_${b}`]) - Math.abs(r[`g_${a}`])).slice(0, 8) : []
              return (
                <Tip title={slotTitle(r.t, d.bucket)}>
                  <TipRow label="Wagered" value={credits(r.wagered)} />
                  <TipRow label="Paid back" value={credits(r.paid)} />
                  <TipRow color={r.house >= 0 ? "var(--success)" : "var(--error)"} label="House" value={signed(r.house)} />
                  {parts.map((g) => (
                    <TipRow key={g} color={gameColor(g)} label={gameName(g)} value={signed(r[`g_${g}`])} />
                  ))}
                  <div className="border-t pt-1" />
                  <TipRow label="Running total" value={signed(r.total)} />
                </Tip>
              )
            }}
          />
          {split ? (
            games.map((g) => <Bar key={g} yAxisId="bars" dataKey={`g_${g}`} stackId="g" fill={gameColor(g)} isAnimationActive={false} />)
          ) : (
            <Bar yAxisId="bars" dataKey="house" radius={[2, 2, 0, 0]} isAnimationActive={false}>
              {data.map((r) => (
                <Cell key={r.t} fill={r.house >= 0 ? "var(--success)" : "var(--error)"} />
              ))}
            </Bar>
          )}
          <Line yAxisId="total" dataKey="total" type="monotone" stroke="var(--foreground)" strokeOpacity={0.7} strokeWidth={1.5} dot={false} isAnimationActive={false} />
        </ComposedChart>
      </ChartContainer>
      <p className="text-muted-foreground text-[11px]">The line is the running total (its own scale).</p>
    </div>
  )
}

// ------------------------------------------------------------------ where the credits came from

const FLOW_NAMES: Record<string, string> = { games: "Game results", other: "In play & other" }
// short names under the bars
const FLOW_SHORT: Record<string, string> = {
  start: "Start", end: "Now", restart: "Restarts", claim: "Claims", admin: "Admin", shop: "Shop", games: "Games",
  other: "Other", giveall: "Give all", removeall: "Remove all",
}

function FlowChart({ flow }: { flow: Insights["flow"] }) {
  const data = React.useMemo(() => {
    const rows: { name: string; short?: string; base: number; value: number; amount: number; kind: string; n: number | null; total?: boolean }[] = []
    rows.push({ name: "Start", base: 0, value: flow.start, amount: flow.start, kind: "start", n: null, total: true })
    let level = flow.start
    for (const s of flow.steps) {
      const next = level + s.amount
      rows.push({ name: FLOW_NAMES[s.kind] ?? moneyName(s.kind), base: Math.min(level, next), value: Math.abs(s.amount), amount: s.amount, kind: s.kind, n: s.n })
      level = next
    }
    rows.push({ name: "Now", base: 0, value: flow.end, amount: flow.end, kind: "end", n: null, total: true })
    return rows.map((r) => ({ ...r, short: FLOW_SHORT[r.kind] ?? r.name }))
  }, [flow])
  return (
    <div className="flex flex-col gap-2">
      <ChartContainer config={{}} className="aspect-auto h-[240px] w-full">
        <BarChart data={data} margin={{ left: 0, right: 8, top: 8 }}>
          <CartesianGrid vertical={false} strokeDasharray="3 3" />
          <XAxis dataKey="short" tickLine={false} axisLine={false} interval={0} fontSize={10} angle={-35} textAnchor="end" height={44} />
          <YAxis tickFormatter={(v) => compact(v)} tickLine={false} axisLine={false} width={48} fontSize={11} />
          <ChartTooltip
            cursor={{ fill: "var(--muted)", opacity: 0.4 }}
            content={({ payload }) => {
              const r = payload?.[0]?.payload as (typeof data)[number] | undefined
              if (!r) return null
              return (
                <Tip title={r.name}>
                  {r.total ? (
                    <TipRow label="Credits held" value={credits(r.amount)} />
                  ) : (
                    <>
                      <TipRow color={r.amount >= 0 ? "var(--success)" : "var(--error)"} label={r.amount >= 0 ? "Added" : "Removed"} value={signed(r.amount)} />
                      {r.n ? <TipRow label="Times" value={credits(r.n)} /> : null}
                      {r.kind === "games" ? <p className="text-muted-foreground max-w-56">What players won from games minus what they bet.</p> : null}
                      {r.kind === "other" ? <p className="text-muted-foreground max-w-56">Bets still on the table and anything the history doesn't cover.</p> : null}
                    </>
                  )}
                </Tip>
              )
            }}
          />
          <Bar dataKey="base" stackId="w" fill="transparent" isAnimationActive={false} />
          <Bar dataKey="value" stackId="w" radius={[2, 2, 0, 0]} isAnimationActive={false}>
            {data.map((r) => (
              <Cell key={r.name} fill={r.total ? "var(--chart-1)" : r.amount >= 0 ? "var(--success)" : "var(--error)"} />
            ))}
          </Bar>
        </BarChart>
      </ChartContainer>
      <div className="grid gap-x-4 gap-y-1 text-[11px] sm:grid-cols-2">
        {data.slice(1, -1).map((r) => (
          <div key={r.name} className="flex justify-between gap-2">
            <span className="text-muted-foreground truncate">{r.name}</span>
            <span className={cn("tabular", r.amount >= 0 ? "text-success" : "text-error")}>{signed(r.amount)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ heatmap

function HeatmapCard({ cells }: { cells: Insights["heatmap"]["cells"] }) {
  const [metric, setMetric] = React.useState<"games" | "wagered">("games")
  if (!cells.length) return <Empty>No games yet.</Empty>
  return (
    <div className="flex flex-col gap-2">
      <div className="flex justify-end">
        <ToggleGroup type="single" size="sm" variant="outline" value={metric} onValueChange={(v) => v && setMetric(v as "games" | "wagered")}>
          <ToggleGroupItem value="games" className="h-6 px-2 text-[11px]">Games</ToggleGroupItem>
          <ToggleGroupItem value="wagered" className="h-6 px-2 text-[11px]">Wagered</ToggleGroupItem>
        </ToggleGroup>
      </div>
      <Heatmap cells={cells} metric={metric} />
    </div>
  )
}

// ------------------------------------------------------------------ players

const playersConfig = {
  returning: { label: "Returning", color: "var(--chart-1)" },
  new_players: { label: "New", color: "var(--chart-3)" },
} satisfies ChartConfig

function PlayersChart({ d }: { d: Insights }) {
  const data = d.timeline.map((s) => ({ t: s.t, returning: Math.max(0, s.players - s.new_players), new_players: s.new_players, players: s.players }))
  const newTotal = d.timeline.reduce((a, s) => a + s.new_players, 0)
  const peak = Math.max(0, ...d.timeline.map((s) => s.players))
  return (
    <div className="flex flex-col gap-2">
      <div className="flex gap-4 text-xs">
        <span><span className="text-muted-foreground">Busiest: </span><span className="tabular font-medium">{peak}</span></span>
        <span><span className="text-muted-foreground">New players: </span><span className="tabular font-medium">{newTotal}</span></span>
      </div>
      <ChartContainer config={playersConfig} className="aspect-auto h-[200px] w-full">
        <BarChart data={data} margin={{ left: 0, right: 8, top: 8 }}>
          <CartesianGrid vertical={false} strokeDasharray="3 3" />
          <XAxis dataKey="t" tickFormatter={(t) => slotTick(t, d.bucket)} tickLine={false} axisLine={false} minTickGap={28} fontSize={11} />
          <YAxis allowDecimals={false} tickLine={false} axisLine={false} width={28} fontSize={11} />
          <ChartTooltip
            cursor={{ fill: "var(--muted)", opacity: 0.4 }}
            content={({ payload }) => {
              const r = payload?.[0]?.payload as (typeof data)[number] | undefined
              if (!r) return null
              return (
                <Tip title={slotTitle(r.t, d.bucket)}>
                  <TipRow label="Players" value={r.players} />
                  <TipRow color="var(--chart-3)" label="First time" value={r.new_players} />
                </Tip>
              )
            }}
          />
          <Bar dataKey="returning" stackId="p" fill="var(--color-returning)" isAnimationActive={false} />
          <Bar dataKey="new_players" stackId="p" fill="var(--color-new_players)" radius={[2, 2, 0, 0]} isAnimationActive={false} />
        </BarChart>
      </ChartContainer>
    </div>
  )
}

// ------------------------------------------------------------------ going broke

function BrokeCard({ d }: { d: Insights }) {
  const data = d.timeline.map((s) => ({ t: s.t, restarts: s.restarts, credits: s.restart_credits }))
  const total = d.timeline.reduce((a, s) => a + s.restarts, 0)
  const given = d.timeline.reduce((a, s) => a + s.restart_credits, 0)
  if (!total) return <Empty>Nobody went broke in this period.</Empty>
  return (
    <div className="flex flex-col gap-3">
      <div className="flex gap-4 text-xs">
        <span><span className="text-muted-foreground">Restarts: </span><span className="tabular font-medium">{credits(total)}</span></span>
        <span><span className="text-muted-foreground">Credits handed out: </span><span className="tabular font-medium">{compact(given)}</span></span>
      </div>
      <ChartContainer config={{ restarts: { label: "Restarts", color: "var(--error)" } }} className="aspect-auto h-[120px] w-full">
        <BarChart data={data} margin={{ left: 0, right: 8, top: 4 }}>
          <XAxis dataKey="t" tickFormatter={(t) => slotTick(t, d.bucket)} tickLine={false} axisLine={false} minTickGap={28} fontSize={10} />
          <YAxis allowDecimals={false} tickLine={false} axisLine={false} width={28} fontSize={10} />
          <ChartTooltip
            cursor={{ fill: "var(--muted)", opacity: 0.4 }}
            content={({ payload }) => {
              const r = payload?.[0]?.payload as (typeof data)[number] | undefined
              if (!r) return null
              return (
                <Tip title={slotTitle(r.t, d.bucket)}>
                  <TipRow color="var(--error)" label="Restarts" value={r.restarts} />
                  <TipRow label="Credits handed out" value={credits(r.credits)} />
                </Tip>
              )
            }}
          />
          <Bar dataKey="restarts" fill="var(--color-restarts)" radius={[2, 2, 0, 0]} isAnimationActive={false} />
        </BarChart>
      </ChartContainer>
      <ul className="flex flex-col gap-1.5">
        {d.broke.slice(0, 5).map((b) => (
          <li key={b.user_id} className="grid grid-cols-[1fr_auto_auto] items-center gap-3 text-xs">
            <Who people={d.people} id={b.user_id} />
            <span className="tabular">{credits(b.n)}×</span>
            <span className="text-muted-foreground w-14 text-right">{ago(b.last)}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

// ------------------------------------------------------------------ bet sizes

function betLabel(lo: number, hi: number | null) {
  if (lo <= 1 && hi) return `< ${compact(hi)}`
  return hi ? `${compact(lo)}–${compact(hi)}` : `${compact(lo)}+`
}

function BetsChart({ bets }: { bets: Insights["bets"] }) {
  const [metric, setMetric] = React.useState<"rounds" | "wagered">("rounds")
  const totalRounds = bets.reduce((a, b) => a + b.rounds, 0)
  const totalWagered = bets.reduce((a, b) => a + b.wagered, 0)
  const data = bets.map((b) => ({ ...b, label: betLabel(b.lo, b.hi) }))
  if (!totalRounds) return <Empty>No bets in this period.</Empty>
  return (
    <div className="flex flex-col gap-2">
      <div className="flex justify-end">
        <ToggleGroup type="single" size="sm" variant="outline" value={metric} onValueChange={(v) => v && setMetric(v as "rounds" | "wagered")}>
          <ToggleGroupItem value="rounds" className="h-6 px-2 text-[11px]">Bets</ToggleGroupItem>
          <ToggleGroupItem value="wagered" className="h-6 px-2 text-[11px]">Credits</ToggleGroupItem>
        </ToggleGroup>
      </div>
      <ChartContainer config={{ v: { label: "Bets", color: "var(--chart-4)" } }} className="aspect-auto h-[200px] w-full">
        <BarChart data={data} margin={{ left: 0, right: 8, top: 8 }}>
          <CartesianGrid vertical={false} strokeDasharray="3 3" />
          <XAxis dataKey="label" tickLine={false} axisLine={false} interval={0} fontSize={9} />
          <YAxis tickFormatter={(v) => compact(v)} tickLine={false} axisLine={false} width={40} fontSize={11} />
          <ChartTooltip
            cursor={{ fill: "var(--muted)", opacity: 0.4 }}
            content={({ payload }) => {
              const r = payload?.[0]?.payload as (typeof data)[number] | undefined
              if (!r) return null
              return (
                <Tip title={`Bets of ${r.label}`}>
                  <TipRow label="Bets" value={`${credits(r.rounds)} (${pct(r.rounds, totalRounds, 0)})`} />
                  <TipRow label="Credits" value={`${compact(r.wagered)} (${pct(r.wagered, totalWagered, 0)})`} />
                </Tip>
              )
            }}
          />
          <Bar dataKey={metric} fill="var(--chart-4)" radius={[2, 2, 0, 0]} isAnimationActive={false} />
        </BarChart>
      </ChartContainer>
    </div>
  )
}

// ------------------------------------------------------------------ wealth

const wealthConfig = {
  top10: { label: "Top 10%", color: "var(--chart-1)" },
  top1: { label: "Richest player", color: "var(--chart-3)" },
} satisfies ChartConfig

function WealthChart({ d }: { d: Insights }) {
  const data = d.wealth.filter((w) => w.top10 != null).map((w) => ({ ...w, top10p: (w.top10 ?? 0) * 100, top1p: (w.top1 ?? 0) * 100 }))
  const last = data[data.length - 1]
  if (!last) return <Empty>No balances yet.</Empty>
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
        <span title={`The richest ${last.top_n} of ${last.accounts} accounts`}>
          <span className="text-muted-foreground">Top 10% hold: </span>
          <span className="tabular font-medium">{share(last.top10)}</span>
        </span>
        <span>
          <span className="text-muted-foreground">Richest: </span>
          <span className="tabular font-medium">{share(last.top1)}</span>
        </span>
        <span title="0 means everyone holds the same, 1 means one player holds everything">
          <span className="text-muted-foreground">Gini: </span>
          <span className="tabular font-medium">{last.gini?.toFixed(2)}</span>
        </span>
      </div>
      <ChartContainer config={wealthConfig} className="aspect-auto h-[200px] w-full">
        <LineChart data={data} margin={{ left: 0, right: 8, top: 8 }}>
          <CartesianGrid vertical={false} strokeDasharray="3 3" />
          <XAxis dataKey="t" tickFormatter={(t) => slotTick(t, d.bucket)} tickLine={false} axisLine={false} minTickGap={28} fontSize={11} />
          <YAxis domain={[0, 100]} tickFormatter={(v) => `${v}%`} tickLine={false} axisLine={false} width={36} fontSize={11} />
          <ChartTooltip
            content={({ payload }) => {
              const r = payload?.[0]?.payload as (typeof data)[number] | undefined
              if (!r) return null
              return (
                <Tip title={slotTitle(r.t, d.bucket)}>
                  <TipRow color="var(--chart-1)" label={`Top 10% (${r.top_n})`} value={share(r.top10, 1)} />
                  <TipRow color="var(--chart-3)" label="Richest player" value={share(r.top1, 1)} />
                  <TipRow label="Gini" value={r.gini?.toFixed(3)} />
                  <TipRow label="Accounts" value={r.accounts} />
                </Tip>
              )
            }}
          />
          <Line dataKey="top10p" type="monotone" stroke="var(--color-top10)" strokeWidth={1.75} dot={false} isAnimationActive={false} />
          <Line dataKey="top1p" type="monotone" stroke="var(--color-top1)" strokeWidth={1.5} strokeDasharray="4 3" dot={false} isAnimationActive={false} />
        </LineChart>
      </ChartContainer>
    </div>
  )
}

// ------------------------------------------------------------------ records

function Records({ d }: { d: Insights }) {
  const r = d.records
  return (
    <Tabs defaultValue="wins" className="gap-3">
      <TabsList className="flex h-auto flex-wrap">
        <TabsTrigger value="wins">Biggest wins</TabsTrigger>
        <TabsTrigger value="multipliers">Biggest multipliers</TabsTrigger>
        <TabsTrigger value="losses">Biggest losses</TabsTrigger>
        <TabsTrigger value="winners">Top winners</TabsTrigger>
        <TabsTrigger value="losers">Top losers</TabsTrigger>
        <TabsTrigger value="luckiest">Luckiest</TabsTrigger>
        <TabsTrigger value="unluckiest">Unluckiest</TabsTrigger>
      </TabsList>
      <TabsContent value="wins"><RoundList rows={r.wins} people={d.people} show="win" /></TabsContent>
      <TabsContent value="multipliers"><RoundList rows={r.multipliers} people={d.people} show="multiplier" /></TabsContent>
      <TabsContent value="losses"><RoundList rows={r.losses} people={d.people} show="loss" /></TabsContent>
      <TabsContent value="winners"><PlayerList rows={r.winners} people={d.people} /></TabsContent>
      <TabsContent value="losers"><PlayerList rows={r.losers} people={d.people} /></TabsContent>
      <TabsContent value="luckiest">
        <PlayerList rows={r.luckiest} people={d.people} luck note={`Highest payback, with at least ${r.min_games} games.`} />
      </TabsContent>
      <TabsContent value="unluckiest">
        <PlayerList rows={r.unluckiest} people={d.people} luck note={`Lowest payback, with at least ${r.min_games} games.`} />
      </TabsContent>
    </Tabs>
  )
}

export function RoundList({ rows, people, show }: { rows: Round[]; people: People; show: "win" | "multiplier" | "loss" }) {
  if (!rows.length) return <Empty className="py-6">Nothing in this period.</Empty>
  return (
    <ol className="flex flex-col">
      {rows.map((r, i) => {
        const net = r.payout - r.wagered
        return (
          <li key={`${r.user_id}-${r.ts}-${i}`} className="grid grid-cols-[1.25rem_minmax(0,1fr)_6.5rem] items-center gap-3 border-b py-1.5 text-xs last:border-0 sm:grid-cols-[1.25rem_minmax(0,1.4fr)_minmax(0,1fr)_11rem_7rem]">
            <span className="text-muted-foreground tabular">{i + 1}</span>
            <Who people={people} id={r.user_id} />
            <span className="hidden min-w-0 items-center gap-1.5 sm:flex">
              <span className="size-2 shrink-0 rounded-[2px]" style={{ background: gameColor(r.game) }} />
              <span className="truncate">{gameName(r.game)}</span>
            </span>
            <span className="text-muted-foreground tabular hidden text-right sm:block">
              {show === "multiplier" ? `${compact(r.wagered)} → ${compact(r.payout)}` : `bet ${compact(r.wagered)} · ${times(r.payout / r.wagered)}`}
            </span>
            <span className={cn("tabular text-right font-medium", net >= 0 ? "text-success" : "text-error")}>
              {show === "multiplier" ? times(r.payout / r.wagered) : signed(net)}
            </span>
          </li>
        )
      })}
    </ol>
  )
}

function PlayerList({ rows, people, luck, note }: { rows: PlayerTotal[]; people: People; luck?: boolean; note?: string }) {
  if (!rows.length) return <Empty className="py-6">Nobody qualifies in this period.</Empty>
  return (
    <div className="flex flex-col gap-1">
      {note ? <p className="text-muted-foreground text-[11px]">{note}</p> : null}
      <ol className="flex flex-col">
        {rows.map((r, i) => (
          <li key={r.user_id} className="grid grid-cols-[1.25rem_minmax(0,1fr)_6.5rem] items-center gap-3 border-b py-1.5 text-xs last:border-0 sm:grid-cols-[1.25rem_minmax(0,1.4fr)_6rem_7rem_7rem]">
            <span className="text-muted-foreground tabular">{i + 1}</span>
            <Who people={people} id={r.user_id} />
            <span className="text-muted-foreground tabular hidden text-right sm:block">{credits(r.games)} games</span>
            <span className="text-muted-foreground tabular hidden text-right sm:block">{compact(r.wagered)} bet</span>
            <span className={cn("tabular text-right font-medium", luck ? "" : r.net >= 0 ? "text-success" : "text-error")}>
              {luck ? pct(r.paid, r.wagered) : signed(r.net)}
            </span>
          </li>
        ))}
      </ol>
    </div>
  )
}


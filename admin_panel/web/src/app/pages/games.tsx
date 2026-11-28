// Every game on its own card (GET /api/games): how much it's played, whether it pays back what its rules say it
// should, how often it pays, its biggest wins and its top players.
import * as React from "react"
import { useQuery } from "@tanstack/react-query"
import { ArrowClockwiseIcon } from "@phosphor-icons/react"
import { Bar, CartesianGrid, ComposedChart, Line, ReferenceLine, XAxis, YAxis } from "recharts"

import { api, type GamesPage as GamesData, type GameStats, type Guild, type People, type Period } from "@/app/api"
import { ago, compact, credits, gameColor, gameName, pct, signed, signedCompact } from "@/app/format"
import { DEFAULT_PERIOD, Empty, PeriodToggle, share, slotTick, slotTitle, times, Tip, TipRow, Who } from "@/app/stats-kit"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { ChartContainer, ChartTooltip, type ChartConfig } from "@/components/ui/chart"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

const SORTS = {
  wagered: { label: "Most wagered", key: (g: GameStats) => g.wagered },
  plays: { label: "Most played", key: (g: GameStats) => g.plays },
  payback: { label: "Highest payback", key: (g: GameStats) => g.paid / g.wagered },
  off: { label: "Furthest off expected", key: (g: GameStats) => (g.expected ? Math.abs(g.paid / g.wagered - g.expected.value) : -1) },
} as const
type Sort = keyof typeof SORTS

export function GamesPage({ guild }: { guild: Guild }) {
  const [period, setPeriod] = React.useState<Period>(DEFAULT_PERIOD)
  const [sort, setSort] = React.useState<Sort>("wagered")
  const q = useQuery({
    queryKey: ["games", guild.id, period],
    queryFn: () => api.get<GamesData>(`/api/games?guild=${guild.id}&period=${period}`),
    refetchInterval: 60_000,
  })
  const games = React.useMemo(
    () => [...(q.data?.games ?? [])].sort((a, b) => SORTS[sort].key(b) - SORTS[sort].key(a)),
    [q.data, sort]
  )

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex w-full max-w-[2400px] flex-col gap-4 p-4 md:p-6 2xl:px-8">
        <header className="flex flex-wrap items-center justify-between gap-3 [&>*]:min-w-0">
          <div>
            <h1 className="text-lg font-semibold">Games</h1>
            <p className="text-muted-foreground text-xs">
              Each game's payback against what its rules pay on average · updated {q.dataUpdatedAt ? ago(q.dataUpdatedAt / 1000) : "…"}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Select value={sort} onValueChange={(v) => setSort(v as Sort)}>
              <SelectTrigger size="sm" className="min-w-40" aria-label="Sort games">
                <SelectValue />
              </SelectTrigger>
              <SelectContent align="end">
                {(Object.keys(SORTS) as Sort[]).map((k) => (
                  <SelectItem key={k} value={k}>
                    {SORTS[k].label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <PeriodToggle value={period} onChange={setPeriod} />
            <Button variant="outline" size="icon-sm" aria-label="Refresh" onClick={() => q.refetch()}>
              <ArrowClockwiseIcon className={cn("size-4", q.isFetching && "animate-spin")} />
            </Button>
          </div>
        </header>

        {q.isError ? <p className="text-destructive text-sm">{String(q.error.message)}</p> : null}

        {q.isLoading ? (
          <div className="grid gap-4 lg:grid-cols-2 3xl:grid-cols-3">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-[420px] w-full" />
            ))}
          </div>
        ) : games.length ? (
          <div className="grid items-start gap-4 lg:grid-cols-2 3xl:grid-cols-3">
            {games.map((g) => (
              <GameCard key={g.game} g={g} bucket={q.data!.bucket} people={q.data!.people} />
            ))}
          </div>
        ) : (
          <Empty>No games in this period.</Empty>
        )}
      </div>
    </div>
  )
}

const chartConfig = {
  plays: { label: "Plays", color: "var(--chart-other)" },
  payback: { label: "Payback so far", color: "var(--chart-1)" },
} satisfies ChartConfig

function GameCard({ g, bucket, people }: { g: GameStats; bucket: "hour" | "day"; people: People }) {
  const payback = g.paid / g.wagered
  const house = g.wagered - g.paid
  const exp = g.expected?.value
  const diff = exp != null ? payback - exp : null
  const data = React.useMemo(() => {
    let w = 0, p = 0
    return g.timeline.map((s) => {
      w += s.wagered
      p += s.paid
      return { ...s, payback: w ? (p / w) * 100 : null, slot: s.wagered ? (s.paid / s.wagered) * 100 : null }
    })
  }, [g])
  const values = data.flatMap((r) => (r.payback == null ? [] : [r.payback]))
  const lo = Math.min(...values, exp != null ? exp * 100 : 100)
  const hi = Math.max(...values, exp != null ? exp * 100 : 100)
  const pad = Math.max(2, (hi - lo) * 0.15)

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <span className="size-2.5 rounded-[3px]" style={{ background: gameColor(g.game) }} />
          {gameName(g.game)}
        </CardTitle>
        <CardDescription>
          {credits(g.plays)} plays by {g.players} player{g.players === 1 ? "" : "s"} · {compact(g.wagered)} wagered
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          <Stat
            label="Payback"
            value={share(payback, 1)}
            hint={exp != null ? `expected ${g.expected!.approx ? "≈" : ""}${share(exp, 1)}` : "no fixed payback"}
            tone={diff == null ? undefined : Math.abs(diff) < 0.02 ? undefined : diff > 0 ? "bad" : "good"}
            title={
              diff != null
                ? `${diff > 0 ? "Paying more" : "Paying less"} than expected by ${(Math.abs(diff) * 100).toFixed(1)} points. Over few plays, luck moves this a lot.`
                : undefined
            }
          />
          <Stat label="House" value={signed(house)} tone={house >= 0 ? "good" : "bad"} />
          <Stat label="Pays something" value={pct(g.hits, g.plays, 0)} hint={`wins ${pct(g.wins, g.plays, 0)} of rounds`} title="Rounds that paid anything back / paid more than the bet" />
          <Stat label="Average pay" value={times(g.avg_multiplier)} hint="when it pays" />
          <Stat label="Biggest pay" value={times(g.max_multiplier)} />
          <Stat label="Avg bet" value={g.plays ? compact(g.wagered / g.plays) : "—"} />
        </div>

        <div>
          <h3 className="text-muted-foreground mb-1 text-xs">Payback so far, with plays per {bucket}</h3>
          <ChartContainer config={chartConfig} className="aspect-auto h-[170px] w-full">
            <ComposedChart data={data} margin={{ left: 0, right: 4, top: 8 }}>
              <CartesianGrid vertical={false} strokeDasharray="3 3" />
              <XAxis dataKey="t" tickFormatter={(t) => slotTick(t, bucket)} tickLine={false} axisLine={false} minTickGap={28} fontSize={10} />
              <YAxis yAxisId="plays" hide />
              <YAxis
                yAxisId="pct"
                domain={[Math.max(0, Math.floor(lo - pad)), Math.ceil(hi + pad)]}
                tickFormatter={(v) => `${v}%`}
                tickLine={false}
                axisLine={false}
                width={40}
                fontSize={10}
              />
              {exp != null ? (
                <ReferenceLine yAxisId="pct" y={exp * 100} stroke="var(--warning)" strokeDasharray="4 3" ifOverflow="extendDomain" />
              ) : null}
              <ChartTooltip
                cursor={{ fill: "var(--muted)", opacity: 0.4 }}
                content={({ payload }) => {
                  const r = payload?.[0]?.payload as (typeof data)[number] | undefined
                  if (!r) return null
                  return (
                    <Tip title={slotTitle(r.t, bucket)}>
                      <TipRow color="var(--chart-other)" label="Plays" value={credits(r.plays)} />
                      <TipRow label="Wagered" value={credits(r.wagered)} />
                      <TipRow label={`Payback this ${bucket}`} value={r.slot == null ? "—" : `${r.slot.toFixed(1)}%`} />
                      <TipRow color="var(--chart-1)" label="Payback so far" value={r.payback == null ? "—" : `${r.payback.toFixed(1)}%`} />
                      {exp != null ? <TipRow color="var(--warning)" label="Expected" value={share(exp, 1)} /> : null}
                    </Tip>
                  )
                }}
              />
              <Bar yAxisId="plays" dataKey="plays" fill="var(--color-plays)" fillOpacity={0.6} radius={[2, 2, 0, 0]} isAnimationActive={false} />
              <Line yAxisId="pct" dataKey="payback" type="monotone" stroke="var(--color-payback)" strokeWidth={1.75} dot={false} connectNulls isAnimationActive={false} />
            </ComposedChart>
          </ChartContainer>
          {exp != null ? <p className="text-muted-foreground text-[11px]">Dashed line: the expected payback.</p> : null}
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <section>
            <h3 className="text-muted-foreground mb-1.5 text-xs">Biggest wins</h3>
            {g.top_wins.length ? (
              <ul className="flex flex-col gap-1.5">
                {g.top_wins.map((w, i) => (
                  <li key={i} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 text-xs">
                    <Who people={people} id={w.user_id} size={18} />
                    <span className="tabular text-success" title={`Bet ${credits(w.wagered)}, paid ${credits(w.payout)} (${times(w.payout / w.wagered)}) · ${new Date(w.ts * 1000).toLocaleString()}`}>
                      {signedCompact(w.payout - w.wagered)} <span className="text-muted-foreground">({times(w.payout / w.wagered)})</span>
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-muted-foreground text-xs">No wins.</p>
            )}
          </section>
          <section>
            <h3 className="text-muted-foreground mb-1.5 text-xs">Top players</h3>
            <ul className="flex flex-col gap-1.5">
              {g.top_players.map((p) => (
                <li key={p.user_id} className="grid grid-cols-[minmax(0,1fr)_3.5rem_4rem] items-center gap-2 text-xs">
                  <Who people={people} id={p.user_id} size={18} />
                  <span className="text-muted-foreground tabular text-right" title={`${credits(p.plays)} plays, ${credits(p.wagered)} wagered`}>
                    {compact(p.wagered)}
                  </span>
                  <span className={cn("tabular text-right", p.net >= 0 ? "text-success" : "text-error")} title={signed(p.net)}>{signedCompact(p.net)}</span>
                </li>
              ))}
            </ul>
          </section>
        </div>
      </CardContent>
    </Card>
  )
}

function Stat({ label, value, hint, tone, title }: { label: string; value: string; hint?: string; tone?: "good" | "bad"; title?: string }) {
  return (
    <div className="bg-muted/40 min-w-0 rounded-md border px-2.5 py-2" title={title}>
      <div className="text-muted-foreground truncate text-[11px]">{label}</div>
      <div className={cn("tabular truncate text-sm font-medium", tone === "good" && "text-success", tone === "bad" && "text-error")}>{value}</div>
      {hint ? <div className="text-muted-foreground line-clamp-2 text-[10px] sm:line-clamp-1">{hint}</div> : null}
    </div>
  )
}

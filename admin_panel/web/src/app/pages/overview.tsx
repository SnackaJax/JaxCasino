import * as React from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { ArrowClockwiseIcon } from "@phosphor-icons/react"
import { Area, AreaChart, Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts"

import { api, type Guild, type Overview, type Period, type Player } from "@/app/api"
import { EditBalanceDialog, type EditTarget } from "@/app/edit-balance"
import { ago, compact, credits, displayName, gameColor, gameName, moneyName, pct, signed } from "@/app/format"
import { MoneyPie } from "@/app/money-pie"
import { OverviewExtras } from "@/app/overview-extras"
import { DEFAULT_PERIOD, PeriodToggle } from "@/app/stats-kit"
import { PlayerAvatar } from "@/app/player-avatar"
import { Button } from "@/components/ui/button"
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { ChartContainer, ChartTooltip, type ChartConfig } from "@/components/ui/chart"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { ColGroup, ResizeHandle, useColumnWidths } from "@/app/resizable-table"
import { cn } from "@/lib/utils"

export function OverviewPage({ guild }: { guild: Guild }) {
  const [period, setPeriod] = React.useState<Period>(DEFAULT_PERIOD)
  const [editing, setEditing] = React.useState<EditTarget | null>(null)
  const qc = useQueryClient()
  const overview = useQuery({
    queryKey: ["overview", guild.id, period],
    queryFn: () => api.get<Overview>(`/api/overview?guild=${guild.id}&period=${period}`),
    refetchInterval: 30_000,
  })
  const players = useQuery({
    queryKey: ["players", guild.id],
    queryFn: () => api.get<Player[]>(`/api/players?guild=${guild.id}`),
    refetchInterval: 30_000,
  })
  const o = overview.data
  const edit = (p: Player) => setEditing({ id: p.id, name: displayName(p, p.id), avatar: p.avatar, balance: p.balance })

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex w-full max-w-[2400px] flex-col gap-4 p-4 md:p-6 2xl:px-8">
        <header className="flex flex-wrap items-center justify-between gap-3 [&>*]:min-w-0">
          <div>
            <h1 className="text-lg font-semibold">{guild.name}</h1>
            <p className="text-muted-foreground text-xs">
              Overview · updated {overview.dataUpdatedAt ? ago(overview.dataUpdatedAt / 1000) : "…"}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <PeriodToggle value={period} onChange={setPeriod} />
            <Button
              variant="outline"
              size="icon-sm"
              aria-label="Refresh"
              onClick={() => {
                overview.refetch()
                players.refetch()
                qc.invalidateQueries({ queryKey: ["insights", guild.id] })
              }}
            >
              <ArrowClockwiseIcon className={cn("size-4", (overview.isFetching || players.isFetching) && "animate-spin")} />
            </Button>
          </div>
        </header>

        {overview.isError ? <p className="text-destructive text-sm">{String(overview.error.message)}</p> : null}

        <section className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
          <Kpi label="Credits held" value={o && compact(o.kpis.held)} hint={o && `${credits(o.kpis.in_play)} in play right now`} />
          <Kpi
            label="Players"
            value={o && credits(o.kpis.accounts)}
            hint={o && `${o.kpis.active_day} active today · ${o.kpis.active_week} this week`}
          />
          <Kpi label="Games played" value={o && compact(o.kpis.games)} hint={o && `${o.kpis.players} players took part`} />
          <Kpi label="Wagered" value={o && compact(o.kpis.wagered)} hint={o && `${compact(o.kpis.paid)} paid back`} />
          <Kpi
            label="House result"
            value={o && signed(o.kpis.wagered - o.kpis.paid)}
            tone={o ? (o.kpis.wagered - o.kpis.paid >= 0 ? "good" : "bad") : undefined}
            hint={o && `payback ${pct(o.kpis.paid, o.kpis.wagered)}`}
          />
          <Kpi
            label="Balance edits"
            value={o && credits(o.kpis.edits)}
            hint={o && `${signed(o.kpis.edits_net)} credits net`}
          />
        </section>

        <section className="grid gap-4 xl:grid-cols-[1.15fr_1fr] 3xl:grid-cols-[1.1fr_1fr_1fr]">
          <Card>
            <CardHeader>
              <CardTitle>Money distribution</CardTitle>
              <CardDescription>Who holds the credits right now. Click a player to edit their balance.</CardDescription>
            </CardHeader>
            <CardContent>
              {players.data ? (
                <MoneyPie players={players.data} onPick={edit} />
              ) : (
                <Skeleton className="h-[280px] w-full" />
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Credits in circulation</CardTitle>
              <CardDescription>Everything players hold, over the period.</CardDescription>
            </CardHeader>
            <CardContent>{o ? <CirculationChart points={o.circulation} /> : <Skeleton className="h-[280px] w-full" />}</CardContent>
          </Card>
          <Card className="xl:col-span-2 3xl:col-span-1">
            <CardHeader>
              <CardTitle>Games per day</CardTitle>
              <CardDescription>Won, lost and even games, with the credits wagered each day.</CardDescription>
            </CardHeader>
            <CardContent>{o ? <DailyChart daily={o.daily} /> : <Skeleton className="h-[220px] w-full" />}</CardContent>
          </Card>
        </section>

        <OverviewExtras guild={guild} period={period} />

        <section className="grid items-start gap-4 xl:grid-cols-[1.4fr_1fr] 3xl:grid-cols-[1.6fr_1fr_1fr]">
          <Card className="xl:row-span-2 3xl:row-span-1">
            <CardHeader>
              <CardTitle>Games</CardTitle>
              <CardDescription>What was played in the period, by credits wagered.</CardDescription>
            </CardHeader>
            <CardContent className="px-0">{o ? <GamesTable games={o.games} /> : <Skeleton className="mx-6 h-40" />}</CardContent>
          </Card>
            <Card>
              <CardHeader>
                <CardTitle>Credits outside games</CardTitle>
                <CardDescription>Claims, restarts, payments, the shop and admin changes.</CardDescription>
              </CardHeader>
              <CardContent className="px-0">{o ? <MoneyTable money={o.money} /> : <Skeleton className="mx-6 h-24" />}</CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Latest balance edits</CardTitle>
                <CardAction>
                  <a href="#/edits" className="text-muted-foreground hover:text-foreground text-xs">
                    All edits →
                  </a>
                </CardAction>
              </CardHeader>
              <CardContent>
                {o?.edits.length ? (
                  <ul className="flex flex-col gap-2.5">
                    {o.edits.map((e) => (
                      <li key={e.id} className="flex items-center gap-2.5 text-xs">
                        <PlayerAvatar name={e.name} avatar={e.avatar} size={20} />
                        <span className="min-w-0 flex-1 truncate">
                          {displayName(e, e.user_id)}
                          {e.note ? <span className="text-muted-foreground"> · {e.note}</span> : null}
                        </span>
                        <span className={cn("tabular", e.after > e.before ? "text-success" : "text-error")}>
                          {signed(e.after - e.before)}
                        </span>
                        <span className="text-muted-foreground w-16 text-right">{ago(e.ts)}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-muted-foreground text-xs">No edits yet.</p>
                )}
              </CardContent>
            </Card>
        </section>
      </div>
      <EditBalanceDialog guildId={guild.id} target={editing} onClose={() => setEditing(null)} />
    </div>
  )
}

function Kpi({
  label,
  value,
  hint,
  tone,
}: {
  label: string
  value?: React.ReactNode
  hint?: React.ReactNode
  tone?: "good" | "bad"
}) {
  return (
    <Card className="gap-1 py-4">
      <CardHeader className="px-4">
        <CardDescription className="text-xs">{label}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-1 px-4">
        {value === undefined ? (
          <Skeleton className="h-7 w-24" />
        ) : (
          <span
            className={cn(
              "tabular truncate text-xl font-semibold tracking-tight sm:text-2xl",
              tone === "good" && "text-success",
              tone === "bad" && "text-error"
            )}
          >
            {value}
          </span>
        )}
        <span className="text-muted-foreground truncate text-[11px]">{hint ?? " "}</span>
      </CardContent>
    </Card>
  )
}

const circulationConfig = { total: { label: "Credits held", color: "var(--chart-1)" } } satisfies ChartConfig

function CirculationChart({ points }: { points: [number, number][] }) {
  const data = React.useMemo(() => points.map(([ts, total]) => ({ ts: ts * 1000, total })), [points])
  if (data.length < 2) {
    return <p className="text-muted-foreground py-16 text-center text-sm">Not enough history yet.</p>
  }
  const span = data[data.length - 1].ts - data[0].ts
  const tick = (ts: number) =>
    new Date(ts).toLocaleString(undefined, span < 2 * 86400_000 ? { hour: "2-digit", minute: "2-digit" } : { month: "short", day: "numeric" })
  return (
    <ChartContainer config={circulationConfig} className="aspect-auto h-[280px] w-full">
      <AreaChart data={data} margin={{ left: 0, right: 8, top: 8 }}>
        <defs>
          <linearGradient id="fillTotal" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--color-total)" stopOpacity={0.35} />
            <stop offset="100%" stopColor="var(--color-total)" stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <CartesianGrid vertical={false} strokeDasharray="3 3" />
        <XAxis dataKey="ts" type="number" scale="time" domain={["dataMin", "dataMax"]} tickFormatter={tick} tickLine={false} axisLine={false} minTickGap={40} fontSize={11} />
        <YAxis tickFormatter={(v) => compact(v)} tickLine={false} axisLine={false} width={48} fontSize={11} />
        <ChartTooltip
          content={({ payload }) => {
            const p = payload?.[0]?.payload as { ts: number; total: number } | undefined
            if (!p) return null
            return (
              <div className="bg-popover rounded-md border px-3 py-2 text-xs shadow-md">
                <div className="text-muted-foreground">{new Date(p.ts).toLocaleString()}</div>
                <div className="tabular font-medium">{credits(p.total)} credits</div>
              </div>
            )
          }}
        />
        <Area dataKey="total" type="stepAfter" stroke="var(--color-total)" strokeWidth={1.5} fill="url(#fillTotal)" isAnimationActive={false} />
      </AreaChart>
    </ChartContainer>
  )
}

const dailyConfig = {
  won: { label: "Won", color: "var(--success)" },
  even: { label: "Even", color: "var(--chart-other)" },
  lost: { label: "Lost", color: "var(--error)" },
} satisfies ChartConfig

function DailyChart({ daily }: { daily: Overview["daily"] }) {
  return (
    <ChartContainer config={dailyConfig} className="aspect-auto h-[220px] w-full">
      <BarChart data={daily} margin={{ left: 0, right: 8, top: 8 }}>
        <CartesianGrid vertical={false} strokeDasharray="3 3" />
        <XAxis
          dataKey="day"
          tickLine={false}
          axisLine={false}
          minTickGap={24}
          fontSize={11}
          tickFormatter={(d: string) => new Date(d + "T12:00").toLocaleDateString(undefined, { month: "short", day: "numeric" })}
        />
        <YAxis tickLine={false} axisLine={false} width={40} fontSize={11} tickFormatter={(v) => compact(v)} />
        <ChartTooltip
          cursor={{ fill: "var(--muted)", opacity: 0.4 }}
          content={({ payload }) => {
            const d = payload?.[0]?.payload as Overview["daily"][number] | undefined
            if (!d) return null
            const games = d.won + d.lost + d.even
            return (
              <div className="bg-popover grid min-w-44 gap-1 rounded-md border px-3 py-2 text-xs shadow-md">
                <div className="font-medium">
                  {new Date(d.day + "T12:00").toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })}
                </div>
                <Row color="var(--success)" label="Won" value={credits(d.won)} />
                <Row color="var(--chart-other)" label="Even" value={credits(d.even)} />
                <Row color="var(--error)" label="Lost" value={credits(d.lost)} />
                <div className="border-t pt-1" />
                <Row label="Games" value={credits(games)} />
                <Row label="Wagered" value={credits(d.wagered)} />
                <Row label="House" value={signed(d.wagered - d.paid)} />
              </div>
            )
          }}
        />
        <Bar dataKey="won" stackId="a" fill="var(--color-won)" isAnimationActive={false} />
        <Bar dataKey="even" stackId="a" fill="var(--color-even)" isAnimationActive={false} />
        <Bar dataKey="lost" stackId="a" fill="var(--color-lost)" radius={[3, 3, 0, 0]} isAnimationActive={false} />
      </BarChart>
    </ChartContainer>
  )
}

function Row({ color, label, value }: { color?: string; label: string; value: string }) {
  return (
    <div className="flex items-center gap-2">
      {color ? <span className="size-2 rounded-[2px]" style={{ background: color }} /> : <span className="size-2" />}
      <span className="text-muted-foreground flex-1">{label}</span>
      <span className="tabular">{value}</span>
    </div>
  )
}

function GamesTable({ games }: { games: Overview["games"] }) {
  const cols = useColumnWidths("overview-games", [170, 80, 80, 260, 90, 130])
  const played = games.filter((g) => g.wagered > 0)
  const max = Math.max(1, ...played.map((g) => g.wagered))
  if (!played.length) return <p className="text-muted-foreground px-6 text-sm">No games in this period.</p>
  return (
    <Table ref={cols.tableRef} style={cols.tableStyle} className="[&_td]:overflow-hidden [&_td]:text-ellipsis [&_th]:overflow-hidden">
      <ColGroup widths={cols.widths} />
      <TableHeader>
        <TableRow>
          <TableHead className="relative pl-6">Game<ResizeHandle onPointerDown={cols.startResize(0)} onReset={cols.reset} /></TableHead>
          <TableHead className="relative text-right">Plays<ResizeHandle onPointerDown={cols.startResize(1)} onReset={cols.reset} /></TableHead>
          <TableHead className="relative text-right">Players<ResizeHandle onPointerDown={cols.startResize(2)} onReset={cols.reset} /></TableHead>
          <TableHead className="relative">Wagered<ResizeHandle onPointerDown={cols.startResize(3)} onReset={cols.reset} /></TableHead>
          <TableHead className="relative text-right">Payback<ResizeHandle onPointerDown={cols.startResize(4)} onReset={cols.reset} /></TableHead>
          <TableHead className="relative pr-6 text-right">House<ResizeHandle onPointerDown={cols.startResize(5)} onReset={cols.reset} /></TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {played.map((g) => {
          const house = g.wagered - g.paid
          return (
            <TableRow key={g.game}>
              <TableCell className="pl-6">
                <span className="flex items-center gap-2">
                  <span className="size-2 rounded-[2px]" style={{ background: gameColor(g.game) }} />
                  {gameName(g.game)}
                </span>
              </TableCell>
              <TableCell className="tabular text-right">{credits(g.plays)}</TableCell>
              <TableCell className="tabular text-right">{g.players}</TableCell>
              <TableCell>
                <div className="flex items-center gap-2">
                  <div className="bg-muted h-1.5 flex-1 overflow-hidden rounded-full">
                    <div className="h-full rounded-full" style={{ width: `${(g.wagered / max) * 100}%`, background: gameColor(g.game) }} />
                  </div>
                  <span className="tabular w-14 text-right text-xs">{compact(g.wagered)}</span>
                </div>
              </TableCell>
              <TableCell className="tabular text-right">{pct(g.paid, g.wagered)}</TableCell>
              <TableCell className={cn("tabular pr-6 text-right", house >= 0 ? "text-success" : "text-error")}>
                {signed(house)}
              </TableCell>
            </TableRow>
          )
        })}
      </TableBody>
    </Table>
  )
}

function MoneyTable({ money }: { money: Overview["money"] }) {
  const cols = useColumnWidths("overview-money", [150, 80, 150, 150])
  if (!money.length) return <p className="text-muted-foreground px-6 text-sm">Nothing in this period.</p>
  return (
    <Table ref={cols.tableRef} style={cols.tableStyle} className="[&_td]:overflow-hidden [&_td]:text-ellipsis [&_th]:overflow-hidden">
      <ColGroup widths={cols.widths} />
      <TableHeader>
        <TableRow>
          <TableHead className="relative pl-6">Kind<ResizeHandle onPointerDown={cols.startResize(0)} onReset={cols.reset} /></TableHead>
          <TableHead className="relative text-right">Times<ResizeHandle onPointerDown={cols.startResize(1)} onReset={cols.reset} /></TableHead>
          <TableHead className="relative text-right">Added<ResizeHandle onPointerDown={cols.startResize(2)} onReset={cols.reset} /></TableHead>
          <TableHead className="relative pr-6 text-right">Removed<ResizeHandle onPointerDown={cols.startResize(3)} onReset={cols.reset} /></TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {money.map((m) => (
          <TableRow key={m.kind}>
            <TableCell className="pl-6">{moneyName(m.kind)}</TableCell>
            <TableCell className="tabular text-right">{credits(m.n)}</TableCell>
            {m.kind === "pay" ? (
              // a payment moves credits between players: nothing enters or leaves the economy
              <TableCell colSpan={2} className="tabular text-muted-foreground pr-6 text-right">
                {credits(m.added)} moved between players
              </TableCell>
            ) : (
              <>
                <TableCell className="tabular text-success text-right">{m.added ? `+${credits(m.added)}` : "—"}</TableCell>
                <TableCell className="tabular text-error pr-6 text-right">{m.removed ? `−${credits(m.removed)}` : "—"}</TableCell>
              </>
            )}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

// The extras in a player's details (GET /api/players/{id}/insights): when they play, their net result per day,
// where their bets go with their payback next to everyone's, best winstreaks, going broke and /pay.
import * as React from "react"
import { useQuery } from "@tanstack/react-query"
import { Bar, BarChart, Cell, Pie, PieChart, ReferenceLine, XAxis, YAxis } from "recharts"

import { api, type PlayerInsights } from "@/app/api"
import { ago, compact, credits, gameColor, gameName, pct, signed } from "@/app/format"
import { Heatmap, share, Tip, TipRow, Who } from "@/app/stats-kit"
import { ChartContainer, ChartTooltip } from "@/components/ui/chart"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

export function usePlayerInsights(guildId: string, playerId: string) {
  return useQuery({
    queryKey: ["player-insights", guildId, playerId],
    queryFn: () => api.get<PlayerInsights>(`/api/players/${playerId}/insights?guild=${guildId}`),
  })
}

function Section({ title, children, aside }: { title: string; children: React.ReactNode; aside?: React.ReactNode }) {
  return (
    <section>
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <h3 className="text-muted-foreground text-xs">{title}</h3>
        {aside ? <span className="text-muted-foreground text-[11px]">{aside}</span> : null}
      </div>
      {children}
    </section>
  )
}

/** Net result per day (the last 30 days). */
export function PlayerDaily({ d }: { d?: PlayerInsights }) {
  if (!d) return <Skeleton className="h-32 w-full" />
  const total = d.daily.reduce((a, r) => a + r.net, 0)
  return (
    <Section
      title="Net result per day"
      aside={
        <>
          30 days: <span className={cn("tabular", total >= 0 ? "text-success" : "text-error")}>{signed(total)}</span>
        </>
      }
    >
      {d.daily.some((r) => r.games) ? (
        <ChartContainer config={{}} className="aspect-auto h-32 w-full">
          <BarChart data={d.daily} margin={{ left: 0, right: 4, top: 4 }}>
            <XAxis
              dataKey="day"
              tickFormatter={(v: string) => new Date(v + "T12:00").toLocaleDateString([], { day: "numeric", month: "short" })}
              tickLine={false}
              axisLine={false}
              minTickGap={24}
              fontSize={10}
            />
            <YAxis tickFormatter={(v) => compact(v)} tickLine={false} axisLine={false} width={44} fontSize={10} />
            <ReferenceLine y={0} stroke="var(--border)" />
            <ChartTooltip
              cursor={{ fill: "var(--muted)", opacity: 0.4 }}
              content={({ payload }) => {
                const r = payload?.[0]?.payload as PlayerInsights["daily"][number] | undefined
                if (!r) return null
                return (
                  <Tip title={new Date(r.day + "T12:00").toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" })}>
                    <TipRow label="Games" value={credits(r.games)} />
                    <TipRow label="Wagered" value={credits(r.wagered)} />
                    <TipRow color={r.net >= 0 ? "var(--success)" : "var(--error)"} label="Net" value={signed(r.net)} />
                  </Tip>
                )
              }}
            />
            <Bar dataKey="net" radius={[2, 2, 0, 0]} isAnimationActive={false}>
              {d.daily.map((r) => (
                <Cell key={r.day} fill={r.net >= 0 ? "var(--success)" : "var(--error)"} />
              ))}
            </Bar>
          </BarChart>
        </ChartContainer>
      ) : (
        <p className="text-muted-foreground text-xs">No games in the last 30 days.</p>
      )}
    </Section>
  )
}

/** Where their bets go (donut) and their payback per game next to everyone's. */
export function PlayerMix({ d }: { d?: PlayerInsights }) {
  if (!d) return <Skeleton className="h-40 w-full" />
  if (!d.mix.length) return null
  const wagered = d.mix.reduce((a, m) => a + m.wagered, 0)
  const paid = d.mix.reduce((a, m) => a + m.paid, 0)
  const mine = wagered ? paid / wagered : 0
  const all = d.everyone.wagered ? d.everyone.paid / d.everyone.wagered : 0
  return (
    <Section
      title="Games and payback"
      aside={
        <>
          payback <span className={cn("tabular", mine >= all ? "text-success" : "text-error")}>{share(mine, 1)}</span> · everyone{" "}
          <span className="tabular">{share(all, 1)}</span>
        </>
      }
    >
      <div className="grid grid-cols-[7.5rem_minmax(0,1fr)] items-center gap-3">
        <ChartContainer config={{}} className="aspect-square h-[7.5rem] w-[7.5rem]">
          <PieChart>
            <ChartTooltip
              content={({ payload }) => {
                const m = payload?.[0]?.payload as PlayerInsights["mix"][number] | undefined
                if (!m) return null
                return (
                  <Tip title={gameName(m.game)}>
                    <TipRow label="Share of bets" value={pct(m.wagered, wagered, 0)} />
                    <TipRow label="Plays" value={credits(m.plays)} />
                  </Tip>
                )
              }}
            />
            <Pie data={d.mix} dataKey="wagered" nameKey="game" innerRadius="58%" outerRadius="100%" strokeWidth={1} stroke="var(--card)" isAnimationActive={false}>
              {d.mix.map((m) => (
                <Cell key={m.game} fill={gameColor(m.game)} />
              ))}
            </Pie>
          </PieChart>
        </ChartContainer>
        <ul className="flex min-w-0 flex-col gap-1">
          <li className="text-muted-foreground grid grid-cols-[minmax(0,1fr)_2.5rem_3.5rem_3.5rem] gap-2 text-[10px]">
            <span>Game</span>
            <span className="text-right">Share</span>
            <span className="text-right">Theirs</span>
            <span className="text-right">Everyone</span>
          </li>
          {d.mix.slice(0, 7).map((m) => {
            const theirs = m.paid / m.wagered
            const everyone = m.all_wagered ? m.all_paid / m.all_wagered : 0
            return (
              <li key={m.game} className="grid grid-cols-[minmax(0,1fr)_2.5rem_3.5rem_3.5rem] items-center gap-2 text-xs">
                <span className="flex min-w-0 items-center gap-1.5">
                  <span className="size-2 shrink-0 rounded-full" style={{ background: gameColor(m.game) }} />
                  <span className="truncate">{gameName(m.game)}</span>
                </span>
                <span className="text-muted-foreground tabular text-right">{pct(m.wagered, wagered, 0)}</span>
                <span className={cn("tabular text-right", theirs >= everyone ? "text-success" : "text-error")}>{share(theirs, 0)}</span>
                <span className="text-muted-foreground tabular text-right">{share(everyone, 0)}</span>
              </li>
            )
          })}
        </ul>
      </div>
    </Section>
  )
}

export function PlayerHeatmap({ d }: { d?: PlayerInsights }) {
  if (!d) return <Skeleton className="h-36 w-full" />
  if (!d.heatmap.length) return null
  return (
    <Section title="When they play" aside="all time, by weekday and hour">
      <Heatmap cells={d.heatmap} metric="games" compactRows />
    </Section>
  )
}

/** Best winstreaks, going broke and payments. */
export function PlayerHistory({ d }: { d?: PlayerInsights }) {
  if (!d) return <Skeleton className="h-32 w-full" />
  return (
    <>
      <section className="grid grid-cols-2 gap-2">
        <div className="bg-muted/40 rounded-md border px-2.5 py-2">
          <div className="text-muted-foreground text-[11px]">Went broke</div>
          <div className="tabular text-sm font-medium">{credits(d.broke.n)}×</div>
          <div className="text-muted-foreground text-[10px]">
            {d.broke.n ? `${d.broke.week} this week · last ${ago(d.broke.last)} · ${compact(d.broke.credits)} handed out` : "never"}
          </div>
        </div>
        <div className="bg-muted/40 rounded-md border px-2.5 py-2">
          <div className="text-muted-foreground text-[11px]">Best winstreaks</div>
          {d.streaks.length ? (
            <div className="flex flex-wrap gap-x-3 text-xs">
              {d.streaks.map((s) => (
                <span key={s.game} title={`${s.best} wins in a row at ${gameName(s.game)}`}>
                  <span className="tabular font-medium">🔥 {s.best}</span> <span className="text-muted-foreground">{gameName(s.game)}</span>
                </span>
              ))}
            </div>
          ) : (
            <div className="text-muted-foreground text-xs">none yet</div>
          )}
        </div>
      </section>

      <Section title="Payments with other players">
        {d.payments.length ? (
          <ul className="flex flex-col gap-1.5">
            <li className="text-muted-foreground grid grid-cols-[minmax(0,1fr)_5rem_5rem] gap-2 text-[10px]">
              <span>Player</span>
              <span className="text-right">Sent to</span>
              <span className="text-right">Got from</span>
            </li>
            {d.payments.slice(0, 8).map((p) => (
              <li key={p.user_id} className="grid grid-cols-[minmax(0,1fr)_5rem_5rem] items-center gap-2 text-xs" title={`Last payment ${ago(p.last)}`}>
                <Who people={d.people} id={p.user_id} size={18} />
                <span className="tabular text-right" title={`${p.sent_n} payment${p.sent_n === 1 ? "" : "s"}`}>
                  {p.sent ? <span className="text-error">−{compact(p.sent)}</span> : <span className="text-muted-foreground">—</span>}
                </span>
                <span className="tabular text-right" title={`${p.received_n} payment${p.received_n === 1 ? "" : "s"}`}>
                  {p.received ? <span className="text-success">+{compact(p.received)}</span> : <span className="text-muted-foreground">—</span>}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-muted-foreground text-xs">No payments.</p>
        )}
      </Section>
    </>
  )
}

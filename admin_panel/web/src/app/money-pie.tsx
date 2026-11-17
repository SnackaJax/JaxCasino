import * as React from "react"
import { Cell, Label, Pie, PieChart } from "recharts"

import type { Player } from "@/app/api"
import { compact, credits, displayName, pct } from "@/app/format"
import { PlayerAvatar } from "@/app/player-avatar"
import { ChartContainer, ChartTooltip, type ChartConfig } from "@/components/ui/chart"
import { cn } from "@/lib/utils"

const SLICES = 9 // the richest players get their own slice; everyone else shares one

type Slice = { key: string; label: string; value: number; color: string; player?: Player; count?: number }

/** Who holds the server's credits: the richest players by name, everyone else as one slice. */
export function MoneyPie({ players, onPick }: { players: Player[]; onPick?: (p: Player) => void }) {
  const [active, setActive] = React.useState<string | null>(null)
  const sorted = React.useMemo(() => [...players].sort((a, b) => b.balance - a.balance), [players])
  const total = sorted.reduce((sum, p) => sum + p.balance, 0)

  const slices: Slice[] = React.useMemo(() => {
    const top = sorted.slice(0, SLICES).filter((p) => p.balance > 0)
    const rest = sorted.slice(top.length)
    const out: Slice[] = top.map((p, i) => ({
      key: p.id,
      label: displayName(p, p.id),
      value: p.balance,
      color: `var(--chart-${i + 1})`,
      player: p,
    }))
    const restTotal = rest.reduce((sum, p) => sum + p.balance, 0)
    if (restTotal > 0) {
      out.push({
        key: "rest",
        label: `${rest.length} other players`,
        value: restTotal,
        color: "var(--chart-other)",
        count: rest.length,
      })
    }
    return out
  }, [sorted])

  const config = React.useMemo(
    () => Object.fromEntries(slices.map((s) => [s.key, { label: s.label, color: s.color }])) as ChartConfig,
    [slices]
  )

  // how concentrated the money is: what the richest tenth holds
  const tenth = Math.max(1, Math.ceil(sorted.length / 10))
  const topTenth = sorted.slice(0, tenth).reduce((sum, p) => sum + p.balance, 0)
  const highlighted = slices.find((s) => s.key === active)

  if (!total) {
    return <p className="text-muted-foreground py-16 text-center text-sm">No credits held yet.</p>
  }

  return (
    <div className="grid items-center gap-6 md:grid-cols-[minmax(220px,280px)_1fr]">
      <ChartContainer config={config} className="mx-auto aspect-square w-full max-w-[280px]">
        <PieChart>
          <ChartTooltip
            cursor={false}
            content={({ payload }) => {
              const s = payload?.[0]?.payload as Slice | undefined
              if (!s) return null
              return (
                <div className="bg-popover text-popover-foreground rounded-md border px-3 py-2 text-xs shadow-md">
                  <div className="font-medium">{s.label}</div>
                  <div className="text-muted-foreground tabular">
                    {credits(s.value)} · {pct(s.value, total)}
                  </div>
                </div>
              )
            }}
          />
          <Pie
            data={slices}
            dataKey="value"
            nameKey="label"
            innerRadius="62%"
            outerRadius="100%"
            paddingAngle={1}
            stroke="var(--card)"
            strokeWidth={2}
            onMouseEnter={(_, i) => setActive(slices[i]?.key ?? null)}
            onMouseLeave={() => setActive(null)}
            onClick={(_, i) => {
              const p = slices[i]?.player
              if (p && onPick) onPick(p)
            }}
            isAnimationActive={false}
          >
            {slices.map((s) => (
              <Cell
                key={s.key}
                fill={s.color}
                opacity={active && active !== s.key ? 0.35 : 1}
                className={cn(s.player && onPick && "cursor-pointer")}
              />
            ))}
            <Label
              content={({ viewBox }) => {
                if (!viewBox || !("cx" in viewBox)) return null
                const { cx, cy } = viewBox as { cx: number; cy: number }
                return (
                  <text x={cx} y={cy} textAnchor="middle" dominantBaseline="middle">
                    <tspan x={cx} y={cy - 8} className="fill-foreground text-xl font-semibold">
                      {highlighted ? pct(highlighted.value, total) : compact(total)}
                    </tspan>
                    <tspan x={cx} y={cy + 14} className="fill-muted-foreground text-[11px]">
                      {highlighted ? highlighted.label.slice(0, 18) : "credits held"}
                    </tspan>
                  </text>
                )
              }}
            />
          </Pie>
        </PieChart>
      </ChartContainer>
      <div className="flex min-w-0 flex-col gap-3">
        <p className="text-muted-foreground text-xs">
          The richest {tenth === 1 ? "player holds" : `${tenth} players (top 10%) hold`}{" "}
          <span className="text-foreground font-medium">{pct(topTenth, total)}</span> of all credits.
        </p>
        <ol className="flex flex-col">
          {slices.map((s) => (
            <li key={s.key}>
              <button
                type="button"
                disabled={!s.player || !onPick}
                onClick={() => s.player && onPick?.(s.player)}
                onMouseEnter={() => setActive(s.key)}
                onMouseLeave={() => setActive(null)}
                className={cn(
                  "flex w-full items-center gap-2.5 rounded-sm px-1.5 py-1 text-left text-xs transition-colors",
                  s.player && onPick && "hover:bg-muted/60",
                  active === s.key && "bg-muted/60"
                )}
              >
                <span className="size-2.5 shrink-0 rounded-[2px]" style={{ background: s.color }} />
                {s.player ? (
                  <PlayerAvatar name={s.player.name} avatar={s.player.avatar} size={18} />
                ) : (
                  <span className="size-[18px]" />
                )}
                <span className="min-w-0 flex-1 truncate">{s.label}</span>
                <span className="tabular text-right">{credits(s.value)}</span>
                <span className="text-muted-foreground tabular w-12 text-right">{pct(s.value, total)}</span>
              </button>
            </li>
          ))}
        </ol>
      </div>
    </div>
  )
}

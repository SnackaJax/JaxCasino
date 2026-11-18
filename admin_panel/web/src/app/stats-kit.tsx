// Pieces the stats pages share: the period switch, time labels, the weekday x hour heatmap and player names.
import * as React from "react"

import type { HeatCell, People, Period } from "@/app/api"
import { compact, credits, displayName } from "@/app/format"
import { PlayerAvatar } from "@/app/player-avatar"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { cn } from "@/lib/utils"

export const PERIODS: { value: Period; label: string }[] = [
  { value: "24h", label: "24h" },
  { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" },
  { value: "all", label: "All time" },
]

/** The overview and the Games page open on the last 24 hours. */
export const DEFAULT_PERIOD: Period = "24h"

export function PeriodToggle({ value, onChange }: { value: Period; onChange: (p: Period) => void }) {
  return (
    <ToggleGroup type="single" variant="outline" size="sm" value={value} onValueChange={(v) => v && onChange(v as Period)}>
      {PERIODS.map((p) => (
        <ToggleGroupItem
          key={p.value}
          value={p.value}
          className="data-[state=on]:bg-primary data-[state=on]:text-primary-foreground px-2 sm:px-3"
        >
          {p.label}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  )
}

/** An axis label for a chart slot (an hour or a day). */
export function slotTick(t: number, bucket: "hour" | "day") {
  const d = new Date(t * 1000)
  return bucket === "hour"
    ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString([], { month: "short", day: "numeric" })
}

/** The heading of a chart slot in a tooltip. */
export function slotTitle(t: number, bucket: "hour" | "day") {
  const d = new Date(t * 1000)
  if (bucket === "hour") {
    const end = new Date((t + 3600) * 1000)
    return `${d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" })}, ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}–${end.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`
  }
  return d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" })
}

export const times = (x: number | null | undefined) =>
  x == null ? "—" : `${x >= 100 ? Math.round(x).toLocaleString("en-US") : x.toFixed(x >= 10 ? 1 : 2)}×`

export const share = (x: number | null | undefined, digits = 0) => (x == null ? "—" : `${(x * 100).toFixed(digits)}%`)

/** The box a chart's tooltip draws in. */
export function Tip({ title, children }: { title?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="bg-popover grid min-w-44 gap-1 rounded-md border px-3 py-2 text-xs shadow-md">
      {title ? <div className="font-medium">{title}</div> : null}
      {children}
    </div>
  )
}

export function TipRow({ color, label, value }: { color?: string; label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2">
      {color ? <span className="size-2 shrink-0 rounded-[2px]" style={{ background: color }} /> : <span className="size-2 shrink-0" />}
      <span className="text-muted-foreground flex-1">{label}</span>
      <span className="tabular">{value}</span>
    </div>
  )
}

/** A player's avatar and name, from the people a stats answer came with. */
export function Who({ people, id, size = 20, className }: { people: People; id: string; size?: number; className?: string }) {
  const p = people[id] ?? { name: null, avatar: null, in_server: false }
  return (
    <span className={cn("flex min-w-0 items-center gap-2", className)}>
      <PlayerAvatar name={p.name} avatar={p.avatar} size={size} />
      <span className={cn("truncate", !p.in_server && "text-muted-foreground")}>{displayName(p, id)}</span>
    </span>
  )
}

export function Empty({ children, className }: { children: React.ReactNode; className?: string }) {
  return <p className={cn("text-muted-foreground py-10 text-center text-sm", className)}>{children}</p>
}

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

/**
 * Weekday x hour of the day, darker where more was played. Hovering a cell shows its numbers under the grid.
 * metric: what the colour follows (games played or credits wagered).
 */
export function Heatmap({
  cells,
  metric,
  compactRows = false,
}: {
  cells: HeatCell[]
  metric: "games" | "wagered"
  compactRows?: boolean
}) {
  const [hover, setHover] = React.useState<HeatCell | null>(null)
  const grid = React.useMemo(() => {
    const g: (HeatCell | null)[][] = Array.from({ length: 7 }, () => Array(24).fill(null))
    for (const c of cells) g[c.dow][c.hour] = c
    return g
  }, [cells])
  const max = Math.max(1, ...cells.map((c) => c[metric]))
  const busiest = cells.reduce<HeatCell | null>((a, c) => (!a || c[metric] > a[metric] ? c : a), null)
  const show = hover ?? busiest
  const hourLabel = (h: number) => `${String(h).padStart(2, "0")}:00`
  return (
    <div className="flex flex-col gap-2">
      <div className="grid grid-cols-[2.25rem_repeat(24,minmax(0,1fr))] gap-[2px]" onPointerLeave={() => setHover(null)}>
        <span />
        {Array.from({ length: 24 }, (_, h) => (
          <span key={h} className="text-muted-foreground text-center text-[9px] leading-3">
            {h % 3 === 0 ? String(h).padStart(2, "0") : ""}
          </span>
        ))}
        {grid.map((row, d) => (
          <React.Fragment key={d}>
            <span className="text-muted-foreground self-center text-[10px]">{DAYS[d]}</span>
            {row.map((c, h) => {
              const v = c ? c[metric] / max : 0
              return (
                <span
                  key={h}
                  onPointerEnter={() => setHover(c ?? { dow: d, hour: h, games: 0, wagered: 0, players: 0 })}
                  className={cn(
                    "rounded-[2px]",
                    compactRows ? "h-3.5" : "h-5",
                    hover && hover.dow === d && hover.hour === h && "ring-foreground/60 ring-1"
                  )}
                  style={{
                    background: v
                      ? `color-mix(in oklab, var(--chart-1) ${Math.round(12 + Math.sqrt(v) * 88)}%, transparent)`
                      : "var(--muted)",
                    opacity: v ? 1 : 0.35,
                  }}
                />
              )
            })}
          </React.Fragment>
        ))}
      </div>
      <p className="text-muted-foreground min-h-4 text-[11px]">
        {show ? (
          <>
            <span className="text-foreground">
              {DAYS[show.dow]} {hourLabel(show.hour)}–{hourLabel((show.hour + 1) % 24)}
            </span>
            {!hover ? " (busiest)" : ""}: {credits(show.games)} games · {compact(show.wagered)} wagered
            {show.players != null ? ` · ${show.players} player${show.players === 1 ? "" : "s"}` : ""}
          </>
        ) : (
          "No games yet."
        )}
      </p>
    </div>
  )
}

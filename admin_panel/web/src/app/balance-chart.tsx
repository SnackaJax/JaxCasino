import * as React from "react"
import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { Area, AreaChart, CartesianGrid, ReferenceArea, XAxis, YAxis } from "recharts"

import { api, type BalanceWindow } from "@/app/api"
import { useDragZoom } from "@/app/drag-zoom"
import { compact, credits } from "@/app/format"
import { Button } from "@/components/ui/button"
import { ChartContainer, ChartTooltip, type ChartConfig } from "@/components/ui/chart"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"

const config = { balance: { label: "Balance", color: "var(--chart-1)" } } satisfies ChartConfig

const HOUR = 3600
const DAY = 24 * HOUR
const RANGES = [
  { value: "1h", label: "1 hour", seconds: HOUR },
  { value: "8h", label: "8 hours", seconds: 8 * HOUR },
  { value: "24h", label: "24 hours", seconds: DAY },
  { value: "3d", label: "3 days", seconds: 3 * DAY },
  { value: "1w", label: "Week", seconds: 7 * DAY },
  { value: "1m", label: "Month", seconds: 30 * DAY },
  { value: "1y", label: "Year", seconds: 365 * DAY },
  { value: "all", label: "All time", seconds: 0 },
] as const
type Range = (typeof RANGES)[number]["value"]
const Y_AXIS = 44          // px: the value axis on the left
const RIGHT_MARGIN = 8     // px: room on the right of the line

/** Every player's chart opens on the last 24 hours. */
const DEFAULT_RANGE: Range = "24h"

/** Time labels that suit how much time is on screen. */
function tickLabel(ms: number, span: number) {
  const d = new Date(ms)
  if (span <= 2 * DAY) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
  if (span <= 60 * DAY) return d.toLocaleDateString([], { day: "numeric", month: "short" })
  return d.toLocaleDateString([], { month: "short", year: "2-digit" })
}

/**
 * A player's balance over time. The dropdown picks how far back it looks; drag across the chart to zoom into any
 * stretch (it loads that stretch in full detail), double-click or "Back" to return to the picked range.
 */
export function BalanceChart({ guildId, playerId }: { guildId: string; playerId: string }) {
  const [range, setRange] = React.useState<Range>(DEFAULT_RANGE)
  const [zoom, setZoom] = React.useState<{ from: number; to: number } | null>(null)
  // "now" moves on with every refetch of a preset range, so the window is worked out per query
  const preset = RANGES.find((r) => r.value === range)!

  const history = useQuery({
    queryKey: ["balance-history", guildId, playerId, zoom ? `${zoom.from}-${zoom.to}` : range],
    queryFn: () => {
      const now = Date.now() / 1000
      const from = zoom ? zoom.from : preset.seconds ? now - preset.seconds : 0
      const to = zoom ? zoom.to : now
      return api.get<BalanceWindow>(
        `/api/players/${playerId}/history?guild=${guildId}&from=${from.toFixed(3)}&to=${to.toFixed(3)}`
      )
    },
    placeholderData: keepPreviousData,
  })

  const data = React.useMemo(
    () => (history.data?.points ?? []).map(([ts, balance]) => ({ ts: ts * 1000, balance })),
    [history.data]
  )
  const span = history.data ? history.data.to - (history.data.from || (data[0]?.ts ?? 0) / 1000) : DAY

  const pick = (v: string) => {
    setRange(v as Range)
    setZoom(null)
  }

  const { ref: plotRef, drag, dragging, handlers } = useDragZoom({
    start: data[0]?.ts ?? 0,
    end: data[data.length - 1]?.ts ?? 0,
    left: Y_AXIS,
    right: RIGHT_MARGIN,
    onZoom: (from, to) => setZoom({ from: from / 1000, to: to / 1000 }),
    onReset: () => setZoom(null),
  })

  return (
    <section>
      <div className="mb-2 flex items-center justify-between gap-2">
        <h3 className="text-muted-foreground text-xs">Balance over time</h3>
        <div className="flex items-center gap-1.5">
          {zoom ? (
            <Button variant="ghost" size="xs" onClick={() => setZoom(null)} title="Back to the picked range (or double-click the chart)">
              ← Back
            </Button>
          ) : null}
          <Select value={range} onValueChange={pick}>
            <SelectTrigger size="sm" className="h-7 min-w-28" aria-label="Time range">
              <SelectValue />
            </SelectTrigger>
            <SelectContent align="end">
              {RANGES.map((r) => (
                <SelectItem key={r.value} value={r.value}>
                  {r.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
      {history.isLoading ? (
        <Skeleton className="h-44 w-full" />
      ) : data.length > 1 ? (
        <div className="select-none">
          <div ref={plotRef} className="touch-none" {...handlers}>
          <ChartContainer config={config} className="aspect-auto h-44 w-full cursor-crosshair">
            <AreaChart
              data={data}
              margin={{ left: 0, right: RIGHT_MARGIN, top: 4, bottom: 0 }}
            >
              <defs>
                <linearGradient id="fillBalance" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--color-balance)" stopOpacity={0.35} />
                  <stop offset="100%" stopColor="var(--color-balance)" stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid vertical={false} strokeOpacity={0.4} />
              <XAxis
                dataKey="ts"
                type="number"
                scale="time"
                domain={["dataMin", "dataMax"]}
                tickFormatter={(v) => tickLabel(v, span)}
                tickLine={false}
                axisLine={false}
                fontSize={10}
                minTickGap={40}
              />
              <YAxis tickFormatter={(v) => compact(v)} tickLine={false} axisLine={false} width={Y_AXIS} fontSize={10} />
              <ChartTooltip
                content={({ payload }) => {
                  const p = payload?.[0]?.payload as { ts: number; balance: number } | undefined
                  if (!p || dragging) return null
                  return (
                    <div className="bg-popover rounded-md border px-2 py-1.5 text-xs shadow-md">
                      <div className="text-muted-foreground">{new Date(p.ts).toLocaleString()}</div>
                      <div className="tabular font-medium">{credits(p.balance)}</div>
                    </div>
                  )
                }}
              />
              <Area
                dataKey="balance"
                type="stepAfter"
                stroke="var(--color-balance)"
                strokeWidth={1.5}
                fill="url(#fillBalance)"
                isAnimationActive={false}
              />
              {drag ? (
                <ReferenceArea x1={drag.a} x2={drag.b} fill="var(--color-balance)" fillOpacity={0.15} strokeOpacity={0} />
              ) : null}
            </AreaChart>
          </ChartContainer>
          </div>
          <p className="text-muted-foreground mt-1 text-[11px]">
            {zoom
              ? `${new Date(zoom.from * 1000).toLocaleString()} – ${new Date(zoom.to * 1000).toLocaleString()} · double-click to go back`
              : "Drag across the chart to zoom in."}
          </p>
        </div>
      ) : (
        <p className="text-muted-foreground text-xs">No balance changes in this period.</p>
      )}
    </section>
  )
}

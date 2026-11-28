import * as React from "react"
import { useInfiniteQuery } from "@tanstack/react-query"
import { BroadcastIcon } from "@phosphor-icons/react"
import type { ColumnDef } from "@tanstack/react-table"

import { api, type ActivityPage as Page, type ActivityRow, type Guild, type Person } from "@/app/api"
import { GAME_NAMES, MONEY_NAMES, credits, dateTime, displayName, gameName, moneyName, resizable, signed } from "@/app/format"
import { PlayerAvatar } from "@/app/player-avatar"
import { TimelineChart, type TimelineChartSeries } from "@/components/data-table/data-table-chart/timeline-chart"
import { DataTableFilterCommand } from "@/components/data-table/data-table-filter-command"
import { DataTableInfinite } from "@/components/data-table/data-table-infinite"
import { useDataTable } from "@/components/data-table/data-table-provider"
import { DataTableRefreshButton } from "@/components/data-table/data-table-refresh-button"
import { Button } from "@/components/ui/button"
import { TableCell, TableRow } from "@/components/ui/table"
import { bucketChartData } from "@/lib/data-table/chart-data"
import { useMemoryAdapter } from "@/lib/store/adapters/memory"
import { DataTableStoreProvider } from "@/lib/store/provider/DataTableStoreProvider"
import type { DataTableFeatures } from "@/lib/table/features"
import {
  col,
  createTableSchema,
  generateColumns,
  generateFilterFields,
  generateFilterSchema,
  getDefaultColumnVisibility,
} from "@/lib/table-schema"
import { cn } from "@/lib/utils"

const PAGE = 200
const LIVE_EVERY = 3000

const RESULTS = ["win", "loss", "even", "credit", "debit"] as const
type Result = (typeof RESULTS)[number]
const RESULT_INFO: Record<Result, { label: string; color: string }> = {
  win: { label: "Won", color: "var(--success)" },
  loss: { label: "Lost", color: "var(--error)" },
  even: { label: "Even", color: "var(--chart-other)" },
  credit: { label: "Credits in", color: "var(--info)" },
  debit: { label: "Credits out", color: "var(--warning)" },
}
const SERIES: TimelineChartSeries[] = RESULTS.map((r) => ({ key: r, label: RESULT_INFO[r].label, color: RESULT_INFO[r].color }))

function Dot({ result }: { result: Result }) {
  return <span className="inline-block size-2 shrink-0 rounded-full" style={{ background: RESULT_INFO[result].color }} />
}

const WHAT = [...Object.values(GAME_NAMES), ...Object.values(MONEY_NAMES)] as readonly string[]

type Row = {
  id: string
  ts: number
  date: Date
  result: Result
  player: string
  avatar: string | null
  what: string
  to: string | null
  bet: number
  payout: number
  change: number
}

const tableSchema = createTableSchema({
  result: col
    .enum(RESULTS)
    .label("Result")
    .display("custom", {
      cell: (v: unknown) => (
        <span className="flex justify-center">
          <Dot result={v as Result} />
        </span>
      ),
    })
    .filterable("checkbox", {
      options: RESULTS.map((r) => ({ label: RESULT_INFO[r].label, value: r })),
      component: (o: { label: string; value: unknown }) => (
        <span className="flex items-center gap-2">
          <Dot result={o.value as Result} />
          {o.label}
        </span>
      ),
    })
    .defaultOpen()
    .hideHeader()
    .size(36),
  date: col
    .timestamp()
    .label("Time")
    .display("custom", { cell: (v: unknown) => <span className="tabular text-muted-foreground">{dateTime((v as Date).getTime() / 1000)}</span> })
    .defaultOpen()
    .sortable(false)
    .size(170),
  player: col
    .string()
    .label("Player")
    .display("custom", {
      cell: (_: unknown, row: unknown) => {
        const r = row as Row
        return (
          <span className="flex min-w-0 items-center gap-2">
            <PlayerAvatar name={r.player} avatar={r.avatar} size={18} />
            <span className="truncate">{r.player}</span>
          </span>
        )
      },
    })
    .defaultOpen()
    .minSize(180),
  what: col
    .enum(WHAT)
    .label("What")
    .display("custom", {
      cell: (v: unknown, row: unknown): React.JSX.Element => {
        const r = row as Row
        return (
          <span className="truncate">
            {v as string}
            {r.to ? <span className="text-muted-foreground"> → {r.to}</span> : null}
          </span>
        )
      },
    })
    .filterable("checkbox", { options: WHAT.map((v) => ({ label: v, value: v })) })
    .size(200),
  bet: col
    .number()
    .label("Bet")
    .display("custom", { cell: (v: unknown) => ((v as number) ? <span className="tabular">{credits(v as number)}</span> : <span className="text-muted-foreground">—</span>) })
    .filterable("slider", { min: 0, max: 1000 })
    .sortable(false)
    .size(100),
  payout: col
    .number()
    .label("Paid")
    .display("custom", {
      cell: (v: unknown, row: unknown) =>
        (row as Row).bet ? <span className="tabular">{credits(v as number)}</span> : <span className="text-muted-foreground">—</span>,
    })
    .notFilterable()
    .sortable(false)
    .size(100),
  change: col
    .number()
    .label("Change")
    .display("custom", {
      cell: (v: unknown) => {
        const n = v as number
        return <span className={cn("tabular", n > 0 && "text-success", n < 0 && "text-error")}>{signed(n)}</span>
      },
    })
    .filterable("slider", { min: 0, max: 1000 })
    .sortable(false)
    .size(120),
})

const filterSchema = generateFilterSchema(tableSchema.definition)
const filterFields = generateFilterFields<Row>(tableSchema.definition)
const defaultColumnVisibility = getDefaultColumnVisibility(tableSchema.definition)
const columns: ColumnDef<DataTableFeatures, Row>[] = resizable(generateColumns<Row>(tableSchema.definition), ["result"])

/** One credit movement on a phone. */
function ActivityCard({ row }: { row: Row }) {
  return (
    <div className="flex items-center gap-3">
      <Dot result={row.result} />
      <PlayerAvatar name={row.player} avatar={row.avatar} size={28} />
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="truncate text-sm">
          {row.player}
          {row.to ? <span className="text-muted-foreground"> → {row.to}</span> : null}
        </span>
        <span className="text-muted-foreground truncate text-[11px]">
          {row.what} · {dateTime(row.ts)}
        </span>
      </span>
      <span className="flex shrink-0 flex-col items-end gap-0.5">
        <span className={cn("tabular text-sm font-medium", row.change > 0 && "text-success", row.change < 0 && "text-error")}>
          {signed(row.change)}
        </span>
        {row.bet ? <span className="text-muted-foreground tabular text-[11px]">bet {credits(row.bet)}</span> : null}
      </span>
    </div>
  )
}

const renderActivityCard = (row: { original: Row }) => <ActivityCard row={row.original} />

function toRow(r: ActivityRow, people: Record<string, Person>): Row {
  const who = r.user_id ? (people[r.user_id] ?? { name: null, avatar: null, in_server: false }) : null
  const result: Result =
    r.type === "game"
      ? r.delta > 0
        ? "win"
        : r.delta < 0
          ? "loss"
          : "even"
      : r.delta >= 0
        ? "credit"
        : "debit"
  const to = r.other_id ? displayName(people[r.other_id] ?? { name: null }, r.other_id) : null
  return {
    id: r.id,
    ts: r.ts,
    date: new Date(r.ts * 1000),
    result,
    player: who ? displayName(who, r.user_id) : "Everyone",
    avatar: who?.avatar ?? null,
    what: r.type === "game" ? gameName(r.kind) : moneyName(r.kind),
    bet: r.wagered ?? 0,
    payout: r.payout ?? 0,
    change: r.delta,
    to,
  }
}

// ---------------------------------------------------------------- the page

export function ActivityPage({ guild }: { guild: Guild }) {
  const adapter = useMemoryAdapter(filterSchema.definition, { id: "activity" })
  return (
    <DataTableStoreProvider adapter={adapter}>
      <ActivityTable guild={guild} />
    </DataTableStoreProvider>
  )
}

function ActivityTable({ guild }: { guild: Guild }) {
  const query = useInfiniteQuery({
    queryKey: ["activity", guild.id],
    queryFn: ({ pageParam }) =>
      api.get<Page>(`/api/activity?guild=${guild.id}&limit=${PAGE}${pageParam ? `&before=${pageParam}` : ""}`),
    initialPageParam: 0 as number,
    getNextPageParam: (last) => (last.rows.length < PAGE ? undefined : last.rows[last.rows.length - 1].ts),
    staleTime: Infinity,
  })

  // Live mode: new rows are fetched every few seconds and land above a divider.
  const [live, setLive] = React.useState(false)
  const [liveSince, setLiveSince] = React.useState<number | null>(null)
  const [fresh, setFresh] = React.useState<Row[]>([])
  const rows = React.useMemo(() => {
    const older = (query.data?.pages ?? []).flatMap((p) => p.rows.map((r) => toRow(r, p.people)))
    const seen = new Set(fresh.map((r) => r.id))
    return [...fresh, ...older.filter((r) => !seen.has(r.id))]
  }, [query.data, fresh])
  const newest = rows[0]?.ts ?? 0

  const newestRef = React.useRef(newest)
  newestRef.current = newest
  React.useEffect(() => {
    if (!live) return
    let stop = false
    const tick = async () => {
      try {
        const page = await api.get<Page>(`/api/activity?guild=${guild.id}&limit=500&after=${newestRef.current}`)
        if (!stop && page.rows.length) setFresh((prev) => [...page.rows.map((r) => toRow(r, page.people)), ...prev])
      } catch {
        // the next tick tries again
      }
    }
    const timer = window.setInterval(tick, LIVE_EVERY)
    tick()
    return () => {
      stop = true
      window.clearInterval(timer)
    }
  }, [live, guild.id])

  const toggleLive = () => {
    if (live) {
      setLive(false)
      setLiveSince(null)
    } else {
      setLiveSince(newestRef.current)
      setLive(true)
    }
  }

  const refresh = () => {
    setFresh([])
    query.refetch()
  }

  const getRowClassName = React.useCallback(
    (row: { original: Row }) => (liveSince !== null && row.original.ts <= liveSince ? "opacity-50" : ""),
    [liveSince]
  )

  const renderLiveRow = React.useCallback(
    (props?: { row: { original: Row; index: number } }) => {
      if (liveSince === null || !props) return null
      const { row } = props
      if (row.original.ts > liveSince) return null
      // only above the first row that was already there when live mode started
      const firstOld = rows.find((r) => r.ts <= liveSince)
      if (firstOld?.id !== row.original.id) return null
      return (
        <TableRow className="hover:bg-transparent">
          <TableCell colSpan={columns.length} className="text-info border-info/50 border-y py-1.5 text-center text-xs">
            Live mode: new activity above, since {dateTime(liveSince)}
          </TableCell>
        </TableRow>
      )
    },
    [liveSince, rows]
  )

  return (
    <DataTableInfinite
      columns={columns}
      data={rows}
      totalRowsFetched={rows.length}
      defaultColumnVisibility={defaultColumnVisibility}
      filterFields={filterFields}
      isFetching={query.isFetching}
      isLoading={query.isLoading}
      fetchNextPage={query.fetchNextPage}
      hasNextPage={query.hasNextPage}
      getRowId={(row) => row.id}
      getRowClassName={getRowClassName}
      renderLiveRow={renderLiveRow as never}
      tableId="activity"
      renderCard={renderActivityCard}
      commandSlot={<DataTableFilterCommand schema={filterSchema.definition} tableId="activity" />}
      chartSlot={<ActivityChart />}
      toolbarActions={
        <div className="flex items-center gap-2">
          <DataTableRefreshButton onClick={refresh} />
          <Button
            variant={live ? "default" : "outline"}
            size="sm"
            onClick={toggleLive}
            className={cn(live && "bg-info text-background hover:bg-info/90")}
          >
            <BroadcastIcon data-icon="inline-start" className={cn(live && "animate-pulse")} />
            Live
          </Button>
        </div>
      }
      footerSlot={
        <p className="text-muted-foreground text-[11px]">
          Every finished game and every credit movement outside games (claims, payments, shop, admin changes). Scroll
          down and press Load More for older activity.
        </p>
      }
    />
  )
}

/** The timeline above the table: the loaded (and filtered) rows per time bucket, by result. */
function ActivityChart() {
  const { table, columnFilters } = useDataTable<Row, unknown>()
  const filtered = table.getFilteredRowModel().rows
  const range = columnFilters.find((f) => f.id === "date")?.value as Date[] | undefined
  const data = React.useMemo(
    () =>
      bucketChartData(
        filtered.map((r) => r.original),
        { timestamp: (r) => r.date, series: (r) => r.result, keys: RESULTS, range }
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [filtered, range]
  )
  return <TimelineChart data={data} columnId="date" series={SERIES} className="-mb-2" />
}

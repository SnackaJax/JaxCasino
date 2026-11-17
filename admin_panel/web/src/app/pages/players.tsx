import * as React from "react"
import { useQuery } from "@tanstack/react-query"
import { PencilSimpleIcon } from "@phosphor-icons/react"
import type { ColumnDef } from "@tanstack/react-table"

import { api, type Guild, type Player } from "@/app/api"
import { EditBalanceDialog, type EditTarget } from "@/app/edit-balance"
import { ago, compact, credits, displayName, gameColor, gameName, pct, signed } from "@/app/format"
import { PlayerAvatar } from "@/app/player-avatar"
import { PlayerSheet } from "@/app/player-sheet"
import { DataTableFilterCommand } from "@/components/data-table/data-table-filter-command"
import { DataTableInfinite } from "@/components/data-table/data-table-infinite"
import { useDataTable } from "@/components/data-table/data-table-provider"
import { DataTableRefreshButton } from "@/components/data-table/data-table-refresh-button"
import { DataTableSheetDetails } from "@/components/data-table/data-table-sheet/data-table-sheet-details"
import { Button } from "@/components/ui/button"
import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card"
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

// ---------------------------------------------------------------- the row and its status

const STATUSES = ["active", "recent", "idle", "left"] as const
type Status = (typeof STATUSES)[number]
const STATUS_INFO: Record<Status, { label: string; color: string; hint: string }> = {
  active: { label: "Active", color: "var(--success)", hint: "played in the last 24 hours" },
  recent: { label: "This week", color: "var(--info)", hint: "played in the last 7 days" },
  idle: { label: "Idle", color: "var(--warning)", hint: "nothing for over a week" },
  left: { label: "Left", color: "var(--error)", hint: "no longer in the server" },
}

function statusOf(p: Player): Status {
  if (!p.in_server) return "left"
  const age = Date.now() / 1000 - (p.last_active ?? 0)
  return age < 86400 ? "active" : age < 7 * 86400 ? "recent" : "idle"
}

function StatusDot({ status }: { status: Status }) {
  return <span className="inline-block size-2 shrink-0 rounded-full" style={{ background: STATUS_INFO[status].color }} />
}

type PlayerRow = {
  id: string
  status: Status
  name: string
  balance: number
  share: number
  games: number
  winrate: number
  wagered: number
  net: number
  biggest_win: number
  last_active: Date
  avatar: string | null
  mix: Record<string, number>
  player: Player
}

/** Opens the balance editor for a row; set by the page, read by the balance cell. */
const EditContext = React.createContext<(id: string) => void>(() => {})

const tableSchema = createTableSchema({
  status: col
    .enum(STATUSES)
    .label("Status")
    .display("custom", {
      cell: (value: unknown) => (
        <span className="flex justify-center" title={STATUS_INFO[value as Status].hint}>
          <StatusDot status={value as Status} />
        </span>
      ),
    })
    .filterable("checkbox", {
      options: STATUSES.map((s) => ({ label: STATUS_INFO[s].label, value: s })),
      component: (o: { label: string; value: unknown }) => (
        <span className="flex items-center gap-2">
          <StatusDot status={o.value as Status} />
          {o.label}
        </span>
      ),
    })
    .defaultOpen()
    .hideHeader()
    .size(36),
  name: col
    .string()
    .label("Player")
    .display("custom", {
      cell: (_: unknown, row: unknown) => {
        const r = row as PlayerRow
        return (
          <span className="flex min-w-0 items-center gap-2">
            <PlayerAvatar name={r.name} avatar={r.avatar} size={20} />
            <span className="truncate">{r.name}</span>
          </span>
        )
      },
    })
    .defaultOpen()
    .minSize(200),
  balance: col
    .number()
    .label("Balance")
    .display("custom", { cell: (_: unknown, row: unknown) => <BalanceCell row={row as PlayerRow} /> })
    .filterable("slider", { min: 0, max: 1000 })
    .defaultOpen()
    .sortable()
    .size(150),
  share: col
    .number()
    .label("Share")
    .display("custom", {
      cell: (value: unknown) => <span className="tabular text-muted-foreground">{(value as number).toFixed(1)}%</span>,
    })
    .notFilterable()
    .sortable()
    .size(80),
  games: col
    .number()
    .label("Games")
    .display("custom", { cell: (v: unknown) => <span className="tabular">{credits(v as number)}</span> })
    .filterable("slider", { min: 0, max: 1000 })
    .sortable()
    .size(90),
  winrate: col
    .number()
    .label("Win %")
    .display("custom", {
      cell: (v: unknown, row: unknown) =>
        (row as PlayerRow).games ? <span className="tabular">{(v as number).toFixed(0)}%</span> : <Dash />,
    })
    .filterable("slider", { min: 0, max: 1000 })
    .sortable()
    .size(80),
  wagered: col
    .number()
    .label("Bet")
    .display("custom", { cell: (v: unknown) => <span className="tabular">{compact(v as number)}</span> })
    .filterable("slider", { min: 0, max: 1000 })
    .sortable()
    .size(90),
  net: col
    .number()
    .label("Net")
    .display("custom", {
      cell: (v: unknown): React.JSX.Element => {
        const n = v as number
        return <span className={cn("tabular", n > 0 && "text-success", n < 0 && "text-error")}>{signed(n)}</span>
      },
    })
    .filterable("slider", { min: 0, max: 1000 })
    .sortable()
    .size(110),
  biggest_win: col
    .number()
    .label("Best win")
    .display("custom", { cell: (v: unknown) => ((v as number) ? <span className="tabular">{compact(v as number)}</span> : <Dash />) })
    .notFilterable()
    .sortable()
    .hidden()
    .size(90),
  last_active: col
    .timestamp()
    .label("Last active")
    .display("custom", {
      cell: (v: unknown) => {
        const t = (v as Date).getTime()
        return <span className="text-muted-foreground">{t ? ago(t / 1000) : "never"}</span>
      },
    })
    .sortable()
    .size(110),
  id: col.string().label("Discord ID").hidden().size(170),
})


const filterSchema = generateFilterSchema(tableSchema.definition)
const filterFields = generateFilterFields<PlayerRow>(tableSchema.definition)
const defaultColumnVisibility = getDefaultColumnVisibility(tableSchema.definition)
const columns: ColumnDef<DataTableFeatures, PlayerRow>[] = [
  ...generateColumns<PlayerRow>(tableSchema.definition),
  gameMixColumn(),
]

function toRows(players: Player[]): PlayerRow[] {
  const total = players.reduce((s, p) => s + p.balance, 0)
  return players.map((p) => ({
    id: p.id,
    status: statusOf(p),
    name: displayName(p, p.id),
    balance: p.balance,
    share: total ? (p.balance / total) * 100 : 0,
    games: p.games,
    winrate: p.games ? (p.wins / p.games) * 100 : 0,
    wagered: p.wagered,
    net: p.net,
    biggest_win: p.biggest_win,
    last_active: new Date((p.last_active ?? 0) * 1000),
    avatar: p.avatar,
    mix: p.mix,
    player: p,
  }))
}

// ---------------------------------------------------------------- cells

function Dash() {
  return <span className="text-muted-foreground">—</span>
}

function BalanceCell({ row }: { row: PlayerRow }) {
  const edit = React.useContext(EditContext)
  return (
    <span className="group/bal flex items-center justify-between gap-2">
      <span className="tabular font-medium">{credits(row.balance)}</span>
      <Button
        variant="ghost"
        size="icon-xs"
        aria-label={`Edit ${row.name}'s balance`}
        className="opacity-40 group-hover/bal:opacity-100 focus-visible:opacity-100"
        onClick={(e) => {
          e.stopPropagation()
          edit(row.id)
        }}
      >
        <PencilSimpleIcon />
      </Button>
    </span>
  )
}

/** Which games a player puts their credits into, as one stacked bar (like the reference's timing phases). */
function gameMixColumn(): ColumnDef<DataTableFeatures, PlayerRow> {
  return {
    id: "mix",
    header: () => <div className="whitespace-nowrap">Games played</div>,
    cell: ({ row }) => {
      const mix = Object.entries(row.original.mix).sort((a, b) => b[1] - a[1])
      const total = mix.reduce((s, [, v]) => s + v, 0)
      if (!total) return <Dash />
      return (
        <HoverCard openDelay={100} closeDelay={50}>
          <HoverCardTrigger asChild>
            <div className="flex h-4 w-full overflow-hidden rounded-[3px] opacity-80 hover:opacity-100">
              {mix.map(([game, v]) => (
                <div key={game} style={{ width: `${(v / total) * 100}%`, background: gameColor(game) }} />
              ))}
            </div>
          </HoverCardTrigger>
          <HoverCardContent side="bottom" align="end" className="w-64 p-2">
            <div className="flex flex-col gap-1">
              {mix.map(([game, v]) => (
                <div key={game} className="grid grid-cols-[1fr_auto_auto] items-center gap-3 text-xs">
                  <span className="flex items-center gap-2">
                    <span className="size-2 rounded-full" style={{ background: gameColor(game) }} />
                    {gameName(game)}
                  </span>
                  <span className="text-muted-foreground tabular">{pct(v, total, 0)}</span>
                  <span className="tabular w-14 text-right">{compact(v)}</span>
                </div>
              ))}
            </div>
          </HoverCardContent>
        </HoverCard>
      )
    },
    enableResizing: false,
    enableSorting: false,
    size: 160,
    minSize: 160,
    maxSize: 160,
    meta: { label: "Games played" },
  }
}

const getRowClassName = (row: { original: PlayerRow }) =>
  row.original.status === "left" ? "text-muted-foreground" : ""

// ---------------------------------------------------------------- the page

export function PlayersPage({ guild }: { guild: Guild }) {
  const adapter = useMemoryAdapter(filterSchema.definition, { id: "players" })
  return (
    <DataTableStoreProvider adapter={adapter}>
      <PlayersTable guild={guild} />
    </DataTableStoreProvider>
  )
}

function PlayersTable({ guild }: { guild: Guild }) {
  const players = useQuery({
    queryKey: ["players", guild.id],
    queryFn: () => api.get<Player[]>(`/api/players?guild=${guild.id}`),
    refetchInterval: 30_000,
  })
  const rows = React.useMemo(() => toRows(players.data ?? []), [players.data])
  const [editing, setEditing] = React.useState<EditTarget | null>(null)

  const openEditor = React.useCallback(
    (id: string) => {
      const r = rows.find((x) => x.id === id)
      if (r) setEditing({ id: r.id, name: r.name, avatar: r.avatar, balance: r.balance })
    },
    [rows]
  )

  return (
    <EditContext.Provider value={openEditor}>
      <DataTableInfinite
        columns={columns}
        data={rows}
        totalRows={rows.length}
        totalRowsFetched={rows.length}
        defaultColumnSorting={[{ id: "balance", desc: true }]}
        defaultColumnVisibility={defaultColumnVisibility}
        filterFields={filterFields}
        isFetching={players.isFetching}
        isLoading={players.isLoading}
        fetchNextPage={async () => undefined as never}
        hasNextPage={false}
        getRowId={(row) => row.id}
        getRowClassName={getRowClassName}
        tableId="players"
        commandSlot={<DataTableFilterCommand schema={filterSchema.definition} tableId="players" />}
        toolbarActions={<DataTableRefreshButton onClick={() => players.refetch()} />}
        footerSlot={<StatusLegend />}
        sheetSlot={<Sheet guildId={guild.id} onEdit={openEditor} />}
      />
      <EditBalanceDialog guildId={guild.id} target={editing} onClose={() => setEditing(null)} />
    </EditContext.Provider>
  )
}

function Sheet({ guildId, onEdit }: { guildId: string; onEdit: (id: string) => void }) {
  const { table, rowSelection } = useDataTable<PlayerRow, unknown>()
  const id = Object.keys(rowSelection)[0]
  const row = id ? table.getCoreRowModel().flatRows.find((r) => r.id === id)?.original : undefined
  return (
    <DataTableSheetDetails
      title={
        row ? (
          <span className="flex items-center gap-2">
            <PlayerAvatar name={row.name} avatar={row.avatar} size={22} />
            <span className="truncate">{row.name}</span>
          </span>
        ) : undefined
      }
    >
      {row ? <PlayerSheet guildId={guildId} player={row.player} onEdit={() => onEdit(row.id)} /> : null}
    </DataTableSheetDetails>
  )
}

function StatusLegend() {
  return (
    <ul className="text-muted-foreground flex flex-col gap-1 text-[11px]">
      {STATUSES.map((s) => (
        <li key={s} className="flex items-center gap-2">
          <StatusDot status={s} />
          <span className="text-foreground">{STATUS_INFO[s].label}</span> {STATUS_INFO[s].hint}
        </li>
      ))}
    </ul>
  )
}

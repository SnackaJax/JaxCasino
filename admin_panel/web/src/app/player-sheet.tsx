import * as React from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { PencilSimpleIcon } from "@phosphor-icons/react"

import { api, ApiError, type Player, type PlayerDetail } from "@/app/api"
import { BalanceChart } from "@/app/balance-chart"
import { PlayerDaily, PlayerHeatmap, PlayerHistory, PlayerMix, usePlayerInsights } from "@/app/player-insights"
import { ago, compact, credits, duration, moneyName, pct, signed } from "@/app/format"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

/** One player's details in the side sheet: balance, history, games and the edits made to them. */
export function PlayerSheet(props: { guildId: string; player: Player; onEdit: () => void }) {
  // a value the sheet doesn't expect breaks only the sheet, not the whole page
  return (
    <SheetBoundary key={props.player.id}>
      <PlayerSheetBody {...props} />
    </SheetBoundary>
  )
}

class SheetBoundary extends React.Component<{ children: React.ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() {
    return { failed: true }
  }
  componentDidCatch(error: unknown) {
    console.error("Player sheet:", error)
  }
  render() {
    return this.state.failed ? (
      <p className="text-muted-foreground p-4 text-xs">Couldn't show this player's details.</p>
    ) : (
      this.props.children
    )
  }
}

function PlayerSheetBody({ guildId, player, onEdit }: { guildId: string; player: Player; onEdit: () => void }) {
  const detail = useQuery({
    queryKey: ["player", guildId, player.id],
    queryFn: () => api.get<PlayerDetail>(`/api/players/${player.id}?guild=${guildId}`),
  })
  const d = detail.data
  const extra = usePlayerInsights(guildId, player.id).data

  return (
    <div className="flex flex-col gap-5 p-4">
      <div className="flex items-end justify-between gap-3">
        <div>
          <div className="text-muted-foreground text-xs">Balance</div>
          <div className="tabular text-3xl font-semibold">{credits(player.balance)}</div>
          {player.in_play ? <div className="text-muted-foreground text-xs">+{credits(player.in_play)} in play</div> : null}
        </div>
        <Button onClick={onEdit} size="sm">
          <PencilSimpleIcon data-icon="inline-start" /> Edit balance
        </Button>
      </div>

      <CruxBanner guildId={guildId} player={player} />

      <div className="flex flex-wrap gap-1.5">
        {player.username ? <Badge variant="outline">@{player.username}</Badge> : null}
        <Badge variant="outline" className="tabular">
          {player.id}
        </Badge>
        {!player.in_server ? <Badge variant="destructive">Left the server</Badge> : null}
        <Badge variant="secondary">Active {ago(player.last_active)}</Badge>
      </div>

      <BalanceChart guildId={guildId} playerId={player.id} />
      <PlayerDaily d={extra} />

      <section className="grid grid-cols-3 gap-2">
        <Stat label="Games" value={credits(player.games)} />
        <Stat label="Win rate" value={pct(player.wins, player.games, 0)} />
        <Stat label="Wagered" value={compact(player.wagered)} title={`${credits(player.wagered)} wagered in total`} />
        <Stat label="Paid back" value={compact(player.won)} />
        <Stat label="Net" value={signed(player.net)} tone={player.net > 0 ? "good" : player.net < 0 ? "bad" : undefined} />
        <Stat label="Best win" value={compact(player.biggest_win)} />
        <Stat
          label="Time played"
          value={duration(player.played)}
          title={`${duration(player.played_week)} in the last 7 days. Counted from when their games happened; breaks of more than 5 minutes don't count.`}
        />
        <Stat label="This week" value={duration(player.played_week)} />
        <Stat label="Avg bet" value={player.games ? compact(player.wagered / player.games) : "—"} />
      </section>

      <PlayerMix d={extra} />
      <PlayerHistory d={extra} />
      <PlayerHeatmap d={extra} />

      {d && Object.keys(d.stats.money).length ? (
        <section>
          <h3 className="text-muted-foreground mb-2 text-xs">Credits outside games</h3>
          <ul className="flex flex-col gap-1.5">
            {Object.entries(d.stats.money).map(([kind, m]) => (
              <li key={kind} className="flex justify-between text-xs">
                <span>
                  {kind === "received" ? "Payments received" : moneyName(kind)}{" "}
                  <span className="text-muted-foreground">({m.n}×)</span>
                </span>
                <span className="tabular">{signed(m.amount)}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section>
        <h3 className="text-muted-foreground mb-2 text-xs">Balance edits from this panel</h3>
        {d?.edits.length ? (
          <ul className="flex flex-col gap-2">
            {d.edits.map((e) => (
              <li key={e.id} className="text-xs">
                <div className="flex justify-between gap-2">
                  <span className="tabular">
                    {credits(e.before)} → {credits(e.after)}
                  </span>
                  <span className="text-muted-foreground">{ago(e.ts)}</span>
                </div>
                {e.note ? <div className="text-muted-foreground">{e.note}</div> : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-muted-foreground text-xs">None.</p>
        )}
      </section>
    </div>
  )
}

function Stat({ label, value, tone, title }: { label: string; value: string; tone?: "good" | "bad"; title?: string }) {
  return (
    <div className="bg-muted/40 rounded-md border px-2.5 py-2" title={title}>
      <div className="text-muted-foreground text-[11px]">{label}</div>
      <div className={cn("tabular text-sm font-medium", tone === "good" && "text-success", tone === "bad" && "text-error")}>
        {value}
      </div>
    </div>
  )
}

const CRUX_PRESETS = ["1h", "12h", "1d", "3d", "1w", "30d"]

/** The player's /crux (a break from the casino): give one, change its length, or end it early (also /admin uncrux). */
function CruxBanner({ guildId, player }: { guildId: string; player: Player }) {
  const qc = useQueryClient()
  const until = player.crux_until ?? 0
  const active = until > Date.now() / 1000
  const [open, setOpen] = React.useState(false)
  const [length, setLength] = React.useState("1d")
  const refresh = () => qc.invalidateQueries({ queryKey: ["players"] })
  const end = useMutation({
    mutationFn: () => api.post(`/api/players/${player.id}/crux/remove`, { guild: guildId }),
    onSuccess: refresh,
  })
  const give = useMutation({
    mutationFn: (duration: string) => api.post(`/api/players/${player.id}/crux`, { guild: guildId, duration }),
    onSuccess: () => {
      setOpen(false)
      return refresh()
    },
  })
  const failed = end.error ?? give.error
  const error = failed ? (
    <div className="text-destructive mt-1 text-xs">{failed instanceof ApiError ? failed.message : String(failed)}</div>
  ) : null

  const form = open ? (
    <form
      className="flex flex-col gap-2 border-t pt-2.5"
      onSubmit={(e) => {
        e.preventDefault()
        give.mutate(length)
      }}
    >
      <div className="flex flex-wrap gap-1">
        {CRUX_PRESETS.map((p) => (
          <Button key={p} type="button" size="xs" variant={length === p ? "default" : "outline"} onClick={() => setLength(p)}>
            {p}
          </Button>
        ))}
      </div>
      <div className="flex items-center gap-2">
        <Input
          value={length}
          onChange={(e) => setLength(e.target.value)}
          placeholder="e.g. 2h, 3d, 1w or 1d12h"
          aria-label="Crux length"
          className="w-40"
        />
        <Button type="submit" size="sm" disabled={give.isPending || !length.trim()}>
          {give.isPending ? "Saving…" : active ? "Set new length" : "Give crux"}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
      <p className="text-muted-foreground text-[11px]">
        {active ? "The new length counts from now. " : ""}10 minutes up to a year. Their open games close and show the crux
        screen; no games or commands for them until it ends.
      </p>
    </form>
  ) : null

  if (!active) {
    return (
      <div className="flex flex-col gap-2.5 rounded-md border px-3 py-2.5">
        <div className="flex items-center justify-between gap-3">
          <div className="text-muted-foreground text-xs">🧊 No crux</div>
          {!open ? (
            <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
              Give crux
            </Button>
          ) : null}
        </div>
        {form}
        {error}
      </div>
    )
  }
  return (
    <div className="border-info/40 bg-info/10 flex flex-col gap-2.5 rounded-md border px-3 py-2.5">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0 text-xs">
          <div className="font-medium">🧊 On a crux: {duration(until - Date.now() / 1000)} left</div>
          <div className="text-muted-foreground">
            Until {new Date(until * 1000).toLocaleString()}
            {player.crux_started ? ` · started ${ago(player.crux_started)}` : ""}. No games or commands for them until then.
          </div>
        </div>
        <div className="flex shrink-0 gap-1.5">
          {!open ? (
            <Button size="sm" variant="ghost" onClick={() => setOpen(true)}>
              Change
            </Button>
          ) : null}
          <Button size="sm" variant="outline" disabled={end.isPending} onClick={() => end.mutate()}>
            {end.isPending ? "Ending…" : "Remove crux"}
          </Button>
        </div>
      </div>
      {form}
      {error}
    </div>
  )
}

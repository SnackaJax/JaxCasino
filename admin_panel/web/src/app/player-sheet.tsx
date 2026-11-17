import * as React from "react"
import { useQuery } from "@tanstack/react-query"
import { PencilSimpleIcon } from "@phosphor-icons/react"

import { api, type Player, type PlayerDetail } from "@/app/api"
import { BalanceChart } from "@/app/balance-chart"
import { ago, compact, credits, gameColor, gameName, moneyName, pct, signed } from "@/app/format"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
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

      <div className="flex flex-wrap gap-1.5">
        {player.username ? <Badge variant="outline">@{player.username}</Badge> : null}
        <Badge variant="outline" className="tabular">
          {player.id}
        </Badge>
        {!player.in_server ? <Badge variant="destructive">Left the server</Badge> : null}
        <Badge variant="secondary">Active {ago(player.last_active)}</Badge>
      </div>

      <BalanceChart guildId={guildId} playerId={player.id} />

      <section className="grid grid-cols-3 gap-2">
        <Stat label="Games" value={credits(player.games)} />
        <Stat label="Win rate" value={pct(player.wins, player.games, 0)} />
        <Stat label="Wagered" value={compact(player.wagered)} title={`${credits(player.wagered)} wagered in total`} />
        <Stat label="Paid back" value={compact(player.won)} />
        <Stat label="Net" value={signed(player.net)} tone={player.net > 0 ? "good" : player.net < 0 ? "bad" : undefined} />
        <Stat label="Best win" value={compact(player.biggest_win)} />
      </section>

      {d?.stats.per_game.length ? (
        <section>
          <h3 className="text-muted-foreground mb-2 text-xs">Per game</h3>
          <ul className="flex flex-col gap-1.5">
            {[...d.stats.per_game]
              .sort((a, b) => b.wagered - a.wagered)
              .map((g) => (
                <li key={g.game} className="grid grid-cols-[1fr_auto_auto_auto] items-center gap-3 text-xs">
                  <span className="flex items-center gap-2">
                    <span className="size-2 rounded-full" style={{ background: gameColor(g.game) }} />
                    {gameName(g.game)}
                  </span>
                  <span className="text-muted-foreground tabular">{credits(g.plays)}×</span>
                  <span className="tabular w-14 text-right">{compact(g.wagered)}</span>
                  <span className={cn("tabular w-20 text-right", g.net > 0 ? "text-success" : g.net < 0 ? "text-error" : "")}>
                    {signed(g.net)}
                  </span>
                </li>
              ))}
          </ul>
        </section>
      ) : null}

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

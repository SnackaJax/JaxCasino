import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { api, ApiError, type GameSettings, type Guild } from "@/app/api"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

/** Game switches for one server. A change is saved and passed to the running games straight away. */
export function SettingsPage({ guild }: { guild: Guild }) {
  const qc = useQueryClient()
  const key = ["settings", guild.id]
  const settings = useQuery({ queryKey: key, queryFn: () => api.get<GameSettings>(`/api/settings?guild=${guild.id}`) })
  const change = useMutation({
    mutationFn: (v: { key: keyof GameSettings; value: GameSettings[keyof GameSettings] }) =>
      api.post<{ key: string; value: unknown }>("/api/settings", { guild: guild.id, ...v }),
    onSuccess: (res) => qc.setQueryData<GameSettings>(key, (s) => (s ? { ...s, [res.key]: res.value } : s)),
  })

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 p-4 md:p-6">
        <div>
          <h1 className="text-lg font-semibold">Game settings</h1>
          <p className="text-muted-foreground text-sm">Switches for {guild.name}. Players see a change straight away.</p>
        </div>
        {settings.isLoading ? (
          <Skeleton className="h-40" />
        ) : settings.isError ? (
          <p className="text-destructive text-sm">Couldn't load the settings: {String(settings.error)}</p>
        ) : (
          <Card>
            <CardHeader className="flex flex-row items-start justify-between gap-4">
              <div className="flex flex-col gap-1.5">
                <CardTitle className="flex items-center gap-2">
                  Blackjack: double side bets
                  {settings.data?.bj_double_side ? <Badge className="bg-pink-600 text-white">2× on</Badge> : null}
                </CardTitle>
                <CardDescription>
                  Perfect Pairs and 21+3 wins in the Activity's blackjack are paid out as usual and then doubled: 100
                  on a mixed pair (6:1) comes back as 1,400 instead of 700. Players see a neon “2× SIDE BETS” sign in
                  the blackjack lobby and on the tables. It starts or stops from the next deal; a round that's already
                  dealt pays the way it was dealt.
                </CardDescription>
              </div>
              <Switch
                label="Blackjack double side bets"
                on={!!settings.data?.bj_double_side}
                busy={change.isPending}
                onChange={(value) => change.mutate({ key: "bj_double_side", value })}
              />
            </CardHeader>
            <CardContent className="flex flex-col gap-2">
              <p className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm">
                <b>The house loses on side bets while this is on.</b> Doubled, Perfect Pairs pays back about 188% and
                21+3 about 191% of what's bet on them (normally 94% and 95.5%), so every 100 on a side bet costs the
                house about 90 on average. Use it for short promotions and switch it off afterwards.
              </p>
              {change.isError ? (
                <p className="text-destructive text-xs">
                  {change.error instanceof ApiError ? change.error.message : String(change.error)}
                </p>
              ) : null}
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  )
}

function Switch({ label, on, busy, onChange }: { label: string; on: boolean; busy: boolean; onChange: (on: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      disabled={busy}
      onClick={() => onChange(!on)}
      className={cn(
        "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full border transition-colors",
        "focus-visible:ring-ring focus-visible:ring-2 focus-visible:outline-none disabled:opacity-60",
        on ? "bg-primary border-primary" : "bg-muted border-border"
      )}
    >
      <span
        className={cn("bg-background inline-block size-4 rounded-full shadow transition-transform", on ? "translate-x-6" : "translate-x-1")}
      />
    </button>
  )
}

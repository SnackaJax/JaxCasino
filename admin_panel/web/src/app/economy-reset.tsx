import * as React from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { WarningIcon } from "@phosphor-icons/react"
import { toast } from "sonner"

import { api, ApiError, type Guild, type ResetPreview, type ResetResult } from "@/app/api"
import { credits } from "@/app/format"
import { PlayerAvatar } from "@/app/player-avatar"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

/** 0: closed, 1-3: the three "are you sure" steps */
type Step = 0 | 1 | 2 | 3

/**
 * The "Danger zone" card: resets every player in the server to the starting balance, after three confirmations.
 * The last one needs the server's name typed. Cancel at any step stops it. The server refuses while credits are
 * in play (open bets, poker chips) and also needs the confirm phrase it handed out with the preview.
 */
export function EconomyReset({ guild }: { guild: Guild }) {
  const qc = useQueryClient()
  const [step, setStep] = React.useState<Step>(0)
  const [typed, setTyped] = React.useState("")
  const [last, setLast] = React.useState<ResetResult | null>(null)
  const preview = useQuery({
    queryKey: ["economy-reset", guild.id],
    queryFn: () => api.get<ResetPreview>(`/api/economy/reset?guild=${guild.id}`),
    enabled: step > 0,
    staleTime: 0,
  })
  const reset = useMutation({
    mutationFn: () =>
      api.post<ResetResult>("/api/economy/reset", { guild: guild.id, confirm: preview.data?.confirm }),
    onSuccess: (res) => {
      setLast(res)
      setStep(0)
      toast.success(`Economy reset: ${credits(res.players)} players now have ${credits(res.starting_balance)}`, {
        description: `${credits(res.changed)} balances changed. Nothing was posted in Discord.`,
      })
      for (const key of ["players", "overview", "player", "edits", "activity", "guilds", "insights"]) {
        qc.invalidateQueries({ queryKey: [key] })
      }
    },
    onError: (err) => {
      if (err instanceof ApiError && err.status === 409) preview.refetch()
      toast.error(err instanceof ApiError ? err.message : "Couldn't reset the economy.")
    },
  })

  const open = (s: Step) => {
    setTyped("")
    setStep(s)
  }
  const close = () => {
    if (!reset.isPending) setStep(0)
  }
  const p = preview.data
  const busy = !!p && p.in_play.length > 0
  const nameOk = typed.trim().toLowerCase() === guild.name.trim().toLowerCase()

  return (
    <Card className="border-destructive/50">
      <CardHeader>
        <CardTitle className="text-destructive flex items-center gap-2">
          <WarningIcon className="size-5" weight="fill" />
          Danger zone
        </CardTitle>
        <CardDescription>
          Reset the economy: every player in {guild.name} gets the starting balance, whatever they have now. Each change
          is logged as an admin edit. Lottery, stats, streaks and shop items stay as they are. This can't be undone.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col items-start gap-3">
        <Button variant="destructive" onClick={() => open(1)}>
          Reset economy…
        </Button>
        {last ? (
          <p className="text-muted-foreground text-sm">
            Last reset: <b className="text-foreground">{credits(last.players)}</b> players set to{" "}
            {credits(last.starting_balance)} ({credits(last.changed)} changed; credits held {credits(last.before)} →{" "}
            {credits(last.after)}).
          </p>
        ) : null}
      </CardContent>

      <Dialog open={step > 0} onOpenChange={(o) => !o && close()}>
        <DialogContent className="sm:max-w-md">
          {preview.isLoading || (!p && !preview.isError) ? (
            <DialogHeader>
              <DialogTitle>Reset the economy?</DialogTitle>
              <DialogDescription>Counting the players in {guild.name}…</DialogDescription>
            </DialogHeader>
          ) : preview.isError || !p ? (
            <>
              <DialogHeader>
                <DialogTitle>Reset the economy?</DialogTitle>
                <DialogDescription className="text-destructive">
                  Couldn't load the numbers: {String(preview.error)}
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <Button variant="ghost" onClick={close}>
                  Close
                </Button>
              </DialogFooter>
            </>
          ) : busy ? (
            <InPlay preview={p} guild={guild} onClose={close} onRetry={() => preview.refetch()} />
          ) : step === 1 ? (
            <>
              <DialogHeader>
                <DialogTitle>Reset the economy of {guild.name}?</DialogTitle>
                <DialogDescription>Step 1 of 3.</DialogDescription>
              </DialogHeader>
              <p className="text-sm">
                All <b>{credits(p.players)}</b> players in <b>{guild.name}</b> will have exactly{" "}
                <b className="tabular">{credits(p.starting_balance)}</b> credits, the starting balance. That changes{" "}
                {credits(p.changed)} of them.
              </p>
              <DialogFooter>
                <Button variant="ghost" onClick={close}>
                  Cancel
                </Button>
                <Button variant="destructive" onClick={() => open(2)} disabled={p.players === 0}>
                  Yes, continue
                </Button>
              </DialogFooter>
            </>
          ) : step === 2 ? (
            <>
              <DialogHeader>
                <DialogTitle>Are you really sure?</DialogTitle>
                <DialogDescription>Step 2 of 3. There's no undo.</DialogDescription>
              </DialogHeader>
              <div className="bg-muted/50 flex flex-col gap-1 rounded-md border px-3 py-2 text-sm tabular">
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Credits held now</span>
                  <span>{credits(p.before)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">After the reset</span>
                  <span className="font-medium">{credits(p.after)}</span>
                </div>
              </div>
              <p className="text-sm">
                Every balance in <b>{guild.name}</b> is overwritten, the rich and the broke alike. Players aren't told
                in Discord.
              </p>
              <DialogFooter>
                <Button variant="ghost" onClick={close}>
                  Cancel
                </Button>
                <Button variant="destructive" onClick={() => open(3)}>
                  I'm sure, continue
                </Button>
              </DialogFooter>
            </>
          ) : (
            <form
              className="flex flex-col gap-4"
              onSubmit={(e) => {
                e.preventDefault()
                if (nameOk && !reset.isPending) reset.mutate()
              }}
            >
              <DialogHeader>
                <DialogTitle>Last chance</DialogTitle>
                <DialogDescription>
                  Step 3 of 3. Type the server's name to reset {credits(p.players)} players to{" "}
                  {credits(p.starting_balance)}.
                </DialogDescription>
              </DialogHeader>
              <div className="flex flex-col gap-2">
                <Label htmlFor="reset-name">
                  Type <b className="text-foreground">{guild.name}</b>
                </Label>
                <Input
                  id="reset-name"
                  autoComplete="off"
                  autoFocus
                  value={typed}
                  onChange={(e) => setTyped(e.target.value)}
                  placeholder={guild.name}
                />
              </div>
              <DialogFooter>
                <Button type="button" variant="ghost" onClick={close} disabled={reset.isPending}>
                  Cancel
                </Button>
                <Button type="submit" variant="destructive" disabled={!nameOk || reset.isPending}>
                  {reset.isPending ? "Resetting…" : "Reset the economy"}
                </Button>
              </DialogFooter>
            </form>
          )}
        </DialogContent>
      </Dialog>
    </Card>
  )
}

function InPlay({
  preview,
  guild,
  onClose,
  onRetry,
}: {
  preview: ResetPreview
  guild: Guild
  onClose: () => void
  onRetry: () => void
}) {
  return (
    <>
      <DialogHeader>
        <DialogTitle>Can't reset right now</DialogTitle>
        <DialogDescription>
          These players in {guild.name} have credits in play (open bets, poker chips). Their games would pay out on top
          of the reset, so wait until they're done.
        </DialogDescription>
      </DialogHeader>
      <ul className="flex max-h-60 flex-col gap-2 overflow-y-auto text-sm">
        {preview.in_play.map((r) => (
          <li key={r.user_id} className="flex items-center gap-2">
            <PlayerAvatar name={r.name} avatar={r.avatar} size={22} />
            <span className="flex-1 truncate">{r.name ?? r.user_id}</span>
            <span className="tabular">{credits(r.amount)}</span>
          </li>
        ))}
      </ul>
      <DialogFooter>
        <Button variant="ghost" onClick={onClose}>
          Close
        </Button>
        <Button onClick={onRetry}>Check again</Button>
      </DialogFooter>
    </>
  )
}

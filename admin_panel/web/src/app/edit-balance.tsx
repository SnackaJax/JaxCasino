import * as React from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { ArrowRightIcon } from "@phosphor-icons/react"
import { toast } from "sonner"

import { api, ApiError } from "@/app/api"
import { credits, creditsBig, parseAmount, parseAmountExact, signed, signedBig, toBig } from "@/app/format"
import { PlayerAvatar } from "@/app/player-avatar"
import { Button } from "@/components/ui/button"
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
import { Textarea } from "@/components/ui/textarea"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { cn } from "@/lib/utils"

type Action = "set" | "add" | "remove"

export type EditTarget = { id: string; name: string; avatar: string | null; balance: number }

const ON = "flex-1 data-[state=on]:bg-primary data-[state=on]:text-primary-foreground"

const VERB: Record<Action, string> = { set: "Set balance", add: "Add credits", remove: "Remove credits" }

/** Change one player's balance. Nothing is posted in Discord; the change is logged with the optional note. */
export function EditBalanceDialog({
  guildId,
  target,
  onClose,
}: {
  guildId: string
  target: EditTarget | null
  onClose: () => void
}) {
  const qc = useQueryClient()
  // keep showing the player while the dialog animates closed
  const last = React.useRef<EditTarget | null>(null)
  if (target) last.current = target
  const shown = target ?? last.current
  const [action, setAction] = React.useState<Action>("add")
  const [raw, setRaw] = React.useState("")
  const [note, setNote] = React.useState("")

  React.useEffect(() => {
    if (target) {
      setAction("add")
      setRaw("")
      setNote("")
    }
  }, [target])

  const amount = parseAmount(raw)
  const exactAmount = parseAmountExact(raw) // what's sent: numbers past 2^53 aren't exact in JavaScript
  const before = shown?.balance ?? 0
  const after =
    amount === null ? null : action === "set" ? amount : action === "add" ? before + amount : Math.max(0, before - amount)
  const valid = amount !== null && (action === "set" || amount > 0) && after !== before
  // the same sum to the credit, for the preview (numbers past 2^53 aren't exact in JavaScript)
  const beforeBig = toBig(before)
  const amountBig = exactAmount === null ? null : BigInt(exactAmount)
  const afterBig =
    amountBig === null
      ? null
      : action === "set"
        ? amountBig
        : action === "add"
          ? beforeBig + amountBig
          : beforeBig > amountBig
            ? beforeBig - amountBig
            : BigInt(0)

  const save = useMutation({
    mutationFn: () =>
      api.post<{ before: number; after: number }>(`/api/players/${target!.id}/balance`, {
        guild: guildId,
        action,
        amount: exactAmount !== null && exactAmount.length > 15 ? exactAmount : amount,
        note: note.trim() || null,
      }),
    onSuccess: (res) => {
      toast.success(`${target!.name}: ${credits(res.before)} → ${credits(res.after)}`, {
        description: `${signedBig(toBig(res.after) - toBig(res.before))} credits. Nothing was posted in Discord.`,
      })
      for (const key of ["players", "overview", "player", "edits", "activity", "guilds"]) {
        qc.invalidateQueries({ queryKey: [key] })
      }
      onClose()
    },
    onError: (err) => {
      toast.error(err instanceof ApiError ? err.message : "Couldn't save the change.")
    },
  })

  return (
    <Dialog open={!!target} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-3">
            {shown ? <PlayerAvatar name={shown.name} avatar={shown.avatar} size={28} /> : null}
            <span className="truncate">{shown?.name}</span>
          </DialogTitle>
          <DialogDescription>
            Current balance <span className="text-foreground font-medium tabular">{credits(before)}</span>. Changes
            aren't announced in the server.
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault()
            if (valid && !save.isPending) save.mutate()
          }}
        >
          <ToggleGroup
            type="single"
            variant="outline"
            value={action}
            onValueChange={(v) => v && setAction(v as Action)}
            className="w-full"
          >
            <ToggleGroupItem value="add" className={ON}>
              Add
            </ToggleGroupItem>
            <ToggleGroupItem value="remove" className={ON}>
              Remove
            </ToggleGroupItem>
            <ToggleGroupItem value="set" className={ON}>
              Set exact
            </ToggleGroupItem>
          </ToggleGroup>
          <div className="flex flex-col gap-2">
            <Label htmlFor="amount">{action === "set" ? "New balance" : "Amount"}</Label>
            <Input
              id="amount"
              inputMode="decimal"
              autoComplete="off"
              autoFocus
              placeholder={action === "set" ? credits(before) : "e.g. 5000, 2.5k or 1m"}
              value={raw}
              onChange={(e) => setRaw(e.target.value)}
              aria-invalid={raw && amount === null ? true : undefined}
              className="tabular"
            />
            {raw && amount === null ? (
              <p className="text-destructive text-xs">Enter a whole number, like 5000, 2.5k or 1m.</p>
            ) : null}
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="note">
              Note <span className="text-muted-foreground font-normal">(optional, only shown here)</span>
            </Label>
            <Textarea
              id="note"
              maxLength={200}
              rows={2}
              placeholder="Why the change, for later"
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          </div>
          <div className="bg-muted/50 flex items-center justify-between rounded-md border px-3 py-2 text-sm tabular">
            <span className="text-muted-foreground">{credits(before)}</span>
            <ArrowRightIcon className="text-muted-foreground size-4" />
            <span className="font-medium">{afterBig === null ? "—" : creditsBig(afterBig)}</span>
            <span
              className={cn(
                "min-w-20 text-right",
                after !== null && after > before && "text-success",
                after !== null && after < before && "text-error"
              )}
            >
              {afterBig === null ? "" : signedBig(afterBig - beforeBig)}
            </span>
          </div>
          {action === "remove" && amount !== null && amount > before ? (
            <p className="text-muted-foreground -mt-2 text-xs">Balances don't go below 0, so this sets it to 0.</p>
          ) : null}
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={!valid || save.isPending}>
              {save.isPending ? "Saving…" : VERB[action]}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

import * as React from "react"
import { useQueryClient } from "@tanstack/react-query"
import { DiscordLogoIcon, LockKeyIcon } from "@phosphor-icons/react"

import { api, ApiError } from "@/app/api"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

/** Why a Discord sign-in didn't work (?signin=... when Discord sends you back). */
const SIGNIN_ERRORS: Record<string, string> = {
  denied: "That Discord account isn't allowed in. Only the bot's owner and the people in ADMIN_PANEL_USERS are.",
  cancelled: "Discord sign-in was cancelled.",
  expired: "That sign-in took too long or was started elsewhere. Try again.",
  failed: "Discord sign-in didn't work. Check that this address is listed under OAuth2 → Redirects in the Developer Portal.",
  unavailable: "Discord sign-in isn't set up (DISCORD_CLIENT_SECRET is missing).",
}

function signinError(): string | null {
  const reason = new URLSearchParams(window.location.search).get("signin")
  if (!reason) return null
  // leave the address clean, so a reload doesn't show the message again
  window.history.replaceState(null, "", window.location.pathname + window.location.hash)
  return SIGNIN_ERRORS[reason] ?? SIGNIN_ERRORS.failed
}

export function Login({ discord }: { discord: boolean }) {
  const qc = useQueryClient()
  const [password, setPassword] = React.useState("")
  const [error, setError] = React.useState<string | null>(null)
  const [discordError] = React.useState(signinError)
  const [busy, setBusy] = React.useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.post("/api/login", { password })
      await qc.invalidateQueries({ queryKey: ["session"] })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Can't reach the bot.")
      setBusy(false)
    }
  }

  return (
    <main className="grid min-h-svh place-items-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <div className="bg-muted text-primary mb-2 grid size-9 place-items-center rounded-md">
            <LockKeyIcon className="size-5" />
          </div>
          <CardTitle>JaxCasino admin</CardTitle>
          <CardDescription>
            {discord
              ? "Sign in with the bot owner's Discord account, or with the admin panel password."
              : "Enter the admin panel password (ADMIN_PANEL_PASSWORD in the bot's .env)."}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {discord ? (
            <>
              <Button asChild className="bg-[#5865F2] text-white hover:bg-[#4752C4]">
                <a href="/api/auth/discord/start">
                  <DiscordLogoIcon data-icon="inline-start" weight="fill" /> Sign in with Discord
                </a>
              </Button>
              {discordError ? <p className="text-destructive text-xs">{discordError}</p> : null}
              <div className="text-muted-foreground flex items-center gap-3 text-[11px]">
                <span className="bg-border h-px flex-1" />
                or with the password
                <span className="bg-border h-px flex-1" />
              </div>
            </>
          ) : null}
          <form onSubmit={submit} className="flex flex-col gap-4">
            <div className="flex flex-col gap-2">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                autoFocus={!discord}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                aria-invalid={error ? true : undefined}
              />
              {error ? <p className="text-destructive text-xs">{error}</p> : null}
            </div>
            <Button type="submit" variant={discord ? "outline" : "default"} disabled={busy || !password}>
              {busy ? "Signing in…" : "Sign in"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </main>
  )
}

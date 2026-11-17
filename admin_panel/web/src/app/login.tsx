import * as React from "react"
import { useQueryClient } from "@tanstack/react-query"
import { LockKeyIcon } from "@phosphor-icons/react"

import { api, ApiError } from "@/app/api"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

export function Login() {
  const qc = useQueryClient()
  const [password, setPassword] = React.useState("")
  const [error, setError] = React.useState<string | null>(null)
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
          <CardDescription>Enter the admin panel password (ADMIN_PANEL_PASSWORD in the bot's .env).</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={submit} className="flex flex-col gap-4">
            <div className="flex flex-col gap-2">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                autoFocus
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                aria-invalid={error ? true : undefined}
              />
              {error ? <p className="text-destructive text-xs">{error}</p> : null}
            </div>
            <Button type="submit" disabled={busy || !password}>
              {busy ? "Signing in…" : "Sign in"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </main>
  )
}

import * as React from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  CheckCircleIcon,
  DeviceMobileIcon,
  DesktopIcon,
  HouseIcon,
  GlobeHemisphereWestIcon,
  XCircleIcon,
} from "@phosphor-icons/react"

import { api, ApiError, type SignIn, type SignIns } from "@/app/api"
import { ago, dateTime } from "@/app/format"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

const PASSWORD_MODE = {
  on: "The password works everywhere.",
  local: "The password works only at home. Away from home (through Tailscale), only Discord sign-in works.",
  off: "The password is off: only Discord sign-in works.",
} as const

/** "Chrome on Android" from a User-Agent: enough to spot a sign-in that isn't yours. */
export function browserName(ua: string): { label: string; phone: boolean } {
  if (!ua) return { label: "Unknown browser", phone: false }
  const os = /Android/.test(ua)
    ? "Android"
    : /iPhone|iPad/.test(ua)
      ? "iOS"
      : /Windows/.test(ua)
        ? "Windows"
        : /Mac OS X/.test(ua)
          ? "macOS"
          : /Linux/.test(ua)
            ? "Linux"
            : ""
  const app = /Edg\//.test(ua)
    ? "Edge"
    : /SamsungBrowser/.test(ua)
      ? "Samsung Internet"
      : /Firefox\//.test(ua)
        ? "Firefox"
        : /OPR\//.test(ua)
          ? "Opera"
          : /Chrome\//.test(ua)
            ? "Chrome"
            : /Safari\//.test(ua)
              ? "Safari"
              : /curl|python|aiohttp|Go-http/i.test(ua)
                ? "a script"
                : "Unknown browser"
  return { label: os ? `${app} on ${os}` : app, phone: /Mobile|Android|iPhone/.test(ua) }
}

/** Who signed in to this panel, how and from where, and a button that ends every session. */
export function SecurityPage() {
  const qc = useQueryClient()
  const data = useQuery({ queryKey: ["signins"], queryFn: () => api.get<SignIns>("/api/signins"), refetchInterval: 30_000 })
  const [confirm, setConfirm] = React.useState(false)
  const signOutAll = useMutation({
    mutationFn: () => api.post<{ ok: boolean }>("/api/signout-all", {}),
    onSuccess: () => {
      setConfirm(false)
      qc.invalidateQueries({ queryKey: ["signins"] })
    },
  })

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 p-4 md:p-6">
        <div>
          <h1 className="text-lg font-semibold">Security</h1>
          <p className="text-muted-foreground text-sm">Sign-ins to this panel, from every device.</p>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Sign out everywhere</CardTitle>
            <CardDescription>
              Ends every session on every device at once, for a lost phone say. This device stays signed in. Sessions
              otherwise last {data.data?.session_days ?? 30} days.{" "}
              {data.data ? PASSWORD_MODE[data.data.password_login] : null}
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap items-center gap-2">
            {confirm ? (
              <>
                <Button variant="destructive" disabled={signOutAll.isPending} onClick={() => signOutAll.mutate()}>
                  {signOutAll.isPending ? "Signing out…" : "Yes, sign out every other device"}
                </Button>
                <Button variant="outline" onClick={() => setConfirm(false)}>
                  Cancel
                </Button>
              </>
            ) : (
              <Button variant="outline" onClick={() => setConfirm(true)}>
                Sign out everywhere
              </Button>
            )}
            {signOutAll.isSuccess && !confirm ? (
              <span className="text-success text-sm">Every other session has ended.</span>
            ) : null}
            {signOutAll.isError ? (
              <span className="text-destructive text-sm">
                {signOutAll.error instanceof ApiError ? signOutAll.error.message : "That didn't work."}
              </span>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Latest sign-ins</CardTitle>
            <CardDescription>
              The last 50, refused ones too. “Away” means it came through Tailscale (or another proxy), not straight
              from your home network.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            {data.isLoading ? (
              <Skeleton className="h-40" />
            ) : data.isError ? (
              <p className="text-destructive text-sm">Couldn't load the sign-ins: {String(data.error)}</p>
            ) : !data.data?.signins.length ? (
              <p className="text-muted-foreground text-sm">No sign-ins recorded yet.</p>
            ) : (
              data.data.signins.map((s, i) => <SignInRow key={`${s.at}-${i}`} s={s} />)
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

function SignInRow({ s }: { s: SignIn }) {
  const b = browserName(s.browser)
  const Device = b.phone ? DeviceMobileIcon : DesktopIcon
  const Where = s.away ? GlobeHemisphereWestIcon : HouseIcon
  return (
    <div
      className={cn(
        "flex items-start gap-3 rounded-md border px-3 py-2.5",
        !s.ok && "border-destructive/40 bg-destructive/5"
      )}
    >
      {s.ok ? (
        <CheckCircleIcon className="text-success mt-0.5 size-5 shrink-0" weight="fill" />
      ) : (
        <XCircleIcon className="text-destructive mt-0.5 size-5 shrink-0" weight="fill" />
      )}
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
          <span className="font-medium capitalize">{s.method}</span>
          {s.who ? <span className="text-muted-foreground truncate">{s.who}</span> : null}
          {!s.ok ? <Badge variant="destructive">Refused{s.note ? `: ${s.note}` : ""}</Badge> : null}
        </div>
        <div className="text-muted-foreground flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
          <span title={dateTime(s.at)}>{ago(s.at)}</span>
          <span className="flex items-center gap-1">
            <Where className="size-3.5" /> {s.away ? "Away" : "At home"}
          </span>
          <span className="flex items-center gap-1" title={s.browser}>
            <Device className="size-3.5" /> {b.label}
          </span>
          <span className="tabular">{s.ip}</span>
          {s.tailnet_user ? <span>{s.tailnet_user}</span> : null}
        </div>
      </div>
    </div>
  )
}

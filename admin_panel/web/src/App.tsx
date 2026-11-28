import * as React from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"

import { api, SIGNED_OUT, type Session } from "@/app/api"
import { Login } from "@/app/login"
import { Shell } from "@/app/shell"

export default function App() {
  const qc = useQueryClient()
  const session = useQuery({
    queryKey: ["session"],
    queryFn: () => api.get<Session>("/api/session"),
    staleTime: Infinity,
  })

  React.useEffect(() => {
    const signedOut = () => {
      // keep the Discord flag, so the sign-in page still offers the Discord button
      qc.setQueryData<Session>(["session"], (old) => ({ ...old, authenticated: false }))
    }
    window.addEventListener(SIGNED_OUT, signedOut)
    return () => window.removeEventListener(SIGNED_OUT, signedOut)
  }, [qc])

  if (session.isLoading) {
    return <div className="text-muted-foreground grid h-svh place-items-center text-sm">Connecting to the bot…</div>
  }
  if (session.isError) {
    return (
      <div className="grid h-svh place-items-center p-6 text-center text-sm">
        <div className="flex flex-col gap-2">
          <p className="text-foreground">Can't reach the bot.</p>
          <p className="text-muted-foreground">Is it running? This page retries when you reload it.</p>
        </div>
      </div>
    )
  }
  if (!session.data?.authenticated)
    return <Login discord={!!session.data?.discord} password={session.data?.password !== false} />
  return <Shell session={session.data} />
}

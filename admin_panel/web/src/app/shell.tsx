import * as React from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import {
  ChartPieSliceIcon,
  ClockCounterClockwiseIcon,
  GameControllerIcon,
  PulseIcon,
  SignOutIcon,
  UsersThreeIcon,
} from "@phosphor-icons/react"

import { api, type Guild, type Session } from "@/app/api"
import { GuildPicker } from "@/app/guild-picker"
import { ActivityPage } from "@/app/pages/activity"
import { EditsPage } from "@/app/pages/edits"
import { GamesPage } from "@/app/pages/games"
import { OverviewPage } from "@/app/pages/overview"
import { PlayersPage } from "@/app/pages/players"
import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

const PAGES = {
  overview: { label: "Overview", icon: ChartPieSliceIcon },
  games: { label: "Games", icon: GameControllerIcon },
  players: { label: "Players", icon: UsersThreeIcon },
  activity: { label: "Activity", icon: PulseIcon },
  edits: { label: "Balance edits", icon: ClockCounterClockwiseIcon },
} as const
type PageKey =
  | keyof typeof PAGES

function readHash(): PageKey {
  const key = window.location.hash.replace(/^#\/?/, "")
  if (key in PAGES) return key as PageKey
  return "overview"
}

const GUILD_KEY = "jaxcasino-admin-guild"
function storedGuild(): string | null {
  try {
    return localStorage.getItem(GUILD_KEY)
  } catch {
    return null
  }
}

export function Shell({ session }: { session: Session }) {
  const qc = useQueryClient()
  const [page, setPage] = React.useState<PageKey>(readHash)
  const guilds = useQuery({ queryKey: ["guilds"], queryFn: () => api.get<Guild[]>("/api/guilds") })
  const [guildId, setGuildId] = React.useState<string | null>(storedGuild)

  React.useEffect(() => {
    const onHash = () => setPage(readHash())
    window.addEventListener("hashchange", onHash)
    return () => window.removeEventListener("hashchange", onHash)
  }, [])

  const guild = guilds.data?.find((g) => g.id === guildId) ?? guilds.data?.[0] ?? null
  const pickGuild = (id: string) => {
    setGuildId(id)
    try {
      localStorage.setItem(GUILD_KEY, id)
    } catch {
      // storage blocked (e.g. incognito): the choice just isn't remembered
    }
  }

  async function signOut() {
    await api.post("/api/logout", {}).catch(() => undefined)
    qc.clear()
    qc.setQueryData<Session>(["session"], { authenticated: false })
  }

  let nav: { key: PageKey; label: string; icon: typeof ChartPieSliceIcon }[] = (Object.keys(PAGES) as (keyof typeof PAGES)[]).map(
    (k) => ({ key: k as PageKey, ...PAGES[k] })
  )

  const navLink = ({ key, label, icon: Icon }: (typeof nav)[number]) => (
    <Tooltip key={key}>
      <TooltipTrigger asChild>
        <a
          href={`#/${key}`}
          aria-label={label}
          aria-current={page === key ? "page" : undefined}
          className={cn(
            "text-muted-foreground hover:bg-sidebar-accent hover:text-foreground grid size-10 place-items-center rounded-md transition-colors",
            page === key && "bg-sidebar-accent text-foreground"
          )}
        >
          <Icon className="size-5" weight={page === key ? "fill" : "regular"} />
        </a>
      </TooltipTrigger>
      <TooltipContent side="right">{label}</TooltipContent>
    </Tooltip>
  )

  return (
    <div className="flex h-svh w-full overflow-hidden">
      <nav className="bg-sidebar border-sidebar-border flex w-14 shrink-0 flex-col items-center gap-1 border-r py-3">
        <GuildPicker guilds={guilds.data ?? []} current={guild} onPick={pickGuild} />
        <div className="bg-sidebar-border my-2 h-px w-8" />
        {nav.map(navLink)}
        <div className="flex-1" />
        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="ghost" size="icon" onClick={signOut} aria-label="Sign out">
              <SignOutIcon className="size-5" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="right">Sign out</TooltipContent>
        </Tooltip>
      </nav>
      <div className="relative min-w-0 flex-1 overflow-hidden">
        {guilds.isLoading ? (
          <Centered>Loading servers…</Centered>
        ) : !guild ? (
          <Centered>No server has players yet.</Centered>
        ) : page === "overview" ? (
          <OverviewPage guild={guild} />
        ) : page === "games" ? (
          <GamesPage key={guild.id} guild={guild} />
        ) : page === "players" ? (
          <PlayersPage key={guild.id} guild={guild} />
        ) : page === "activity" ? (
          <ActivityPage key={guild.id} guild={guild} />
        ) : page === "edits" ? (
          <EditsPage guild={guild} />
        ) : (
          <Centered>That page isn't available.</Centered>
        )}
      </div>
    </div>
  )
}

function Centered({ children }: { children: React.ReactNode }) {
  return <div className="text-muted-foreground grid h-full place-items-center text-sm">{children}</div>
}

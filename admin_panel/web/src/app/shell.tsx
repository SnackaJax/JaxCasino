import * as React from "react"
import { useQuery } from "@tanstack/react-query"
import {
  ChartPieSliceIcon,
  CheckIcon,
  ClockCounterClockwiseIcon,
  DotsThreeOutlineIcon,
  GameControllerIcon,
  GearSixIcon,
  PulseIcon,
  ShieldCheckIcon,
  SignOutIcon,
  UsersThreeIcon,
} from "@phosphor-icons/react"

import { api, type Guild, type Session } from "@/app/api"
import { GuildIcon, GuildPicker } from "@/app/guild-picker"
import { ActivityPage } from "@/app/pages/activity"
import { EditsPage } from "@/app/pages/edits"
import { GamesPage } from "@/app/pages/games"
import { OverviewPage } from "@/app/pages/overview"
import { PlayersPage } from "@/app/pages/players"
import { SecurityPage } from "@/app/pages/security"
import { SettingsPage } from "@/app/pages/settings"
import { Button } from "@/components/ui/button"
import { Drawer, DrawerContent, DrawerDescription, DrawerHeader, DrawerTitle } from "@/components/ui/drawer"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

const PAGES = {
  overview: { label: "Overview", icon: ChartPieSliceIcon },
  games: { label: "Games", icon: GameControllerIcon },
  players: { label: "Players", icon: UsersThreeIcon },
  activity: { label: "Activity", icon: PulseIcon },
  edits: { label: "Balance edits", icon: ClockCounterClockwiseIcon },
  settings: { label: "Game settings", icon: GearSixIcon },
  security: { label: "Security", icon: ShieldCheckIcon },
} as const
type PageKey =
  | keyof typeof PAGES

function readHash(): PageKey {
  const key = window.location.hash.replace(/^#\/?/, "")
  if (key in PAGES) return key as PageKey
  return "overview"
}

/** On a phone these sit in the bottom bar; everything else is under "More". */
const PHONE_BAR: PageKey[] = ["overview", "players", "activity", "games"]

const GUILD_KEY = "jaxcasino-admin-guild"
function storedGuild(): string | null {
  try {
    return localStorage.getItem(GUILD_KEY)
  } catch {
    return null
  }
}

export function Shell({ session }: { session: Session }) {
  const [page, setPage] = React.useState<PageKey>(readHash)
  const [more, setMore] = React.useState(false)
  const guilds = useQuery({ queryKey: ["guilds"], queryFn: () => api.get<Guild[]>("/api/guilds") })
  const [guildId, setGuildId] = React.useState<string | null>(storedGuild)

  React.useEffect(() => {
    const onHash = () => {
      setPage(readHash())
      setMore(false)
    }
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
    // a fresh load opens the sign-in page, with nothing of the old session left in memory
    window.location.replace("/")
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

  const barNav = PHONE_BAR.map((k) => nav.find((n) => n.key === k)).filter((n) => n !== undefined)
  const moreNav = [
    ...nav.filter((n) => !PHONE_BAR.includes(n.key)),
  ]

  return (
    <div className="flex h-dvh w-full flex-col overflow-hidden md:flex-row">
      {/* computer and tablet: the icon bar on the left */}
      <nav className="bg-sidebar border-sidebar-border hidden w-14 shrink-0 flex-col items-center gap-1 border-r py-3 md:flex">
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
      <div className="relative min-h-0 min-w-0 flex-1 overflow-hidden">
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
        ) : page === "settings" ? (
          <SettingsPage key={guild.id} guild={guild} />
        ) : page === "security" ? (
          <SecurityPage />
        ) : (
          <Centered>That page isn't available.</Centered>
        )}
      </div>

      {/* phone: a bar at the bottom, in reach of a thumb, and the rest in a drawer */}
      <nav className="bg-sidebar border-sidebar-border grid shrink-0 grid-cols-5 border-t pb-[env(safe-area-inset-bottom)] md:hidden">
        {barNav.map(({ key, label, icon: Icon }) => (
          <a
            key={key}
            href={`#/${key}`}
            aria-current={page === key ? "page" : undefined}
            className={cn(
              "text-muted-foreground flex h-14 flex-col items-center justify-center gap-0.5 text-[10px]",
              page === key && "text-foreground"
            )}
          >
            <Icon className="size-6" weight={page === key ? "fill" : "regular"} />
            {label}
          </a>
        ))}
        <button
          type="button"
          onClick={() => setMore(true)}
          className={cn(
            "text-muted-foreground flex h-14 flex-col items-center justify-center gap-0.5 text-[10px]",
            moreNav.some((n) => n.key === page) && "text-foreground"
          )}
        >
          <DotsThreeOutlineIcon className="size-6" weight={moreNav.some((n) => n.key === page) ? "fill" : "regular"} />
          More
        </button>
      </nav>
      <Drawer open={more} onOpenChange={setMore}>
        <DrawerContent className="pb-[env(safe-area-inset-bottom)] md:hidden">
          <DrawerHeader className="sr-only">
            <DrawerTitle>Menu</DrawerTitle>
            <DrawerDescription>The other pages, the server and signing out</DrawerDescription>
          </DrawerHeader>
          <div className="flex flex-col gap-1 overflow-y-auto p-3 text-sm">
            {moreNav.map(({ key, label, icon: Icon }) => (
              <a
                key={key}
                href={`#/${key}`}
                onClick={() => setMore(false)}
                aria-current={page === key ? "page" : undefined}
                className={cn(
                  "text-muted-foreground flex h-12 items-center gap-3 rounded-md px-3",
                  page === key && "bg-sidebar-accent text-foreground"
                )}
              >
                <Icon className="size-5" weight={page === key ? "fill" : "regular"} />
                {label}
              </a>
            ))}
            {(guilds.data?.length ?? 0) > 1 ? (
              <>
                <p className="text-muted-foreground mt-3 px-3 text-[11px] uppercase tracking-wide">Server</p>
                {guilds.data!.map((g) => (
                  <button
                    key={g.id}
                    type="button"
                    onClick={() => {
                      pickGuild(g.id)
                      setMore(false)
                    }}
                    className="flex h-12 items-center gap-3 rounded-md px-3 text-left"
                  >
                    <GuildIcon guild={g} size={24} />
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="truncate">{g.name}</span>
                      <span className="text-muted-foreground text-[11px]">{g.players} players</span>
                    </span>
                    {g.id === guild?.id ? <CheckIcon className="size-4" /> : null}
                  </button>
                ))}
              </>
            ) : null}
            <button
              type="button"
              onClick={signOut}
              className="text-muted-foreground mt-1 flex h-12 items-center gap-3 rounded-md border-t px-3 text-left"
            >
              <SignOutIcon className="size-5" /> Sign out
            </button>
          </div>
        </DrawerContent>
      </Drawer>
    </div>
  )
}

function Centered({ children }: { children: React.ReactNode }) {
  return <div className="text-muted-foreground grid h-full place-items-center text-sm">{children}</div>
}

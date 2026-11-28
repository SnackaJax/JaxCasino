import { CheckIcon } from "@phosphor-icons/react"

import type { Guild } from "@/app/api"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"

export function GuildIcon({ guild, size = 36 }: { guild: Guild | null; size?: number }) {
  if (guild?.icon) {
    return <img src={guild.icon} alt="" width={size} height={size} className="rounded-md" />
  }
  const initials = (guild?.name ?? "?")
    .split(/\s+/)
    .map((w) => w[0])
    .join("")
    .slice(0, 2)
    .toUpperCase()
  return (
    <span
      style={{ width: size, height: size }}
      className="bg-primary/10 text-primary grid place-items-center rounded-md text-xs font-semibold"
    >
      {initials}
    </span>
  )
}

export function GuildPicker({
  guilds,
  current,
  onPick,
}: {
  guilds: Guild[]
  current: Guild | null
  onPick: (id: string) => void
}) {
  return (
    <DropdownMenu>
      <Tooltip>
        <TooltipTrigger asChild>
          <DropdownMenuTrigger
            className="focus-visible:ring-ring rounded-md outline-none focus-visible:ring-2"
            aria-label="Pick a server"
          >
            <GuildIcon guild={current} />
          </DropdownMenuTrigger>
        </TooltipTrigger>
        <TooltipContent side="right">{current?.name ?? "Pick a server"}</TooltipContent>
      </Tooltip>
      <DropdownMenuContent side="right" align="start" className="min-w-64">
        <DropdownMenuLabel>Servers</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {guilds.map((g) => (
          <DropdownMenuItem key={g.id} onSelect={() => onPick(g.id)} className="gap-3">
            <GuildIcon guild={g} size={24} />
            <span className="flex min-w-0 flex-1 flex-col">
              <span className="truncate">{g.name}</span>
              <span className="text-muted-foreground text-xs">
                {g.players} players{g.bot_in_server ? "" : " · bot left"}
              </span>
            </span>
            {g.id === current?.id ? <CheckIcon className="size-4" /> : null}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

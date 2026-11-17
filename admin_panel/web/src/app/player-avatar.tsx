import { cn } from "@/lib/utils"

/** A player's Discord avatar, or their initials when there's none (or they left). */
export function PlayerAvatar({
  name,
  avatar,
  size = 24,
  className,
}: {
  name: string | null
  avatar: string | null
  size?: number
  className?: string
}) {
  if (avatar) {
    return (
      <img
        src={avatar}
        alt=""
        width={size}
        height={size}
        loading="lazy"
        className={cn("shrink-0 rounded-full", className)}
      />
    )
  }
  const letter = (name ?? "?").trim().charAt(0).toUpperCase() || "?"
  return (
    <span
      style={{ width: size, height: size, fontSize: Math.max(10, size * 0.42) }}
      className={cn("bg-muted text-muted-foreground grid shrink-0 place-items-center rounded-full", className)}
    >
      {letter}
    </span>
  )
}

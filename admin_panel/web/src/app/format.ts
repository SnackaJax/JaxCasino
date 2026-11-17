// Numbers, names and times the way the panel shows them.

const whole = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 })
const compactFmt = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 })

export const credits = (n: number) => whole.format(n)
export const compact = (n: number) => compactFmt.format(n)
export const signed = (n: number) => (n > 0 ? `+${credits(n)}` : n < 0 ? `−${credits(-n)}` : "0")
export const pct = (part: number, whole: number, digits = 1) =>
  whole ? `${((part / whole) * 100).toFixed(digits)}%` : "—"

export function ago(ts: number | null | undefined): string {
  if (!ts) return "never"
  const s = Date.now() / 1000 - ts
  if (s < 60) return "just now"
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  if (s < 86400 * 30) return `${Math.floor(s / 86400)}d ago`
  return new Date(ts * 1000).toLocaleDateString()
}

export const dateTime = (ts: number) =>
  new Date(ts * 1000).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  })

export const GAME_NAMES: Record<string, string> = {
  coinflip: "Coinflip",
  dice: "Dice",
  slots: "Slots",
  roulette: "Roulette",
  wheel: "Wheel",
  blackjack: "Blackjack",
  higherlower: "Higher/Lower",
  mines: "Mines",
  crash: "Crash",
  tower: "Tower",
  videopoker: "Video poker",
  duel: "Duels",
  horserace: "Horse racing",
  lottery: "Lottery",
  poker: "Poker",
  cockfight: "Cockfight",
  bangerbash: "Banger Bash",
  reefrush: "Reef Rush",
  raccoonheist: "Raccoon Heist",
  spinfrenzy: "Spin Frenzy",
}
export const gameName = (key: string | null | undefined) => (key ? GAME_NAMES[key] ?? key.charAt(0).toUpperCase() + key.slice(1) : "Unknown")

export const MONEY_NAMES: Record<string, string> = {
  claim: "Daily claim",
  restart: "Restart",
  admin: "Admin change",
  pay: "Payment",
  shop: "Shop",
  giveall: "Give all",
  removeall: "Remove all",
}
export const moneyName = (key: string | null | undefined) => (key ? MONEY_NAMES[key] ?? key.charAt(0).toUpperCase() + key.slice(1) : "Other")

export const displayName = (p: { name: string | null }, id?: string | null) =>
  p.name ?? (id ? `Unknown (${id})` : "Unknown")

/** Reads "1,500", "2.5k", "3m" or "1b" as a whole number of credits; null when it isn't one. */
export function parseAmount(raw: string): number | null {
  const s = raw.trim().toLowerCase().replace(/[,_\s]/g, "")
  const m = /^(\d+(?:\.\d+)?)([kmb]?)$/.exec(s)
  if (!m) return null
  const n = parseFloat(m[1]) * ({ "": 1, k: 1e3, m: 1e6, b: 1e9 } as Record<string, number>)[m[2]]
  if (!Number.isFinite(n) || n > 1e15) return null
  return Math.round(n)
}

/** A stable chart colour for each game, so a game looks the same everywhere. */
export function gameColor(key: string): string {
  const keys = Object.keys(GAME_NAMES)
  const i = keys.indexOf(key)
  const n = i >= 0 ? i : [...key].reduce((a, c) => a + c.charCodeAt(0), 0)
  return `var(--chart-${(n % 9) + 1})`
}

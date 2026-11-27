// Numbers, names and times the way the panel shows them.
import { exactDigits } from "@/app/api"

const whole = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 })
const compactFmt = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 })
const oneDecimal = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 })

/** 1,234,567: in full, to the credit even past 2^53 when the amount came from the server as it is */
export const credits = (n: number) => {
  let digits: string | undefined
  if (Math.abs(n) > Number.MAX_SAFE_INTEGER) {
    const neg = exactDigits(-n) // signed() shows -n
    digits = exactDigits(n) ?? (neg === undefined ? undefined : neg.startsWith("-") ? neg.slice(1) : `-${neg}`)
  }
  // a numeric string is formatted exactly (Intl.NumberFormat v3)
  return digits ? whole.format(digits as unknown as number) : whole.format(n)
}
// short forms past a trillion: quadrillion, quintillion, sextillion, septillion, octillion, nonillion, decillion
const BIG_UNITS: [number, string][] = [[1e33, "Dc"], [1e30, "No"], [1e27, "Oc"], [1e24, "Sp"], [1e21, "Sx"], [1e18, "Qi"], [1e15, "Qa"]]
/** 1.2K, 3.4M, 5.6B, 7.8T, then 1.2Qa, 3.4Qi ... 5Dc and 1.2e40 */
export const compact = (n: number): string => {
  const a = Math.abs(n)
  if (a < 1e15 || !Number.isFinite(n)) return compactFmt.format(n)
  const sign = n < 0 ? "-" : ""
  if (a >= 1e36) {
    const [m, e] = a.toExponential(1).split("e")
    return `${sign}${+m}e${+e}`
  }
  for (const [d, u] of BIG_UNITS) {
    if (a >= d) {
      const x = a / d
      return x >= 999.95 && u !== "Dc" ? compact(Math.sign(n) * d * 1000) : `${sign}${oneDecimal.format(x)}${u}`
    }
  }
  return compactFmt.format(n)
}
/** An amount as a BigInt, exact when it came from the server as it is (see api.ts) */
export const toBig = (n: number): bigint => {
  const d = Math.abs(n) > Number.MAX_SAFE_INTEGER ? exactDigits(n) : undefined
  return BigInt(d ?? Math.round(n))
}
/** credits() and signed() for a BigInt (exact at any size) */
export const creditsBig = (b: bigint) => whole.format(b)
export const signedBig = (b: bigint) => (b > 0 ? `+${whole.format(b)}` : b < 0 ? `−${whole.format(-b)}` : "0")
export const signed = (n: number) => (n > 0 ? `+${credits(n)}` : n < 0 ? `−${credits(-n)}` : "0")
/** +1.2M / −340K: a signed amount, short */
export const signedCompact = (n: number) => (n > 0 ? `+${compact(n)}` : n < 0 ? `−${compact(-n)}` : "0")
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

const SUFFIX_EXP: Record<string, number> = {
  "": 0, k: 3, m: 6, b: 9, t: 12, qa: 15, qi: 18, sx: 21, sp: 24, oc: 27, no: 30, dc: 33,
}

/** "1,500", "2.5k", "3m", "1.2b", "3.4t", "5qa", "2.1qi" ... as the exact digits of a whole number of credits
 * (any size, no rounding); null when it isn't one. */
export function parseAmountExact(raw: string): string | null {
  const s = raw.trim().toLowerCase().replace(/[,_\s]/g, "")
  const m = /^(\d+)(?:\.(\d+))?([a-z]{0,2})$/.exec(s)
  if (!m || !(m[3] in SUFFIX_EXP) || s.length > 400) return null
  const exp = SUFFIX_EXP[m[3]]
  const frac = m[2] ?? ""
  if (frac.length > exp && /[^0]/.test(frac.slice(exp))) return null // not a whole number of credits
  const digits = (m[1] + frac.slice(0, exp).padEnd(exp, "0")).replace(/^0+(?=\d)/, "")
  return digits
}

/** parseAmountExact as a number (inexact past 2^53: for showing and comparing; send the exact digits). */
export function parseAmount(raw: string): number | null {
  const digits = parseAmountExact(raw)
  return digits === null ? null : Number(digits)
}

/** A stable chart colour for each game, so a game looks the same everywhere. */
export function gameColor(key: string): string {
  const keys = Object.keys(GAME_NAMES)
  const i = keys.indexOf(key)
  const n = i >= 0 ? i : [...key].reduce((a, c) => a + c.charCodeAt(0), 0)
  return `var(--chart-${(n % 9) + 1})`
}

/** Every column can be dragged wider or narrower (the little status/result dots stay as they are). */
export function resizable<T>(columns: T[], fixed: string[] = []): T[] {
  return columns.map((c) => {
    const col = c as { id?: string; accessorKey?: string }
    const id = col.id ?? col.accessorKey ?? ""
    // a column that wasn't resizable has its min and max pinned to its size: open them up
    return fixed.includes(id) ? c : ({ ...c, enableResizing: true, minSize: 56, maxSize: 1200 } as T)
  })
}

/** 45s, 34m, 5h 12m, 3d 4h */
export function duration(secs: number) {
  const s = Math.max(0, Math.round(secs))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ${String(m % 60).padStart(2, "0")}m`
  return `${Math.floor(h / 24)}d ${h % 24}h`
}

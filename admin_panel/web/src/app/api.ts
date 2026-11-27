// Talking to the bot's admin panel server (admin_panel/server.py).

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

/** Fired when the server says we're not signed in (the session expired or the password changed). */
export const SIGNED_OUT = "jaxcasino:signed-out"

// Credits past 2^53 (where JavaScript numbers stop being exact) come as tagged strings, "<digits>n"
// (admin_panel/server.py). They're turned into numbers here, so sorting, charts and sums work as before, and their
// exact digits are kept so credits() in format.ts can still show them to the credit.
const EXACT = new Map<number, string>()
const BIG = /^-?\d+n$/

/** The exact digits of a big amount that came from the server as this number, if any. */
export const exactDigits = (n: number): string | undefined => EXACT.get(n)

function revive(_key: string, value: unknown): unknown {
  if (typeof value === "string" && BIG.test(value)) {
    const digits = value.slice(0, -1)
    const n = Number(digits)
    if (EXACT.size > 100_000) EXACT.clear()
    EXACT.set(n, digits)
    return n
  }
  return value
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  let data: unknown = null
  try {
    data = JSON.parse(await res.text(), revive)
  } catch {
    // not JSON (the bot is restarting, for instance)
  }
  if (!res.ok) {
    if (res.status === 401 && path !== "/api/login") window.dispatchEvent(new Event(SIGNED_OUT))
    const msg = (data as { error?: string } | null)?.error ?? `The bot answered ${res.status} ${res.statusText}`
    throw new ApiError(res.status, msg)
  }
  return data as T
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body: unknown) => request<T>("POST", path, body),
}

// ---------------------------------------------------------------- shapes

export type Session = {
  authenticated: boolean
  /** whether "Sign in with Discord" works (the bot knows its client secret) */
  discord?: boolean
}

/** The server's game switches (GET/POST /api/settings) */
export type GameSettings = {
  /** blackjack in the Activity: side bet wins are paid out, then doubled */
  bj_double_side: boolean
}

/** What an economy reset would do (GET /api/economy/reset) */
export type ResetPreview = {
  players: number
  /** players whose balance isn't the starting balance already */
  changed: number
  starting_balance: number
  /** credits held by all players now, and after the reset */
  before: number
  after: number
  /** the phrase POST /api/economy/reset needs as `confirm` */
  confirm: string
  /** credits in escrow (open bets, poker chips): the reset is refused while there are any */
  in_play: (Person & { user_id: string; amount: number })[]
}

export type ResetResult = Omit<ResetPreview, "confirm" | "in_play">

export type Guild = {
  id: string
  name: string
  icon: string | null
  players: number
  bot_in_server: boolean
}

export type Person = {
  name: string | null
  username?: string
  avatar: string | null
  in_server: boolean
}

export type Player = Person & {
  id: string
  balance: number
  in_play: number
  games: number
  wins: number
  losses: number
  wagered: number
  won: number
  biggest_win: number
  net: number
  last_active: number | null
  /** time played in seconds, worked out from when their games happened (all time / the last 7 days) */
  played: number
  played_week: number
  /** a running /crux (a break from the casino the player took): when it started and ends; null without one */
  crux_until: number | null
  crux_started: number | null
  mix: Record<string, number>
}

export type Period = "24h" | "7d" | "30d" | "all"

export type Overview = {
  period: Period
  since: number | null
  kpis: {
    held: number
    in_play: number
    accounts: number
    active_day: number
    active_week: number
    games: number
    wagered: number
    paid: number
    players: number
    lottery_pot: number
    edits: number
    edits_net: number
  }
  games: { game: string; plays: number; wagered: number; paid: number; players: number }[]
  money: { kind: string; n: number; added: number; removed: number }[]
  daily: { day: string; won: number; lost: number; even: number; wagered: number; paid: number }[]
  circulation: [number, number][]
  edits: Edit[]
}

export type Edit = Person & {
  id: number
  user_id: string
  action: "set" | "add" | "remove"
  amount: number
  before: number
  after: number
  note: string | null
  ts: number
}

export type PlayerDetail = {
  player: Person & { id: string }
  stats: {
    games: number
    wins: number
    wagered: number
    paid: number
    per_game: { game: string; plays: number; wagered: number; net: number }[]
    best: { game: string; wagered: number; payout: number; profit: number; ts: number } | null
    worst: { game: string; wagered: number; payout: number; loss: number; ts: number } | null
    /** keyed by kind; "received" (payments from other players) has no kind field of its own */
    money: Record<string, { kind?: string; n: number; amount: number }>
  }
  edits: Omit<Edit, keyof Person | "user_id">[]
}

/** A player's balance between two unix times, as [ts, balance] points. */
export type BalanceWindow = { from: number; to: number; points: [number, number][] }

export type ActivityRow = {
  id: string
  ts: number
  user_id: string | null
  type: "game" | "money"
  kind: string
  wagered: number | null
  payout: number | null
  delta: number
  other_id: string | null
}

export type ActivityPage = { rows: ActivityRow[]; people: Record<string, Person> }

// ---------------------------------------------------------------- deeper stats (admin_panel/insights.py)

export type People = Record<string, Person>

/** One round of a game, for the record lists. */
export type Round = { user_id: string; game: string; wagered: number; payout: number; ts: number }
/** One player's games over a period, for the record lists. */
export type PlayerTotal = { user_id: string; games: number; wagered: number; paid: number; net: number }

export type HeatCell = { dow: number; hour: number; games: number; wagered: number; players?: number }

export type Insights = {
  since: number
  /** a chart slot is an hour (24-hour period) or a day */
  bucket: "hour" | "day"
  timeline: {
    t: number
    games: number
    wagered: number
    paid: number
    /** the house's result per game: wagered minus paid back */
    by_game: Record<string, number>
    players: number
    new_players: number
    restarts: number
    restart_credits: number
  }[]
  broke: { user_id: string; n: number; credits: number; last: number }[]
  heatmap: { since: number; cells: HeatCell[] }
  flow: { start: number; end: number; steps: { kind: string; amount: number; n: number | null }[] }
  bets: { lo: number; hi: number | null; rounds: number; wagered: number }[]
  wealth: { t: number; top10?: number; top1?: number; gini?: number; accounts?: number; top_n?: number }[]
  records: {
    wins: Round[]
    multipliers: Round[]
    losses: Round[]
    winners: PlayerTotal[]
    losers: PlayerTotal[]
    luckiest: PlayerTotal[]
    unluckiest: PlayerTotal[]
    min_games: number
  }
  people: People
}

export type GameStats = {
  game: string
  plays: number
  players: number
  wagered: number
  paid: number
  /** rounds that paid anything back / that paid more than the bet */
  hits: number
  wins: number
  avg_multiplier: number | null
  max_multiplier: number | null
  /** what the game pays back by its rules and settings; approx when it depends on how it's played */
  expected: { value: number; approx: boolean } | null
  timeline: { t: number; plays: number; wagered: number; paid: number }[]
  top_wins: Round[]
  top_players: { user_id: string; plays: number; wagered: number; net: number }[]
}

export type GamesPage = { since: number; bucket: "hour" | "day"; games: GameStats[]; people: People }

export type PlayerInsights = {
  heatmap: HeatCell[]
  daily: { day: string; games: number; wagered: number; net: number }[]
  mix: { game: string; plays: number; wagered: number; paid: number; all_wagered: number; all_paid: number }[]
  everyone: { wagered: number; paid: number }
  broke: { n: number; credits: number; last: number | null; week: number }
  payments: { user_id: string; sent: number; sent_n: number; received: number; received_n: number; last: number }[]
  streaks: { game: string; best: number }[]
  people: People
}

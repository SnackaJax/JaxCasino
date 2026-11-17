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

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  let data: unknown = null
  try {
    data = await res.json()
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
}

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
    money: Record<string, { kind: string; n: number; amount: number }>
  }
  history: [number, number][]
  edits: Omit<Edit, keyof Person | "user_id">[]
}

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

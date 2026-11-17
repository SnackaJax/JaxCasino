"""Read-only queries behind the admin panel's pages. Writes go through Database.admin_edit (database.py)."""
from __future__ import annotations

import datetime
import time

from database import Database

PERIODS = {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400, "all": 0}
# days shown in the per-day chart for each period
CHART_DAYS = {"24h": 14, "7d": 14, "30d": 30, "all": 90}


async def _all(db: Database, sql: str, params: tuple = ()) -> list[dict]:
    cur = await db.conn.execute(sql, params)
    return [dict(r) for r in await cur.fetchall()]


async def _one(db: Database, sql: str, params: tuple = ()) -> dict:
    cur = await db.conn.execute(sql, params)
    row = await cur.fetchone()
    return dict(row) if row else {}


async def guild_ids(db: Database) -> dict[int, int]:
    """Every server that has players, with its number of players."""
    return {r["guild_id"]: r["n"] for r in
            await _all(db, "SELECT guild_id, COUNT(*) AS n FROM users GROUP BY guild_id")}


async def player_exists(db: Database, guild_id: int, user_id: int) -> bool:
    return bool(await _one(db, "SELECT 1 AS x FROM users WHERE guild_id=? AND user_id=?", (guild_id, user_id)))


async def players(db: Database, guild_id: int) -> list[dict]:
    """Every player in the server with their balance, totals, last activity and credits bet per game."""
    users = await _all(db, """SELECT user_id, balance, games_played, wins, losses, total_wagered, total_won,
                                     biggest_win, last_daily FROM users WHERE guild_id=?""", (guild_id,))
    games = await _all(db, """SELECT user_id, game, SUM(wagered) AS wagered, SUM(payout) - SUM(wagered) AS net,
                                     SUM(CASE WHEN wagered>0 THEN 1 ELSE 0 END) AS plays, MAX(ts) AS last
                              FROM game_log WHERE guild_id=? GROUP BY user_id, game""", (guild_id,))
    seen = await _all(db, """SELECT user_id, MAX(ts) AS last FROM balance_log
                             WHERE guild_id=? AND source IS NULL GROUP BY user_id""", (guild_id,))
    in_play = await _all(db, "SELECT user_id, SUM(amount) AS a FROM escrow WHERE guild_id=? GROUP BY user_id",
                         (guild_id,))
    mix: dict[int, dict] = {}
    net: dict[int, int] = {}
    last: dict[int, float] = {r["user_id"]: r["last"] for r in seen}
    for g in games:
        uid = g["user_id"]
        if g["wagered"]:
            mix.setdefault(uid, {})[g["game"]] = g["wagered"]
        net[uid] = net.get(uid, 0) + (g["net"] or 0)
        last[uid] = max(last.get(uid) or 0, g["last"] or 0)
    escrow = {r["user_id"]: r["a"] for r in in_play}
    out = []
    for u in users:
        uid = u["user_id"]
        out.append({
            "id": str(uid), "balance": u["balance"], "in_play": escrow.get(uid, 0),
            "games": u["games_played"], "wins": u["wins"], "losses": u["losses"],
            "wagered": u["total_wagered"], "won": u["total_won"], "biggest_win": u["biggest_win"],
            "net": net.get(uid, 0), "last_active": last.get(uid) or (u["last_daily"] or None),
            "mix": mix.get(uid, {}),
        })
    return out


async def overview(db: Database, guild_id: int, period: str) -> dict:
    span = PERIODS.get(period, PERIODS["30d"])
    now = time.time()
    since = now - span if span else 0
    played = "guild_id=? AND ts>=? AND wagered>0"
    economy = await _one(db, "SELECT COALESCE(SUM(balance),0) AS held, COUNT(*) AS accounts FROM users WHERE guild_id=?",
                         (guild_id,))
    in_play = await _one(db, "SELECT COALESCE(SUM(amount),0) AS a FROM escrow WHERE guild_id=?", (guild_id,))
    pot = await _one(db, "SELECT COALESCE(SUM(pot),0) AS p FROM lottery WHERE guild_id=?", (guild_id,))
    totals = await _one(db, f"""SELECT COUNT(*) AS games, COALESCE(SUM(wagered),0) AS wagered,
                                       COUNT(DISTINCT user_id) AS players
                                FROM game_log WHERE {played}""", (guild_id, since))
    paid = await _one(db, "SELECT COALESCE(SUM(payout),0) AS paid FROM game_log WHERE guild_id=? AND ts>=?",
                      (guild_id, since))
    active = {}
    for key, secs in (("day", 86400), ("week", 7 * 86400)):
        r = await _one(db, """SELECT COUNT(DISTINCT user_id) AS n FROM balance_log
                              WHERE guild_id=? AND ts>=? AND source IS NULL""", (guild_id, now - secs))
        active[key] = r["n"]
    games = await _all(db, """SELECT game, SUM(CASE WHEN wagered>0 THEN 1 ELSE 0 END) AS plays,
                                     SUM(wagered) AS wagered, SUM(payout) AS paid,
                                     COUNT(DISTINCT user_id) AS players
                              FROM game_log WHERE guild_id=? AND ts>=? GROUP BY game
                              ORDER BY SUM(wagered) DESC""", (guild_id, since))
    money = await _all(db, """SELECT kind, COUNT(*) AS n,
                                     COALESCE(SUM(CASE WHEN amount > 0 THEN amount END), 0) AS added,
                                     COALESCE(-SUM(CASE WHEN amount < 0 THEN amount END), 0) AS removed
                              FROM money_log WHERE guild_id=? AND ts>=? GROUP BY kind""", (guild_id, since))
    # per day: games won / lost / even, credits bet and paid out (local days, oldest first, empty days included)
    days = CHART_DAYS.get(period, 30)
    today = datetime.date.today()
    first = today - datetime.timedelta(days=days - 1)
    start_ts = time.mktime(first.timetuple())
    rows = await _all(db, f"""SELECT date(ts, 'unixepoch', 'localtime') AS day,
                                     SUM(CASE WHEN payout > wagered THEN 1 ELSE 0 END) AS won,
                                     SUM(CASE WHEN payout < wagered THEN 1 ELSE 0 END) AS lost,
                                     SUM(CASE WHEN payout = wagered THEN 1 ELSE 0 END) AS even,
                                     SUM(wagered) AS wagered, SUM(payout) AS paid
                              FROM game_log WHERE {played} GROUP BY day""", (guild_id, start_ts))
    by_day = {r["day"]: r for r in rows}
    daily = []
    for i in range(days):
        d = (first + datetime.timedelta(days=i)).isoformat()
        r = by_day.get(d, {})
        daily.append({"day": d, "won": r.get("won", 0), "lost": r.get("lost", 0), "even": r.get("even", 0),
                      "wagered": r.get("wagered", 0), "paid": r.get("paid", 0)})
    circulation = await db.circulation_history(guild_id, since, buckets=200)
    edits = await _one(db, "SELECT COUNT(*) AS n, COALESCE(SUM(after - before), 0) AS net FROM admin_log "
                           "WHERE guild_id=? AND ts>=?", (guild_id, since))
    return {
        "period": period, "since": since or None,
        "kpis": {
            "held": economy["held"], "in_play": in_play["a"], "accounts": economy["accounts"],
            "active_day": active["day"], "active_week": active["week"],
            "games": totals["games"], "wagered": totals["wagered"], "paid": paid["paid"],
            "players": totals["players"], "lottery_pot": pot["p"],
            "edits": edits["n"], "edits_net": edits["net"],
        },
        "games": games, "money": money, "daily": daily,
        "circulation": [[ts, total] for ts, total in circulation],
    }


async def player_detail(db: Database, guild_id: int, user_id: int) -> dict:
    stats = await db.player_stats(guild_id, user_id)
    edits = await _all(db, """SELECT id, action, amount, before, after, note, ts FROM admin_log
                              WHERE guild_id=? AND user_id=? ORDER BY ts DESC LIMIT 20""", (guild_id, user_id))
    return {"stats": stats, "edits": edits}   # balance over time: balance_window


async def balance_window(db: Database, guild_id: int, user_id: int, start: float, end: float,
                         limit: int = 600) -> list[list]:
    """A player's balance from `start` to `end` (unix times; start 0 = from the first record) as [ts, balance]
    points, oldest first. The line starts at `start` with the balance it had then and runs on to `end`. Thinned to
    about `limit` time slots, keeping each slot's lowest and highest balance so peaks and dips survive."""
    before, rows = await db.balance_history(guild_id, user_id, start)
    points = [[r["ts"], r["balance"]] for r in rows if r["ts"] <= end]
    last = points[-1][1] if points else before
    if start and before is not None:
        points.insert(0, [start, before])
    if len(points) > limit * 2:
        t0, t1 = points[0][0], points[-1][0]
        width = (t1 - t0) / limit or 1
        thinned, slot, slot_no = [], [], None
        for p in points:
            n = int((p[0] - t0) / width)
            if slot and n != slot_no:
                lo, hi = min(slot, key=lambda x: x[1]), max(slot, key=lambda x: x[1])
                thinned += sorted({tuple(lo), tuple(hi), tuple(slot[-1])})
                slot = []
            slot_no = n
            slot.append(p)
        if slot:
            lo, hi = min(slot, key=lambda x: x[1]), max(slot, key=lambda x: x[1])
            thinned += sorted({tuple(lo), tuple(hi), tuple(slot[-1])})
        points = [list(p) for p in thinned]
    if last is not None and (not points or points[-1][0] < end):
        points.append([end, last])
    return points


async def edits(db: Database, guild_id: int, limit: int = 50) -> list[dict]:
    rows = await _all(db, """SELECT id, user_id, action, amount, before, after, note, ts FROM admin_log
                             WHERE guild_id=? ORDER BY ts DESC LIMIT ?""", (guild_id, limit))
    for r in rows:
        r["user_id"] = str(r["user_id"])
    return rows


async def activity(db: Database, guild_id: int, before: float | None, after: float | None,
                   limit: int = 100) -> list[dict]:
    """Everything that moved credits, newest first: finished games (game_log) and other movements such as daily
    claims, payments, shop purchases and admin changes (money_log). `before` pages back, `after` gets what's new."""
    cond, params = "", []
    if before is not None:
        cond += " AND ts < ?"
        params.append(before)
    if after is not None:
        cond += " AND ts > ?"
        params.append(after)
    sql = f"""SELECT * FROM (
                  SELECT 'g' || id AS id, ts, user_id, 'game' AS type, game AS kind, wagered, payout,
                         payout - wagered AS delta, NULL AS other_id
                  FROM game_log WHERE guild_id=? {cond}
                  UNION ALL
                  SELECT 'm' || id, ts, user_id, 'money', kind, NULL, NULL,
                         CASE WHEN kind='pay' THEN -amount ELSE amount END, other_id
                  FROM money_log WHERE guild_id=? {cond}
              ) ORDER BY ts DESC LIMIT ?"""
    rows = await _all(db, sql, (guild_id, *params, guild_id, *params, limit))
    for r in rows:
        r["user_id"] = str(r["user_id"]) if r["user_id"] is not None else None
        r["other_id"] = str(r["other_id"]) if r["other_id"] is not None else None
    return rows

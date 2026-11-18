"""The admin panel's deeper stats: the extra overview charts, the Games page and the extras in a player's details.
Read-only, like queries.py. Times are bucketed by the bot's local time (hours for a 24-hour period, days otherwise).
"""
from __future__ import annotations

import bisect
import datetime
import math
import time

import config
from admin_panel.queries import PERIODS, _all, _one
from database import Database

PLAYED = "guild_id=? AND ts>=? AND wagered>0"
# bet size groups for the bet sizes chart: [low, high) in credits
BET_EDGES = [1, 100, 1_000, 10_000, 100_000, 1_000_000, 10_000_000, 100_000_000]
MIN_LUCK_GAMES = 20           # luckiest / unluckiest players need at least this many games in the period
HEATMAP_MIN = 7 * 86400       # the "when people play" heatmap looks back at least a week


def expected_payback() -> dict[str, dict]:
    """What each game pays back on average by its rules and settings: {game: {value, approx}}. approx: depends on
    how it's played (blackjack, video poker) or on a mix of parts (Spin Frenzy's bonus spots). Games between players
    (duels) and the lottery have none."""
    edge = 1 - config.HOUSE_EDGE
    try:
        from cogs.instant import WHEEL
        wheel = sum(WHEEL) / len(WHEEL)
    except Exception:
        wheel = None
    out = {g: {"value": edge, "approx": False} for g in
           ("coinflip", "dice", "crash", "higherlower", "mines", "tower", "horserace", "cockfight")}
    out.update({
        "slots": {"value": config.SLOTS_RTP, "approx": False},
        "bangerbash": {"value": config.BANGER_RTP, "approx": False},
        "reefrush": {"value": config.REEF_RTP, "approx": False},
        "raccoonheist": {"value": config.HEIST_RTP, "approx": False},
        "spinfrenzy": {"value": config.SPIN_FRENZY_RTP, "approx": True},
        "roulette": {"value": 36 / 37, "approx": False},
        "blackjack": {"value": 0.995, "approx": True},
        "videopoker": {"value": 0.995, "approx": True},
        "poker": {"value": 1 - config.POKER_RAKE, "approx": False},
    })
    if wheel is not None:
        out["wheel"] = {"value": wheel, "approx": False}
    return out


# ------------------------------------------------------------------ time buckets
class Buckets:
    """The time slots of a chart: hours for a period of up to 2 days, days otherwise. `starts` are unix times."""

    def __init__(self, since: float, now: float, unit: str):
        self.unit = unit
        if unit == "hour":
            first = int(since // 3600) * 3600
            self.starts = [float(t) for t in range(first, int(now) + 1, 3600)]
            self.sql = "CAST(ts / 3600 AS INTEGER)"
        else:
            d0, d1 = datetime.date.fromtimestamp(since), datetime.date.fromtimestamp(now)
            days = [d0 + datetime.timedelta(days=i) for i in range((d1 - d0).days + 1)]
            self.starts = [time.mktime(d.timetuple()) for d in days]
            self._day = {d.isoformat(): i for i, d in enumerate(days)}
            self.sql = "date(ts, 'unixepoch', 'localtime')"

    def index_of_key(self, key) -> int | None:
        """The slot of a value of `sql`, or None when it's outside the chart."""
        if self.unit == "hour":
            i = bisect.bisect_right(self.starts, key * 3600) - 1
        else:
            i = self._day.get(key, -1)
        return i if 0 <= i < len(self.starts) else None

    def index_of_ts(self, ts: float) -> int | None:
        i = bisect.bisect_right(self.starts, ts) - 1
        return i if 0 <= i < len(self.starts) else None


async def _window(db: Database, guild_id: int, period: str) -> tuple[float, float, Buckets]:
    """(since, now, buckets) for a period; "all" starts at the server's first logged game or money movement."""
    now = time.time()
    span = PERIODS.get(period, PERIODS["24h"])
    if span:
        since = now - span
    else:
        r = await _one(db, """SELECT MIN(t) AS t FROM (SELECT MIN(ts) AS t FROM game_log WHERE guild_id=?
                              UNION ALL SELECT MIN(ts) FROM money_log WHERE guild_id=?)""", (guild_id, guild_id))
        since = r.get("t") or now - 86400
    return since, now, Buckets(since, now, "hour" if span and span <= 2 * 86400 else "day")


def _ids(rows: list[dict], *keys: str) -> None:
    """User ids as strings (JavaScript can't hold Discord ids as numbers)."""
    for r in rows:
        for k in keys:
            if r.get(k) is not None:
                r[k] = str(r[k])


async def _total_at(db: Database, guild_id: int, ts: float) -> int:
    """Credits all players held at `ts` (players without any balance history count at their current balance)."""
    r = await _one(db, """SELECT COALESCE(SUM(balance), 0) AS t FROM (
                              SELECT balance, ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY ts DESC, id DESC) AS rn
                              FROM balance_log WHERE guild_id=? AND ts < ?) WHERE rn = 1""", (guild_id, ts))
    u = await _one(db, """SELECT COALESCE(SUM(balance), 0) AS t FROM users u WHERE guild_id=? AND NOT EXISTS
                              (SELECT 1 FROM balance_log b WHERE b.guild_id=u.guild_id AND b.user_id=u.user_id)""",
                   (guild_id,))
    return r["t"] + u["t"]


# ------------------------------------------------------------------ overview extras
async def overview_extras(db: Database, guild_id: int, period: str) -> dict:
    since, now, b = await _window(db, guild_id, period)
    n = len(b.starts)
    timeline = [{"t": t, "games": 0, "wagered": 0, "paid": 0, "by_game": {}, "players": 0, "new_players": 0,
                 "restarts": 0, "restart_credits": 0} for t in b.starts]

    # house result per slot and game
    for r in await _all(db, f"""SELECT {b.sql} AS k, game, SUM(CASE WHEN wagered>0 THEN 1 ELSE 0 END) AS plays,
                                       SUM(wagered) AS wagered, SUM(payout) AS paid
                                FROM game_log WHERE guild_id=? AND ts>=? GROUP BY k, game""", (guild_id, since)):
        i = b.index_of_key(r["k"])
        if i is None:
            continue
        s = timeline[i]
        s["games"] += r["plays"]
        s["wagered"] += r["wagered"]
        s["paid"] += r["paid"]
        s["by_game"][r["game"]] = s["by_game"].get(r["game"], 0) + r["wagered"] - r["paid"]

    # players per slot, and new players (their first game ever)
    for r in await _all(db, f"""SELECT {b.sql} AS k, COUNT(DISTINCT user_id) AS n FROM game_log
                                WHERE {PLAYED} GROUP BY k""", (guild_id, since)):
        i = b.index_of_key(r["k"])
        if i is not None:
            timeline[i]["players"] = r["n"]
    for r in await _all(db, """SELECT MIN(ts) AS first FROM game_log WHERE guild_id=? AND wagered>0
                               GROUP BY user_id HAVING first >= ?""", (guild_id, since)):
        i = b.index_of_ts(r["first"])
        if i is not None:
            timeline[i]["new_players"] += 1

    # going broke: /restart (and the Activity's restart button) is only open to broke players
    for r in await _all(db, f"""SELECT {b.sql} AS k, COUNT(*) AS n, SUM(amount) AS credits FROM money_log
                                WHERE guild_id=? AND ts>=? AND kind='restart' GROUP BY k""", (guild_id, since)):
        i = b.index_of_key(r["k"])
        if i is not None:
            timeline[i]["restarts"] = r["n"]
            timeline[i]["restart_credits"] = r["credits"]
    broke = await _all(db, """SELECT user_id, COUNT(*) AS n, SUM(amount) AS credits, MAX(ts) AS last FROM money_log
                              WHERE guild_id=? AND ts>=? AND kind='restart'
                              GROUP BY user_id ORDER BY n DESC, last DESC LIMIT 10""", (guild_id, since))
    _ids(broke, "user_id")

    # when people play: weekday (0 = Monday) x hour, over at least the last week
    heat_since = since if not PERIODS.get(period) else min(since, now - HEATMAP_MIN)
    heat = await _all(db, f"""SELECT (CAST(strftime('%w', ts, 'unixepoch', 'localtime') AS INTEGER) + 6) % 7 AS dow,
                                     CAST(strftime('%H', ts, 'unixepoch', 'localtime') AS INTEGER) AS hour,
                                     COUNT(*) AS games, SUM(wagered) AS wagered,
                                     COUNT(DISTINCT user_id) AS players
                              FROM game_log WHERE {PLAYED} GROUP BY dow, hour""", (guild_id, heat_since))

    # where the credits came from and went: start, every kind of movement outside games, games, end
    start = await _total_at(db, guild_id, since) if PERIODS.get(period) else 0
    end = (await _one(db, "SELECT COALESCE(SUM(balance), 0) AS t FROM users WHERE guild_id=?", (guild_id,)))["t"]
    money = await _all(db, """SELECT kind, SUM(amount) AS amount, COUNT(*) AS n FROM money_log
                              WHERE guild_id=? AND ts>=? AND kind != 'pay' GROUP BY kind
                              ORDER BY ABS(SUM(amount)) DESC""", (guild_id, since))
    games_net = (await _one(db, "SELECT COALESCE(SUM(payout - wagered), 0) AS t FROM game_log "
                                "WHERE guild_id=? AND ts>=?", (guild_id, since)))["t"]
    steps = [{"kind": m["kind"], "amount": m["amount"], "n": m["n"]} for m in money if m["amount"]]
    steps.append({"kind": "games", "amount": games_net, "n": None})
    other = end - start - sum(s["amount"] for s in steps)
    if other:
        # bets still on the table, a period that starts mid-game, players who left the history
        steps.append({"kind": "other", "amount": other, "n": None})
    flow = {"start": start, "end": end, "steps": steps}

    # bet sizes
    case = " ".join(f"WHEN wagered < {hi} THEN {i}" for i, hi in enumerate(BET_EDGES[1:]))
    sizes = {r["g"]: r for r in await _all(
        db, f"""SELECT CASE {case} ELSE {len(BET_EDGES) - 1} END AS g, COUNT(*) AS rounds, SUM(wagered) AS wagered
                FROM game_log WHERE {PLAYED} GROUP BY g""", (guild_id, since))}
    bets = []
    for i, lo in enumerate(BET_EDGES):
        hi = BET_EDGES[i + 1] if i + 1 < len(BET_EDGES) else None
        r = sizes.get(i, {})
        bets.append({"lo": lo, "hi": hi, "rounds": r.get("rounds", 0), "wagered": r.get("wagered", 0)})

    return {
        "since": since, "bucket": b.unit, "timeline": timeline, "broke": broke,
        "heatmap": {"since": heat_since, "cells": heat}, "flow": flow, "bets": bets,
        "wealth": await _wealth(db, guild_id, since, b),
        "records": await _records(db, guild_id, since),
    }


def _shares(values: list[int]) -> dict | None:
    """Top 10% share, the richest player's share and the Gini coefficient (0 = everyone equal, 1 = one has all)."""
    vals = sorted((v for v in values if v > 0), reverse=True)
    accounts = len(values)
    total = sum(vals)
    if not total or not accounts:
        return None
    top = max(1, math.ceil(accounts * 0.1))
    asc = sorted(values)
    gini = sum((2 * (i + 1) - accounts - 1) * v for i, v in enumerate(asc)) / (accounts * total)
    return {"top10": sum(vals[:top]) / total, "top1": vals[0] / total, "gini": gini, "accounts": accounts,
            "top_n": top}


async def _wealth(db: Database, guild_id: int, since: float, b: Buckets) -> list[dict]:
    """How equal the credits are spread at the end of every slot (every account the server had then)."""
    held = {r["user_id"]: r["balance"] for r in await _all(
        db, """SELECT user_id, balance FROM (
                   SELECT user_id, balance, ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY ts DESC, id DESC) AS rn
                   FROM balance_log WHERE guild_id=? AND ts < ?) WHERE rn = 1""", (guild_id, since))}
    unlogged = [r["balance"] for r in await _all(
        db, """SELECT balance FROM users u WHERE guild_id=? AND NOT EXISTS
                   (SELECT 1 FROM balance_log l WHERE l.guild_id=u.guild_id AND l.user_id=u.user_id)""", (guild_id,))]
    cur = await db.conn.execute("SELECT ts, user_id, balance FROM balance_log WHERE guild_id=? AND ts >= ? "
                                "ORDER BY ts, id", (guild_id, since))
    out = []
    ends = b.starts[1:] + [time.time() + 1]
    i = 0
    async for r in cur:
        while i < len(ends) and r["ts"] >= ends[i]:
            s = _shares(list(held.values()) + unlogged)
            out.append({"t": b.starts[i], **s} if s else {"t": b.starts[i]})
            i += 1
        held[r["user_id"]] = r["balance"]
    while i < len(ends):
        s = _shares(list(held.values()) + unlogged)
        out.append({"t": b.starts[i], **s} if s else {"t": b.starts[i]})
        i += 1
    return out


async def _records(db: Database, guild_id: int, since: float) -> dict:
    cols = "user_id, game, wagered, payout, ts"
    out = {
        "wins": await _all(db, f"""SELECT {cols} FROM game_log WHERE {PLAYED} AND payout > wagered
                                   ORDER BY payout - wagered DESC LIMIT 8""", (guild_id, since)),
        "multipliers": await _all(db, f"""SELECT {cols} FROM game_log WHERE {PLAYED} AND payout > wagered
                                          ORDER BY CAST(payout AS REAL) / wagered DESC, payout DESC LIMIT 8""",
                                  (guild_id, since)),
        "losses": await _all(db, f"""SELECT {cols} FROM game_log WHERE {PLAYED} AND payout < wagered
                                     ORDER BY wagered - payout DESC LIMIT 8""", (guild_id, since)),
    }
    per_player = f"""SELECT user_id, COUNT(*) AS games, SUM(wagered) AS wagered, SUM(payout) AS paid,
                            SUM(payout) - SUM(wagered) AS net
                     FROM game_log WHERE {PLAYED} GROUP BY user_id"""
    out["winners"] = await _all(db, f"{per_player} HAVING net > 0 ORDER BY net DESC LIMIT 8", (guild_id, since))
    out["losers"] = await _all(db, f"{per_player} HAVING net < 0 ORDER BY net LIMIT 8", (guild_id, since))
    luck = f"{per_player} HAVING games >= {MIN_LUCK_GAMES}"
    out["luckiest"] = await _all(db, f"{luck} ORDER BY CAST(paid AS REAL) / wagered DESC LIMIT 8", (guild_id, since))
    out["unluckiest"] = await _all(db, f"{luck} ORDER BY CAST(paid AS REAL) / wagered LIMIT 8", (guild_id, since))
    for rows in out.values():
        _ids(rows, "user_id")
    out["min_games"] = MIN_LUCK_GAMES
    return out


# ------------------------------------------------------------------ games page
async def games(db: Database, guild_id: int, period: str) -> dict:
    since, now, b = await _window(db, guild_id, period)
    rows = await _all(db, """SELECT game, SUM(CASE WHEN wagered>0 THEN 1 ELSE 0 END) AS plays,
                                    COUNT(DISTINCT CASE WHEN wagered>0 THEN user_id END) AS players,
                                    SUM(wagered) AS wagered, SUM(payout) AS paid,
                                    SUM(CASE WHEN wagered>0 AND payout>0 THEN 1 ELSE 0 END) AS hits,
                                    SUM(CASE WHEN wagered>0 AND payout>wagered THEN 1 ELSE 0 END) AS wins,
                                    AVG(CASE WHEN wagered>0 AND payout>0 THEN CAST(payout AS REAL) / wagered END)
                                        AS avg_multiplier,
                                    MAX(CASE WHEN wagered>0 THEN CAST(payout AS REAL) / wagered END) AS max_multiplier
                             FROM game_log WHERE guild_id=? AND ts>=? GROUP BY game
                             HAVING SUM(wagered) > 0 ORDER BY SUM(wagered) DESC""", (guild_id, since))
    expected = expected_payback()
    by_game = {}
    for r in rows:
        r["expected"] = expected.get(r["game"])
        r["timeline"] = [{"t": t, "plays": 0, "wagered": 0, "paid": 0} for t in b.starts]
        r["top_wins"], r["top_players"] = [], []
        by_game[r["game"]] = r
    for r in await _all(db, f"""SELECT {b.sql} AS k, game, SUM(CASE WHEN wagered>0 THEN 1 ELSE 0 END) AS plays,
                                       SUM(wagered) AS wagered, SUM(payout) AS paid
                                FROM game_log WHERE guild_id=? AND ts>=? GROUP BY k, game""", (guild_id, since)):
        g, i = by_game.get(r["game"]), b.index_of_key(r["k"])
        if g is not None and i is not None:
            g["timeline"][i].update(plays=r["plays"], wagered=r["wagered"], paid=r["paid"])
    for r in await _all(db, f"""SELECT * FROM (
                                    SELECT game, user_id, wagered, payout, ts, ROW_NUMBER() OVER
                                        (PARTITION BY game ORDER BY payout - wagered DESC, ts DESC) AS rn
                                    FROM game_log WHERE {PLAYED} AND payout > wagered) WHERE rn <= 3""",
                            (guild_id, since)):
        if r["game"] in by_game:
            by_game[r["game"]]["top_wins"].append(r)
    for r in await _all(db, f"""SELECT * FROM (
                                    SELECT game, user_id, plays, wagered, net, ROW_NUMBER() OVER
                                        (PARTITION BY game ORDER BY wagered DESC) AS rn
                                    FROM (SELECT game, user_id, COUNT(*) AS plays, SUM(wagered) AS wagered,
                                                 SUM(payout) - SUM(wagered) AS net
                                          FROM game_log WHERE {PLAYED} GROUP BY game, user_id)) WHERE rn <= 3""",
                            (guild_id, since)):
        if r["game"] in by_game:
            by_game[r["game"]]["top_players"].append(r)
    for g in rows:
        _ids(g["top_wins"], "user_id")
        _ids(g["top_players"], "user_id")
    return {"since": since, "bucket": b.unit, "games": rows}


# ------------------------------------------------------------------ player extras
async def player_extras(db: Database, guild_id: int, user_id: int) -> dict:
    args = (guild_id, user_id)
    heat = await _all(db, """SELECT (CAST(strftime('%w', ts, 'unixepoch', 'localtime') AS INTEGER) + 6) % 7 AS dow,
                                    CAST(strftime('%H', ts, 'unixepoch', 'localtime') AS INTEGER) AS hour,
                                    COUNT(*) AS games, SUM(wagered) AS wagered
                             FROM game_log WHERE guild_id=? AND user_id=? AND wagered>0
                             GROUP BY dow, hour""", args)
    # net result per day, the last 30 days
    today = datetime.date.today()
    first = today - datetime.timedelta(days=29)
    rows = {r["day"]: r for r in await _all(
        db, """SELECT date(ts, 'unixepoch', 'localtime') AS day, COUNT(*) AS games, SUM(wagered) AS wagered,
                      SUM(payout) - SUM(wagered) AS net
               FROM game_log WHERE guild_id=? AND user_id=? AND ts>=? GROUP BY day""",
        (*args, time.mktime(first.timetuple())))}
    daily = []
    for i in range(30):
        d = (first + datetime.timedelta(days=i)).isoformat()
        r = rows.get(d, {})
        daily.append({"day": d, "games": r.get("games", 0), "wagered": r.get("wagered", 0), "net": r.get("net", 0)})
    # where their bets go, and their payback next to everyone's in the same games
    mine = await _all(db, """SELECT game, COUNT(*) AS plays, SUM(wagered) AS wagered, SUM(payout) AS paid
                             FROM game_log WHERE guild_id=? AND user_id=? AND wagered>0 GROUP BY game
                             ORDER BY SUM(wagered) DESC""", args)
    everyone = {r["game"]: r for r in await _all(
        db, """SELECT game, SUM(wagered) AS wagered, SUM(payout) AS paid FROM game_log
               WHERE guild_id=? AND wagered>0 GROUP BY game""", (guild_id,))}
    for m in mine:
        e = everyone.get(m["game"], {})
        m["all_wagered"], m["all_paid"] = e.get("wagered", 0), e.get("paid", 0)
    total = await _one(db, "SELECT COALESCE(SUM(wagered), 0) AS w, COALESCE(SUM(payout), 0) AS p FROM game_log "
                           "WHERE guild_id=? AND wagered>0", (guild_id,))
    broke = await _one(db, """SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS credits, MAX(ts) AS last
                              FROM money_log WHERE guild_id=? AND user_id=? AND kind='restart'""", args)
    week = await _one(db, """SELECT COUNT(*) AS n FROM money_log WHERE guild_id=? AND user_id=? AND kind='restart'
                             AND ts>=?""", (*args, time.time() - 7 * 86400))
    broke["week"] = week["n"]
    # /pay: who they sent credits to and got credits from
    sent = await _all(db, """SELECT other_id AS user_id, COUNT(*) AS n, SUM(amount) AS amount, MAX(ts) AS last
                             FROM money_log WHERE guild_id=? AND user_id=? AND kind='pay' AND other_id IS NOT NULL
                             GROUP BY other_id""", args)
    got = await _all(db, """SELECT user_id, COUNT(*) AS n, SUM(amount) AS amount, MAX(ts) AS last
                            FROM money_log WHERE guild_id=? AND other_id=? AND kind='pay' GROUP BY user_id""", args)
    people: dict[int, dict] = {}
    for key, rows_ in (("sent", sent), ("received", got)):
        for r in rows_:
            p = people.setdefault(r["user_id"], {"user_id": r["user_id"], "sent": 0, "sent_n": 0, "received": 0,
                                                 "received_n": 0, "last": 0})
            p[key] += r["amount"]
            p[f"{key}_n"] += r["n"]
            p["last"] = max(p["last"], r["last"])
    payments = sorted(people.values(), key=lambda p: p["sent"] + p["received"], reverse=True)
    _ids(payments, "user_id")
    streaks = await db.best_streaks(guild_id, user_id)
    return {
        "heatmap": heat, "daily": daily, "mix": mine,
        "everyone": {"wagered": total["w"], "paid": total["p"]},
        "broke": broke, "payments": payments,
        "streaks": [{"game": g, "best": n} for g, n in sorted(streaks.items(), key=lambda x: -x[1])],
    }

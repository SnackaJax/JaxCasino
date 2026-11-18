"""SQLite storage for balances, stats, settings, escrowed bets and the lottery."""
import asyncio
import json
import logging
import time
from typing import Any

import aiosqlite

import config

log = logging.getLogger("jaxcasino.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    guild_id      INTEGER NOT NULL,
    user_id       INTEGER NOT NULL,
    balance       INTEGER NOT NULL,
    last_daily    REAL    NOT NULL DEFAULT 0,
    games_played  INTEGER NOT NULL DEFAULT 0,
    wins          INTEGER NOT NULL DEFAULT 0,
    losses        INTEGER NOT NULL DEFAULT 0,
    total_wagered INTEGER NOT NULL DEFAULT 0,
    total_won     INTEGER NOT NULL DEFAULT 0,
    biggest_win   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);
CREATE TABLE IF NOT EXISTS settings (
    guild_id INTEGER NOT NULL,
    key      TEXT    NOT NULL,
    value    TEXT    NOT NULL,
    PRIMARY KEY (guild_id, key)
);
-- Credits that have been taken from a player for a game that has not finished yet.
-- Anything left here when the bot starts is refunded (e.g. after a crash/restart mid-game).
CREATE TABLE IF NOT EXISTS escrow (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    amount   INTEGER NOT NULL,
    game     TEXT    NOT NULL,
    created  REAL    NOT NULL
);
CREATE TABLE IF NOT EXISTS lottery (
    guild_id   INTEGER PRIMARY KEY,
    pot        INTEGER NOT NULL DEFAULT 0,
    draw_at    REAL    NOT NULL,
    channel_id INTEGER
);
-- One row per finished game (poker: one row per table session, buy-in vs cash-out).
CREATE TABLE IF NOT EXISTS game_log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    game     TEXT    NOT NULL,
    wagered  INTEGER NOT NULL,
    payout   INTEGER NOT NULL,
    ts       REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS game_log_guild_ts ON game_log (guild_id, ts);
-- Credits that entered or moved around the economy outside of games: claims, admin changes, payments.
-- For kind 'pay', user_id is the sender and other_id the recipient.
CREATE TABLE IF NOT EXISTS money_log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id  INTEGER,
    kind     TEXT    NOT NULL,
    amount   INTEGER NOT NULL,
    ts       REAL    NOT NULL,
    other_id INTEGER
);
CREATE INDEX IF NOT EXISTS money_log_guild_ts ON money_log (guild_id, ts);
CREATE TABLE IF NOT EXISTS lottery_tickets (
    guild_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    tickets  INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);
-- Fighting chickens for /cockfight, bought in the shop. One per player; the row is deleted when it dies.
CREATE TABLE IF NOT EXISTS chickens (
    guild_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    wins     INTEGER NOT NULL DEFAULT 0,
    born     REAL    NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);
-- Every balance change, written by the triggers below, for the balance graph in /stats.
-- source is NULL for live rows and 'rebuilt' for history reconstructed from game_log/money_log.
CREATE TABLE IF NOT EXISTS balance_log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    ts       REAL    NOT NULL,
    balance  INTEGER NOT NULL,
    delta    INTEGER NOT NULL,
    source   TEXT
);
CREATE INDEX IF NOT EXISTS balance_log_user_ts ON balance_log (guild_id, user_id, ts);
CREATE TRIGGER IF NOT EXISTS balance_log_on_update AFTER UPDATE OF balance ON users
WHEN NEW.balance != OLD.balance
BEGIN
    INSERT INTO balance_log (guild_id, user_id, ts, balance, delta)
    VALUES (NEW.guild_id, NEW.user_id, (julianday('now') - 2440587.5) * 86400.0, NEW.balance,
            NEW.balance - OLD.balance);
END;
CREATE TRIGGER IF NOT EXISTS balance_log_on_insert AFTER INSERT ON users
BEGIN
    INSERT INTO balance_log (guild_id, user_id, ts, balance, delta)
    VALUES (NEW.guild_id, NEW.user_id, (julianday('now') - 2440587.5) * 86400.0, NEW.balance, NEW.balance);
END;
-- Shop effects that finish later: "unmute" (end of a bought voice mute) and "randomkick" (a hidden
-- random disconnect). Kept here so they survive a bot restart.
CREATE TABLE IF NOT EXISTS shop_jobs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id  INTEGER NOT NULL,
    kind      TEXT    NOT NULL,
    due       REAL    NOT NULL,
    target_id INTEGER,
    buyer_id  INTEGER NOT NULL,
    cost      INTEGER NOT NULL
);
-- Balance changes made in the admin panel (admin_panel/), with the balance before and after and an optional note.
-- The change itself is also in money_log (kind 'admin'), like an /admin command's.
CREATE TABLE IF NOT EXISTS admin_log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    action   TEXT    NOT NULL,
    amount   INTEGER NOT NULL,
    before   INTEGER NOT NULL,
    after    INTEGER NOT NULL,
    note     TEXT,
    ts       REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS admin_log_guild_ts ON admin_log (guild_id, ts);
-- /crux: a player's self-chosen break from the bot and the Activity, until `until` (unix time). Only an admin
-- (/admin uncrux, or the admin panel) can end it early.
CREATE TABLE IF NOT EXISTS crux (
    guild_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    until    REAL    NOT NULL,
    started  REAL    NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);
-- Each player's longest winstreak per game (Activity blackjack and poker seats), and when they reached it.
CREATE TABLE IF NOT EXISTS streaks (
    guild_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    game     TEXT    NOT NULL,
    best     INTEGER NOT NULL,
    ts       REAL    NOT NULL,
    PRIMARY KEY (guild_id, user_id, game)
);
"""


def _check_amount(amount: int) -> None:
    """Debits and transfers only take amounts of 0 or more: a negative amount would quietly add credits instead.
    Every caller already checks its input; this is a last line of defence."""
    if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
        raise ValueError(f"invalid credit amount: {amount!r}")


class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None
        self.lock = asyncio.Lock()

    async def connect(self) -> None:
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        cur = await self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='balance_log'")
        new_balance_log = await cur.fetchone() is None
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()
        if new_balance_log:
            await self._rebuild_balance_history()
        await self._migrate_money_log()

    async def _migrate_money_log(self) -> None:
        """Adds money_log.other_id (payment recipient) to databases made before it existed, then fills it in for
        earlier payments from the bot's log files, where every /pay command is written down."""
        cur = await self.conn.execute("PRAGMA table_info(money_log)")
        if any(r["name"] == "other_id" for r in await cur.fetchall()):
            return
        await self.conn.execute("ALTER TABLE money_log ADD COLUMN other_id INTEGER")
        await self.conn.commit()
        try:
            found = await self._backfill_pay_recipients()
        except Exception:
            log.exception("Couldn't fill in recipients of earlier payments from the log files")
            return
        if found:
            log.info("Payment history: found the recipient of %d earlier payment(s) in the log files.", found)

    async def _backfill_pay_recipients(self) -> int:
        """Matches earlier 'pay' rows to '/pay user=<to> amount=<x> by <name> (<from>)' lines in the log files:
        same sender, same amount, logged at the same moment (allowing for the log's time zone)."""
        import datetime
        import glob
        import os
        import re
        from utils.economy import BetError, parse_amount

        cur = await self.conn.execute(
            "SELECT id, user_id, amount, ts FROM money_log WHERE kind='pay' AND other_id IS NULL")
        rows = await cur.fetchall()
        if not rows:
            return 0
        line_re = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\S*\s.*?/pay user=(\d+) amount=(\S+) by .*\((\d+)\)")
        entries = []   # (local time as if UTC, sender, recipient, raw amount)
        paths = [*glob.glob(os.path.join(config.LOG_DIR, "jaxcasino.log*")),
                 *glob.glob(os.path.join(config.LOG_DIR, "gamblebot.log*"))]   # the log's name before the rename
        for path in paths:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if "/pay user=" not in line:
                        continue
                    m = line_re.match(line)
                    if m:
                        t = datetime.datetime.strptime(m[1], "%Y-%m-%d %H:%M:%S")
                        t = t.replace(tzinfo=datetime.timezone.utc).timestamp()
                        entries.append((t, int(m[4]), int(m[2]), m[3]))
        used = set()
        updates = []
        for r in rows:
            for i, (t, sender, to, raw) in enumerate(entries):
                if i in used or sender != r["user_id"]:
                    continue
                gap = t - r["ts"]          # a whole number of half hours apart = the log's time zone
                if abs(gap - round(gap / 1800) * 1800) > 5:
                    continue
                try:
                    amount = parse_amount(raw, r["amount"])
                except BetError:
                    continue
                if raw.strip().lower() in ("all", "max", "allin", "all-in", "half") or amount == r["amount"]:
                    used.add(i)
                    updates.append((to, r["id"]))
                    break
        if updates:
            await self.conn.executemany("UPDATE money_log SET other_id=? WHERE id=?", updates)
            await self.conn.commit()
        return len(updates)

    async def _rebuild_balance_history(self) -> None:
        """Runs once, when balance_log is first created: rebuilds each player's earlier balances by walking
        backwards from their current balance through game_log and money_log. It's approximate: credits
        received with /pay and admin give-all/remove-all were not logged per player before this."""
        cur = await self.conn.execute("SELECT guild_id, user_id, balance FROM users")
        users = await cur.fetchall()
        cur = await self.conn.execute(
            "SELECT guild_id, user_id, COALESCE(SUM(amount), 0) AS a FROM escrow GROUP BY guild_id, user_id")
        in_play = {(r["guild_id"], r["user_id"]): r["a"] for r in await cur.fetchall()}
        rows = []
        now = time.time()
        for u in users:
            gid, uid = u["guild_id"], u["user_id"]
            cur = await self.conn.execute(
                """SELECT ts, payout - wagered AS delta FROM game_log WHERE guild_id=? AND user_id=?
                   UNION ALL
                   SELECT ts, CASE WHEN kind='pay' THEN -amount ELSE amount END FROM money_log
                   WHERE guild_id=? AND user_id=?
                   ORDER BY ts DESC""", (gid, uid, gid, uid))
            events = await cur.fetchall()
            if not events:
                continue
            balance = u["balance"] + in_play.get((gid, uid), 0)  # escrowed bets are refunded at startup
            points = [(now, balance, 0)]
            for e in events:
                points.append((e["ts"], balance, e["delta"]))
                balance -= e["delta"]
            points.append((events[-1]["ts"] - 1, balance, 0))  # the balance before the first recorded event
            rows += [(gid, uid, ts, bal, delta, "rebuilt") for ts, bal, delta in points[1:]]
        if rows:
            await self.conn.executemany(
                "INSERT INTO balance_log (guild_id, user_id, ts, balance, delta, source) VALUES (?, ?, ?, ?, ?, ?)",
                rows)
            await self.conn.commit()
        log.info("Created balance history: rebuilt %d point(s) for %d player(s) from earlier games.",
                 len(rows), len({(r[0], r[1]) for r in rows}))

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    async def _money(self, guild_id: int, user_id: int | None, kind: str, amount: int,
                     other_id: int | None = None) -> None:
        """Records a non-game credit movement (other_id: the recipient of a payment). Call while holding the
        lock, before commit."""
        if amount:
            await self.conn.execute(
                "INSERT INTO money_log (guild_id, user_id, kind, amount, ts, other_id) VALUES (?, ?, ?, ?, ?, ?)",
                (guild_id, user_id, kind, amount, time.time(), other_id),
            )

    async def _log_game(self, guild_id: int, user_id: int, game: str, wagered: int, payout: int) -> None:
        await self.conn.execute(
            "INSERT INTO game_log (guild_id, user_id, game, wagered, payout, ts) VALUES (?, ?, ?, ?, ?, ?)",
            (guild_id, user_id, game, wagered, payout, time.time()),
        )

    async def _balance(self, guild_id: int, user_id: int) -> int:
        cur = await self.conn.execute(
            "SELECT balance FROM users WHERE guild_id=? AND user_id=?", (guild_id, user_id)
        )
        return (await cur.fetchone())["balance"]

    async def _total(self, guild_id: int) -> int:
        cur = await self.conn.execute(
            "SELECT COALESCE(SUM(balance), 0) AS t FROM users WHERE guild_id=?", (guild_id,))
        return (await cur.fetchone())["t"]

    # ------------------------------------------------------------------ users
    async def _ensure(self, guild_id: int, user_id: int) -> None:
        await self.conn.execute(
            "INSERT OR IGNORE INTO users (guild_id, user_id, balance) VALUES (?, ?, ?)",
            (guild_id, user_id, config.STARTING_BALANCE),
        )

    async def get_balance(self, guild_id: int, user_id: int) -> int:
        async with self.lock:
            await self._ensure(guild_id, user_id)
            await self.conn.commit()
            cur = await self.conn.execute(
                "SELECT balance FROM users WHERE guild_id=? AND user_id=?", (guild_id, user_id)
            )
            return (await cur.fetchone())["balance"]

    async def get_user(self, guild_id: int, user_id: int) -> aiosqlite.Row:
        async with self.lock:
            await self._ensure(guild_id, user_id)
            await self.conn.commit()
            cur = await self.conn.execute(
                "SELECT * FROM users WHERE guild_id=? AND user_id=?", (guild_id, user_id)
            )
            return await cur.fetchone()

    async def add_balance(self, guild_id: int, user_id: int, amount: int, kind: str = "admin") -> int:
        """Adds (or subtracts, if negative) credits. Balance never goes below 0. Returns new balance."""
        async with self.lock:
            await self._ensure(guild_id, user_id)
            before = await self._balance(guild_id, user_id)
            await self.conn.execute(
                "UPDATE users SET balance = MAX(0, balance + ?) WHERE guild_id=? AND user_id=?",
                (amount, guild_id, user_id),
            )
            after = await self._balance(guild_id, user_id)
            await self._money(guild_id, user_id, kind, after - before)
            await self.conn.commit()
            return after

    async def set_balance(self, guild_id: int, user_id: int, amount: int, kind: str = "admin") -> None:
        async with self.lock:
            await self._ensure(guild_id, user_id)
            before = await self._balance(guild_id, user_id)
            await self.conn.execute(
                "UPDATE users SET balance=? WHERE guild_id=? AND user_id=?",
                (max(0, amount), guild_id, user_id),
            )
            await self._money(guild_id, user_id, kind, max(0, amount) - before)
            await self.conn.commit()

    async def admin_edit(self, guild_id: int, user_id: int, action: str, amount: int,
                         note: str | None = None) -> tuple[int, int]:
        """A balance change from the admin panel: action 'set' (to exactly `amount`), 'add' or 'remove'. The
        balance never goes below 0. Logged in money_log (kind 'admin') and admin_log. Returns (before, after)."""
        _check_amount(amount)
        if action not in ("set", "add", "remove"):
            raise ValueError(f"invalid action: {action!r}")
        async with self.lock:
            await self._ensure(guild_id, user_id)
            before = await self._balance(guild_id, user_id)
            after = amount if action == "set" else before + amount if action == "add" else max(0, before - amount)
            await self.conn.execute("UPDATE users SET balance=? WHERE guild_id=? AND user_id=?",
                                    (after, guild_id, user_id))
            await self._money(guild_id, user_id, "admin", after - before)
            await self.conn.execute(
                """INSERT INTO admin_log (guild_id, user_id, action, amount, before, after, note, ts)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (guild_id, user_id, action, amount, before, after, note or None, time.time()))
            await self.conn.commit()
            return before, after

    async def try_debit(self, guild_id: int, user_id: int, amount: int) -> bool:
        """Atomically removes `amount` if the user can afford it."""
        _check_amount(amount)
        async with self.lock:
            await self._ensure(guild_id, user_id)
            cur = await self.conn.execute(
                "UPDATE users SET balance = balance - ? WHERE guild_id=? AND user_id=? AND balance >= ?",
                (amount, guild_id, user_id, amount),
            )
            await self.conn.commit()
            return cur.rowcount == 1

    async def transfer(self, guild_id: int, from_id: int, to_id: int, amount: int) -> bool:
        _check_amount(amount)
        async with self.lock:
            await self._ensure(guild_id, from_id)
            await self._ensure(guild_id, to_id)
            cur = await self.conn.execute(
                "UPDATE users SET balance = balance - ? WHERE guild_id=? AND user_id=? AND balance >= ?",
                (amount, guild_id, from_id, amount),
            )
            if cur.rowcount != 1:
                await self.conn.rollback()
                return False
            await self.conn.execute(
                "UPDATE users SET balance = balance + ? WHERE guild_id=? AND user_id=?",
                (amount, guild_id, to_id),
            )
            await self._money(guild_id, from_id, "pay", amount, other_id=to_id)
            await self.conn.commit()
            return True

    async def bulk_add(self, guild_id: int, user_ids: list[int], amount: int) -> None:
        """Adds `amount` to every user (negative amounts remove, never below 0)."""
        async with self.lock:
            await self.conn.executemany(
                "INSERT OR IGNORE INTO users (guild_id, user_id, balance) VALUES (?, ?, ?)",
                [(guild_id, uid, config.STARTING_BALANCE) for uid in user_ids],
            )
            before = await self._total(guild_id)
            await self.conn.executemany(
                "UPDATE users SET balance = MAX(0, balance + ?) WHERE guild_id=? AND user_id=?",
                [(amount, guild_id, uid) for uid in user_ids],
            )
            await self._money(guild_id, None, "admin", await self._total(guild_id) - before)
            await self.conn.commit()

    async def claim_daily(self, guild_id: int, user_id: int) -> tuple[bool, float, int]:
        """Returns (claimed, next_claim_timestamp, new_balance)."""
        now = time.time()
        async with self.lock:
            await self._ensure(guild_id, user_id)
            cur = await self.conn.execute(
                "SELECT last_daily, balance FROM users WHERE guild_id=? AND user_id=?",
                (guild_id, user_id),
            )
            row = await cur.fetchone()
            next_at = row["last_daily"] + config.DAILY_COOLDOWN
            if now < next_at:
                await self.conn.commit()
                return False, next_at, row["balance"]
            await self.conn.execute(
                "UPDATE users SET balance = balance + ?, last_daily=? WHERE guild_id=? AND user_id=?",
                (config.DAILY_AMOUNT, now, guild_id, user_id),
            )
            await self._money(guild_id, user_id, "claim", config.DAILY_AMOUNT)
            await self.conn.commit()
            return True, now + config.DAILY_COOLDOWN, row["balance"] + config.DAILY_AMOUNT

    async def restart(self, guild_id: int, user_id: int, grant: bool) -> tuple[str, int, float]:
        """Checks (and with grant=True, performs) a restart for a broke player.
        Returns (status, balance, blocked_until). status is one of:
        "ok" (eligible / granted), "not_broke", "in_game" (credits still in an unfinished game),
        "paid" (sent credits with /pay recently; blocked_until says when that expires)."""
        async with self.lock:
            await self._ensure(guild_id, user_id)
            balance = await self._balance(guild_id, user_id)
            if balance >= config.MIN_BET:
                await self.conn.commit()
                return "not_broke", balance, 0
            cur = await self.conn.execute(
                "SELECT COUNT(*) AS n FROM escrow WHERE guild_id=? AND user_id=?", (guild_id, user_id))
            if (await cur.fetchone())["n"]:
                await self.conn.commit()
                return "in_game", balance, 0
            last_pay = None
            if config.RESTART_PAY_BLOCK > 0:  # 0 = paying someone never locks restarts
                cur = await self.conn.execute(
                    "SELECT MAX(ts) AS t FROM money_log WHERE guild_id=? AND user_id=? AND kind='pay' AND ts>=?",
                    (guild_id, user_id, time.time() - config.RESTART_PAY_BLOCK))
                last_pay = (await cur.fetchone())["t"]
            if last_pay:
                await self.conn.commit()
                return "paid", balance, last_pay + config.RESTART_PAY_BLOCK
            if grant:
                await self.conn.execute(
                    "UPDATE users SET balance = balance + ? WHERE guild_id=? AND user_id=?",
                    (config.RESTART_AMOUNT, guild_id, user_id))
                await self._money(guild_id, user_id, "restart", config.RESTART_AMOUNT)
                balance += config.RESTART_AMOUNT
            await self.conn.commit()
            return "ok", balance, 0

    async def record_game(self, guild_id: int, user_id: int, wagered: int, payout: int,
                          game: str = "other") -> None:
        win = 1 if payout > wagered else 0
        loss = 1 if payout < wagered else 0
        profit = max(0, payout - wagered)
        async with self.lock:
            await self._ensure(guild_id, user_id)
            await self.conn.execute(
                """UPDATE users SET games_played = games_played + 1, wins = wins + ?, losses = losses + ?,
                   total_wagered = total_wagered + ?, total_won = total_won + ?,
                   biggest_win = MAX(biggest_win, ?)
                   WHERE guild_id=? AND user_id=?""",
                (win, loss, wagered, payout, profit, guild_id, user_id),
            )
            await self._log_game(guild_id, user_id, game, wagered, payout)
            await self.conn.commit()

    async def leaderboard(self, guild_id: int, limit: int = 10) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(
            "SELECT user_id, balance FROM users WHERE guild_id=? ORDER BY balance DESC LIMIT ?",
            (guild_id, limit),
        )
        return await cur.fetchall()

    # ------------------------------------------------------------------- shop
    async def spend(self, guild_id: int, user_id: int, amount: int, kind: str = "shop") -> bool:
        """Atomically removes `amount` for a purchase if the user can afford it, and logs it."""
        _check_amount(amount)
        async with self.lock:
            await self._ensure(guild_id, user_id)
            cur = await self.conn.execute(
                "UPDATE users SET balance = balance - ? WHERE guild_id=? AND user_id=? AND balance >= ?",
                (amount, guild_id, user_id, amount),
            )
            if cur.rowcount != 1:
                await self.conn.commit()
                return False
            await self._money(guild_id, user_id, kind, -amount)
            await self.conn.commit()
            return True

    async def buy_chicken(self, guild_id: int, user_id: int, price: int) -> str:
        """Returns "ok", "owned" (already has a chicken) or "poor" (can't afford it)."""
        async with self.lock:
            await self._ensure(guild_id, user_id)
            cur = await self.conn.execute(
                "SELECT 1 FROM chickens WHERE guild_id=? AND user_id=?", (guild_id, user_id))
            if await cur.fetchone():
                await self.conn.commit()
                return "owned"
            cur = await self.conn.execute(
                "UPDATE users SET balance = balance - ? WHERE guild_id=? AND user_id=? AND balance >= ?",
                (price, guild_id, user_id, price),
            )
            if cur.rowcount != 1:
                await self.conn.commit()
                return "poor"
            await self.conn.execute(
                "INSERT INTO chickens (guild_id, user_id, wins, born) VALUES (?, ?, 0, ?)",
                (guild_id, user_id, time.time()),
            )
            await self._money(guild_id, user_id, "shop", -price)
            await self.conn.commit()
            return "ok"

    async def get_chicken(self, guild_id: int, user_id: int) -> aiosqlite.Row | None:
        cur = await self.conn.execute(
            "SELECT * FROM chickens WHERE guild_id=? AND user_id=?", (guild_id, user_id))
        return await cur.fetchone()

    async def chicken_won(self, guild_id: int, user_id: int) -> int:
        """Adds a win to the player's chicken. Returns its new win count."""
        async with self.lock:
            await self.conn.execute(
                "UPDATE chickens SET wins = wins + 1 WHERE guild_id=? AND user_id=?", (guild_id, user_id))
            await self.conn.commit()
            cur = await self.conn.execute(
                "SELECT wins FROM chickens WHERE guild_id=? AND user_id=?", (guild_id, user_id))
            row = await cur.fetchone()
            return row["wins"] if row else 0

    async def kill_chicken(self, guild_id: int, user_id: int) -> None:
        async with self.lock:
            await self.conn.execute("DELETE FROM chickens WHERE guild_id=? AND user_id=?", (guild_id, user_id))
            await self.conn.commit()

    async def add_shop_job(self, guild_id: int, kind: str, due: float, target_id: int | None,
                           buyer_id: int, cost: int) -> int:
        async with self.lock:
            cur = await self.conn.execute(
                "INSERT INTO shop_jobs (guild_id, kind, due, target_id, buyer_id, cost) VALUES (?, ?, ?, ?, ?, ?)",
                (guild_id, kind, due, target_id, buyer_id, cost),
            )
            await self.conn.commit()
            return cur.lastrowid

    async def update_shop_job(self, job_id: int, due: float) -> None:
        async with self.lock:
            await self.conn.execute("UPDATE shop_jobs SET due=? WHERE id=?", (due, job_id))
            await self.conn.commit()

    async def delete_shop_job(self, job_id: int) -> bool:
        """Returns False if the job was already gone (so it is only ever run once)."""
        async with self.lock:
            cur = await self.conn.execute("DELETE FROM shop_jobs WHERE id=?", (job_id,))
            await self.conn.commit()
            return cur.rowcount == 1

    async def shop_jobs(self) -> list[aiosqlite.Row]:
        cur = await self.conn.execute("SELECT * FROM shop_jobs ORDER BY due")
        return await cur.fetchall()

    # ----------------------------------------------------------------- escrow
    async def open_escrow(self, guild_id: int, user_id: int, amount: int, game: str) -> int:
        async with self.lock:
            cur = await self.conn.execute(
                "INSERT INTO escrow (guild_id, user_id, amount, game, created) VALUES (?, ?, ?, ?, ?)",
                (guild_id, user_id, amount, game, time.time()),
            )
            await self.conn.commit()
            return cur.lastrowid

    async def update_escrow(self, escrow_id: int, amount: int) -> None:
        async with self.lock:
            await self.conn.execute("UPDATE escrow SET amount=? WHERE id=?", (amount, escrow_id))
            await self.conn.commit()

    async def close_escrow(self, escrow_id: int) -> None:
        async with self.lock:
            await self.conn.execute("DELETE FROM escrow WHERE id=?", (escrow_id,))
            await self.conn.commit()

    async def settle_escrow(self, escrow_id: int, guild_id: int, user_id: int, payout: int) -> int:
        """Closes an escrow and pays out `payout` in one transaction. Returns the new balance."""
        async with self.lock:
            await self._ensure(guild_id, user_id)
            await self.conn.execute("DELETE FROM escrow WHERE id=?", (escrow_id,))
            if payout:
                await self.conn.execute(
                    "UPDATE users SET balance = balance + ? WHERE guild_id=? AND user_id=?",
                    (payout, guild_id, user_id),
                )
            await self.conn.commit()
            cur = await self.conn.execute(
                "SELECT balance FROM users WHERE guild_id=? AND user_id=?", (guild_id, user_id)
            )
            return (await cur.fetchone())["balance"]

    async def refund_all_escrow(self) -> list[aiosqlite.Row]:
        """Refunds bets from games that were interrupted (bot restart). Returns the refunded rows."""
        async with self.lock:
            cur = await self.conn.execute("SELECT * FROM escrow")
            rows = await cur.fetchall()
            for r in rows:
                await self._ensure(r["guild_id"], r["user_id"])
                await self.conn.execute(
                    "UPDATE users SET balance = balance + ? WHERE guild_id=? AND user_id=?",
                    (r["amount"], r["guild_id"], r["user_id"]),
                )
            await self.conn.execute("DELETE FROM escrow")
            await self.conn.commit()
            return rows

    # --------------------------------------------------------------- settings
    # ------------------------------------------------------------------- crux
    async def crux_until(self, guild_id: int, user_id: int) -> float | None:
        """When the player's crux ends, or None if they don't have one (an ended crux is cleared here)."""
        cur = await self.conn.execute("SELECT until FROM crux WHERE guild_id=? AND user_id=?", (guild_id, user_id))
        row = await cur.fetchone()
        if row is None:
            return None
        if row["until"] > time.time():
            return row["until"]
        async with self.lock:
            await self.conn.execute("DELETE FROM crux WHERE guild_id=? AND user_id=? AND until<=?",
                                    (guild_id, user_id, time.time()))
            await self.conn.commit()
        return None

    async def set_crux(self, guild_id: int, user_id: int, until: float) -> None:
        async with self.lock:
            await self.conn.execute(
                """INSERT INTO crux (guild_id, user_id, until, started) VALUES (?, ?, ?, ?)
                   ON CONFLICT (guild_id, user_id) DO UPDATE SET until=excluded.until""",
                (guild_id, user_id, until, time.time()))
            await self.conn.commit()

    async def remove_crux(self, guild_id: int, user_id: int) -> bool:
        """Ends a crux early. Returns whether there was one running."""
        async with self.lock:
            cur = await self.conn.execute("DELETE FROM crux WHERE guild_id=? AND user_id=? AND until>?",
                                          (guild_id, user_id, time.time()))
            await self.conn.execute("DELETE FROM crux WHERE guild_id=? AND user_id=?", (guild_id, user_id))
            await self.conn.commit()
            return cur.rowcount > 0

    async def cruxes(self, guild_id: int) -> dict[int, tuple[float, float]]:
        """Every running crux in the server: user id -> (started, until)."""
        cur = await self.conn.execute("SELECT user_id, started, until FROM crux WHERE guild_id=? AND until>?",
                                      (guild_id, time.time()))
        return {r["user_id"]: (r["started"], r["until"]) for r in await cur.fetchall()}

    # ---------------------------------------------------------------- streaks
    async def record_streak(self, guild_id: int, user_id: int, game: str, streak: int) -> None:
        """Keeps the player's longest winstreak in this game (only a new record is written)."""
        if streak <= 0:
            return
        async with self.lock:
            await self.conn.execute(
                """INSERT INTO streaks (guild_id, user_id, game, best, ts) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT (guild_id, user_id, game) DO UPDATE SET best=excluded.best, ts=excluded.ts
                   WHERE excluded.best > streaks.best""",
                (guild_id, user_id, game, streak, time.time()))
            await self.conn.commit()

    async def best_streaks(self, guild_id: int, user_id: int) -> dict[str, int]:
        """The player's longest winstreak per game."""
        cur = await self.conn.execute("SELECT game, best FROM streaks WHERE guild_id=? AND user_id=?",
                                      (guild_id, user_id))
        return {r["game"]: r["best"] for r in await cur.fetchall()}

    async def top_streak(self, guild_id: int, since: float = 0) -> dict | None:
        """The longest winstreak in the server reached since `since`: user_id, game, best, ts."""
        cur = await self.conn.execute(
            "SELECT user_id, game, best, ts FROM streaks WHERE guild_id=? AND ts>=? ORDER BY best DESC, ts LIMIT 1",
            (guild_id, since))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def get_setting(self, guild_id: int, key: str, default: Any = None) -> Any:
        cur = await self.conn.execute(
            "SELECT value FROM settings WHERE guild_id=? AND key=?", (guild_id, key)
        )
        row = await cur.fetchone()
        return json.loads(row["value"]) if row else default

    async def set_setting(self, guild_id: int, key: str, value: Any) -> None:
        async with self.lock:
            await self.conn.execute(
                "INSERT OR REPLACE INTO settings (guild_id, key, value) VALUES (?, ?, ?)",
                (guild_id, key, json.dumps(value)),
            )
            await self.conn.commit()

    # ---------------------------------------------------------------- lottery
    async def lottery_state(self, guild_id: int) -> aiosqlite.Row:
        async with self.lock:
            await self.conn.execute(
                "INSERT OR IGNORE INTO lottery (guild_id, pot, draw_at) VALUES (?, 0, ?)",
                (guild_id, time.time() + config.LOTTERY_INTERVAL),
            )
            await self.conn.commit()
            cur = await self.conn.execute("SELECT * FROM lottery WHERE guild_id=?", (guild_id,))
            return await cur.fetchone()

    async def buy_tickets(self, guild_id: int, user_id: int, count: int, price: int, channel_id: int) -> bool:
        cost = count * price
        await self.lottery_state(guild_id)
        async with self.lock:
            await self._ensure(guild_id, user_id)
            cur = await self.conn.execute(
                "UPDATE users SET balance = balance - ? WHERE guild_id=? AND user_id=? AND balance >= ?",
                (cost, guild_id, user_id, cost),
            )
            if cur.rowcount != 1:
                await self.conn.rollback()
                return False
            await self.conn.execute(
                """INSERT INTO lottery_tickets (guild_id, user_id, tickets) VALUES (?, ?, ?)
                   ON CONFLICT(guild_id, user_id) DO UPDATE SET tickets = tickets + excluded.tickets""",
                (guild_id, user_id, count),
            )
            await self.conn.execute(
                "UPDATE lottery SET pot = pot + ?, channel_id=? WHERE guild_id=?",
                (cost, channel_id, guild_id),
            )
            await self._log_game(guild_id, user_id, "lottery", cost, 0)
            await self.conn.commit()
            return True

    async def lottery_tickets(self, guild_id: int) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(
            "SELECT user_id, tickets FROM lottery_tickets WHERE guild_id=? AND tickets > 0", (guild_id,)
        )
        return await cur.fetchall()

    async def due_lotteries(self) -> list[aiosqlite.Row]:
        cur = await self.conn.execute("SELECT * FROM lottery WHERE draw_at <= ?", (time.time(),))
        return await cur.fetchall()

    async def finish_lottery(self, guild_id: int, winner_id: int | None, pot: int, bonus: int = 0) -> None:
        """Pays the winner (if any) the ticket pot plus the bank's bonus, clears tickets and schedules the next
        draw. The bonus comes from the house, so only `pot` leaves the lottery table."""
        async with self.lock:
            if winner_id is not None:
                await self._ensure(guild_id, winner_id)
                await self.conn.execute(
                    "UPDATE users SET balance = balance + ? WHERE guild_id=? AND user_id=?",
                    (pot + bonus, guild_id, winner_id),
                )
                await self.conn.execute(
                    "UPDATE lottery SET pot = pot - ? WHERE guild_id=?", (pot, guild_id)
                )
                await self._log_game(guild_id, winner_id, "lottery", 0, pot + bonus)
            await self.conn.execute("DELETE FROM lottery_tickets WHERE guild_id=?", (guild_id,))
            await self.conn.execute(
                "UPDATE lottery SET draw_at=? WHERE guild_id=?",
                (time.time() + config.LOTTERY_INTERVAL, guild_id),
            )
            await self.conn.commit()

    # ------------------------------------------------------------------ stats
    async def balance_history(self, guild_id: int, user_id: int, since: float) -> tuple[int | None, list]:
        """The balance just before `since` (None if there's no earlier record) and every logged balance from
        `since` on, oldest first, as (ts, balance, delta, source) rows."""
        cur = await self.conn.execute(
            """SELECT balance FROM balance_log WHERE guild_id=? AND user_id=? AND ts < ?
               ORDER BY ts DESC, id DESC LIMIT 1""", (guild_id, user_id, since))
        before = await cur.fetchone()
        cur = await self.conn.execute(
            """SELECT ts, balance, delta, source FROM balance_log WHERE guild_id=? AND user_id=? AND ts >= ?
               ORDER BY ts, id""", (guild_id, user_id, since))
        return (before["balance"] if before else None), await cur.fetchall()

    async def circulation_history(self, guild_id: int, since: float = 0, buckets: int = 400,
                                  until: float | None = None) -> list:
        """Total credits held by all players over time, as (ts, total) points, oldest first. Built from
        balance_log; players who have no logged balance at all (untouched since before it existed) are counted
        at their current balance the whole time. Thinned to about `buckets` time slots, keeping each slot's
        lowest and highest total so peaks and dips survive. With `until`, it stops there, and the line runs on to it."""
        q = self.conn.execute
        cur = await q("""SELECT user_id, balance FROM (
                             SELECT user_id, balance,
                                    ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY ts DESC, id DESC) AS rn
                             FROM balance_log WHERE guild_id=? AND ts < ?) WHERE rn = 1""", (guild_id, since))
        held = {r["user_id"]: r["balance"] for r in await cur.fetchall()}
        cur = await q("""SELECT COALESCE(SUM(balance), 0) AS t FROM users u WHERE guild_id=? AND NOT EXISTS
                             (SELECT 1 FROM balance_log b WHERE b.guild_id=u.guild_id AND b.user_id=u.user_id)""",
                      (guild_id,))
        unlogged = (await cur.fetchone())["t"]
        cur = await q("SELECT ts, user_id, balance FROM balance_log WHERE guild_id=? AND ts >= ? AND ts < ? "
                      "ORDER BY ts, id", (guild_id, since, until if until is not None else float("inf")))
        rows = await cur.fetchall()
        total = sum(held.values()) + unlogged
        raw = [(since, total)] if since and held else []
        for r in rows:
            total += r["balance"] - held.get(r["user_id"], 0)
            held[r["user_id"]] = r["balance"]
            raw.append((r["ts"], total))
        if until is not None and raw:
            raw.append((until, total))
        if len(raw) <= buckets * 2:
            return raw
        t0, t1 = raw[0][0], raw[-1][0]
        width = (t1 - t0) / buckets or 1
        out, slot = [], []
        for pt in raw + [None]:
            if slot and (pt is None or int((pt[0] - t0) / width) != int((slot[0][0] - t0) / width)):
                lo, hi = min(slot, key=lambda x: x[1]), max(slot, key=lambda x: x[1])
                keep = sorted({lo, hi, slot[-1]}, key=lambda x: x[0])
                out += keep
                slot = []
            if pt is not None:
                slot.append(pt)
        return out

    async def player_stats(self, guild_id: int, user_id: int, since: float = 0) -> dict:
        """One player's games and other credit movements since `since` (0 = all time)."""
        args = (guild_id, user_id, since)
        cur = await self.conn.execute(
            """SELECT COALESCE(SUM(CASE WHEN wagered>0 THEN 1 ELSE 0 END),0) AS games,
                      COALESCE(SUM(CASE WHEN wagered>0 AND payout>wagered THEN 1 ELSE 0 END),0) AS wins,
                      COALESCE(SUM(wagered),0) AS wagered, COALESCE(SUM(payout),0) AS paid
               FROM game_log WHERE guild_id=? AND user_id=? AND ts>=?""", args)
        totals = dict(await cur.fetchone())
        cur = await self.conn.execute(
            """SELECT game, SUM(CASE WHEN wagered>0 THEN 1 ELSE 0 END) AS plays, SUM(wagered) AS wagered,
                      SUM(payout) - SUM(wagered) AS net
               FROM game_log WHERE guild_id=? AND user_id=? AND ts>=? GROUP BY game""", args)
        per_game = [dict(r) for r in await cur.fetchall()]
        cur = await self.conn.execute(
            """SELECT game, wagered, payout, payout - wagered AS profit, ts FROM game_log
               WHERE guild_id=? AND user_id=? AND ts>=? AND payout > wagered ORDER BY profit DESC LIMIT 1""", args)
        best = await cur.fetchone()
        cur = await self.conn.execute(
            """SELECT game, wagered, payout, wagered - payout AS loss, ts FROM game_log
               WHERE guild_id=? AND user_id=? AND ts>=? AND wagered > payout ORDER BY loss DESC LIMIT 1""", args)
        worst = await cur.fetchone()
        cur = await self.conn.execute(
            """SELECT kind, COUNT(*) AS n,
                      COALESCE(SUM(CASE WHEN kind='pay' THEN -amount ELSE amount END), 0) AS amount
               FROM money_log WHERE guild_id=? AND user_id=? AND ts>=? GROUP BY kind""", args)
        money = {r["kind"]: dict(r) for r in await cur.fetchall()}
        # Payments are logged under the sender; this player's incoming ones are found by recipient.
        cur = await self.conn.execute(
            """SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS amount FROM money_log
               WHERE guild_id=? AND other_id=? AND kind='pay' AND ts>=?""", args)
        received = dict(await cur.fetchone())
        if received["n"]:
            money["received"] = received
        return {**totals, "per_game": per_game, "best": dict(best) if best else None,
                "worst": dict(worst) if worst else None, "money": money}

    async def server_stats(self, guild_id: int, since: float = 0, days: int = 14) -> dict:
        """Aggregates game_log / money_log for the server. `since` is a unix timestamp (0 = all time).
        `daily` always covers the last `days` days, whatever the period, for the per-day chart."""
        q = self.conn.execute
        args = (guild_id, since)
        played = "guild_id=? AND ts>=? AND wagered>0"  # lottery jackpots are logged as 0-wager payouts

        async def one(sql, params=args):
            return await (await q(sql, params)).fetchone()

        async def many(sql, params=args):
            return await (await q(sql, params)).fetchall()

        totals = await one(f"""SELECT COUNT(*) AS games, COALESCE(SUM(wagered),0) AS wagered,
                                  COUNT(DISTINCT user_id) AS players,
                                  COALESCE(SUM(CASE WHEN payout > wagered THEN 1 ELSE 0 END),0) AS wins
                               FROM game_log WHERE {played}""")
        paid = await one("SELECT COALESCE(SUM(payout),0) AS paid FROM game_log WHERE guild_id=? AND ts>=?")
        games = await many("""SELECT game, SUM(CASE WHEN wagered>0 THEN 1 ELSE 0 END) AS plays,
                                     SUM(wagered) AS wagered, SUM(payout) AS paid
                              FROM game_log WHERE guild_id=? AND ts>=?
                              GROUP BY game ORDER BY SUM(wagered) DESC""")
        biggest_win = await one("""SELECT user_id, game, wagered, payout, payout - wagered AS profit FROM game_log
                                   WHERE guild_id=? AND ts>=? AND payout > wagered
                                   ORDER BY payout - wagered DESC LIMIT 1""")
        biggest_loss = await one("""SELECT user_id, game, wagered, payout, wagered - payout AS loss FROM game_log
                                    WHERE guild_id=? AND ts>=? AND wagered > payout
                                    ORDER BY wagered - payout DESC LIMIT 1""")
        best_mult = await one(f"""SELECT user_id, game, wagered, payout, CAST(payout AS REAL) / wagered AS mult
                                  FROM game_log WHERE {played} AND game != 'poker' AND wagered >= ?
                                  ORDER BY mult DESC, payout DESC LIMIT 1""", (guild_id, since, config.MIN_BET))
        per_user = await many("""SELECT user_id, SUM(CASE WHEN wagered>0 THEN 1 ELSE 0 END) AS plays,
                                        SUM(wagered) AS wagered, SUM(payout) - SUM(wagered) AS net
                                 FROM game_log WHERE guild_id=? AND ts>=? GROUP BY user_id""")
        hours = await many(f"""SELECT CAST(strftime('%H', ts, 'unixepoch', 'localtime') AS INTEGER) AS hour,
                                      COUNT(*) AS n FROM game_log WHERE {played} GROUP BY hour""")
        start_day = time.time() - (days - 1) * 86400
        daily = await many(f"""SELECT date(ts, 'unixepoch', 'localtime') AS day, SUM(wagered) AS wagered,
                                      COUNT(*) AS games
                               FROM game_log WHERE {played} GROUP BY day""",
                           (guild_id, start_day - 86400))
        money = await many("""SELECT kind, COUNT(*) AS n, SUM(amount) AS amount,
                                     COALESCE(SUM(CASE WHEN amount > 0 THEN amount END), 0) AS added,
                                     COALESCE(-SUM(CASE WHEN amount < 0 THEN amount END), 0) AS removed
                              FROM money_log WHERE guild_id=? AND ts>=? GROUP BY kind""")
        economy = await one("""SELECT COALESCE(SUM(balance),0) AS circulating, COUNT(*) AS accounts
                               FROM users WHERE guild_id=?""", (guild_id,))
        richest = await one("SELECT user_id, balance FROM users WHERE guild_id=? ORDER BY balance DESC LIMIT 1",
                            (guild_id,))
        in_play = await one("SELECT COALESCE(SUM(amount),0) AS a FROM escrow WHERE guild_id=?", (guild_id,))
        pot = await one("SELECT COALESCE(SUM(pot),0) AS p FROM lottery WHERE guild_id=?", (guild_id,))
        first = await one("SELECT MIN(ts) AS t FROM game_log WHERE guild_id=?", (guild_id,))
        # Per game, for judging its odds: only plays since its odds last changed (/config odds-reset).
        resets = await self.get_setting(guild_id, "odds_reset", {})
        returns = []
        for g in games:
            start = max(since, resets.get(g["game"], 0))
            if start == since:
                returns.append(dict(g))
                continue
            r = await one("""SELECT COALESCE(SUM(CASE WHEN wagered>0 THEN 1 ELSE 0 END),0) AS plays,
                                    COALESCE(SUM(wagered),0) AS wagered, COALESCE(SUM(payout),0) AS paid
                             FROM game_log WHERE guild_id=? AND game=? AND ts>=?""", (guild_id, g["game"], start))
            returns.append({"game": g["game"], **dict(r)})

        users = [dict(r) for r in per_user]
        by = lambda key, rev=True: max(users, key=key) if rev else min(users, key=key)  # noqa: E731
        return {
            "games": totals["games"], "wagered": totals["wagered"], "paid": paid["paid"],
            "players": totals["players"], "wins": totals["wins"],
            "per_game": [dict(r) for r in games],
            "returns": returns,
            "biggest_win": dict(biggest_win) if biggest_win else None,
            "biggest_loss": dict(biggest_loss) if biggest_loss else None,
            "best_mult": dict(best_mult) if best_mult else None,
            "high_roller": by(lambda u: u["wagered"]) if users else None,
            "most_active": by(lambda u: u["plays"]) if users else None,
            "luckiest": by(lambda u: u["net"]) if users else None,
            "unluckiest": by(lambda u: u["net"], rev=False) if users else None,
            "hours": {r["hour"]: r["n"] for r in hours},
            "daily": {r["day"]: (r["wagered"], r["games"]) for r in daily},
            "money": {r["kind"]: dict(r) for r in money},
            "circulating": economy["circulating"] + in_play["a"], "accounts": economy["accounts"],
            "richest": dict(richest) if richest else None,
            "lottery_pot": pot["p"],
            "tracking_since": first["t"],
        }

"""Runs the admin panel without Discord, on a test database, for trying it out and working on the page.

    python -m admin_panel.devserver [--port 8781] [--db admin_dev.db] [--seed] [--live]

--seed fills an empty test database with made-up players and two months of games. --live keeps playing made-up
games every few seconds, for the Live views. The password is "dev". Never point this at the bot's gamble.db.
"""
import argparse
import asyncio
import datetime
import logging
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from admin_panel.server import AdminPanel  # noqa: E402
from database import Database  # noqa: E402

GUILD = 1
OTHER_GUILD = 2
NAMES = ["Alice", "Bob", "Charlie", "Dana", "Eve", "Finn", "Gwen", "Hugo", "Iris", "Jules", "Kai", "Lena", "Milo",
         "Nora", "Otto", "Pia", "Quinn", "Rosa", "Sam", "Tess", "Umar", "Vera", "Wes", "Xena", "Yuri", "Zoe",
         "Abel", "Bea", "Cyril", "Dirk", "Elif", "Floor", "Gijs", "Hanna", "Ivo", "Joep", "Kees", "Lotte"]
GAMES = {"slots": 0.97, "blackjack": 0.99, "roulette": 0.97, "coinflip": 0.98, "mines": 0.97, "crash": 0.97,
         "spinfrenzy": 0.97, "bangerbash": 0.99, "poker": 1.0, "tower": 0.97, "dice": 0.98}


class FakeDirectory:
    def guilds(self) -> dict[int, dict]:
        return {GUILD: {"name": "Jax Test Casino", "icon": None}, OTHER_GUILD: {"name": "Second Server", "icon": None}}

    def member(self, guild_id: int, user_id: int) -> dict | None:
        i = user_id - 1000
        if 0 <= i < len(NAMES):
            return {"name": NAMES[i], "username": NAMES[i].lower(), "avatar": None, "in_server": i % 9 != 8}
        return None

    async def crux_started(self, guild_id: int, user_id: int, until: float) -> None:
        pass   # no casino here to close games in


def play(r: random.Random, game: str, bet: int) -> int:
    """A made-up payout with roughly the game's return."""
    rtp = GAMES[game]
    roll = r.random()
    if roll < 0.02:                      # 2% pay 10x, 43% pay around m, the rest nothing: averages out at rtp
        return bet * 10
    if roll < 0.45:
        m = (rtp - 0.2) / 0.43
        return int(bet * m * (0.8 + 0.4 * r.random()))
    return 0


async def seed(db: Database) -> None:
    cur = await db.conn.execute("SELECT COUNT(*) AS n FROM users")
    if (await cur.fetchone())["n"]:
        print("The test database already has players; not seeding again.")
        return
    r = random.Random(7)
    now = time.time()
    start = now - 60 * 86400
    events = []      # (ts, guild, user, kind, game_or_money_kind, wagered, payout_or_amount)
    for guild, count in ((GUILD, len(NAMES)), (OTHER_GUILD, 6)):
        for i in range(count):
            uid = 1000 + i
            joined = start + r.random() * 40 * 86400
            activity = r.choice([0.2, 0.5, 1, 2, 4, 8])      # games per hour-ish
            t = joined
            fav = r.sample(list(GAMES), 3)
            while t < now:
                t += r.expovariate(activity / 3600) * (3 if r.random() < 0.3 else 1)
                if t >= now:
                    break
                if r.random() < 0.08:
                    events.append((t, guild, uid, "money", "claim", None, 400))
                    continue
                if r.random() < 0.01:
                    events.append((t, guild, uid, "money", "shop", None, -r.choice([1000, 2500, 5000])))
                    continue
                game = r.choice(fav) if r.random() < 0.8 else r.choice(list(GAMES))
                events.append((t, guild, uid, "game", game, None, None))
    events.sort()
    balances: dict[tuple, int] = {}
    stats: dict[tuple, dict] = {}
    game_rows, money_rows, bal_rows = [], [], []
    for ts, guild, uid, kind, what, _, amount in events:
        key = (guild, uid)
        if key not in balances:
            balances[key] = 20000
            stats[key] = {"games": 0, "wins": 0, "losses": 0, "wagered": 0, "won": 0, "best": 0}
            bal_rows.append((guild, uid, ts - 1, 20000, 20000))
        bal = balances[key]
        if kind == "money":
            if amount < 0 and bal < -amount:
                continue
            bal += amount
            money_rows.append((guild, uid, what, amount, ts))
        else:
            if bal < 10:
                bal += 500
                money_rows.append((guild, uid, "restart", 500, ts - 0.5))
            bet = max(10, min(bal, 25000, int(bal * r.choice([0.01, 0.02, 0.05, 0.1, 0.25]))))
            payout = play(r, what, bet)
            bal += payout - bet
            s = stats[key]
            s["games"] += 1
            s["wins"] += payout > bet
            s["losses"] += payout < bet
            s["wagered"] += bet
            s["won"] += payout
            s["best"] = max(s["best"], payout - bet)
            game_rows.append((guild, uid, what, bet, payout, ts))
        bal_rows.append((guild, uid, ts, bal, bal - balances[key]))
        balances[key] = bal
    c = db.conn
    await c.executemany("INSERT INTO game_log (guild_id, user_id, game, wagered, payout, ts) VALUES (?,?,?,?,?,?)",
                        game_rows)
    await c.executemany("INSERT INTO money_log (guild_id, user_id, kind, amount, ts) VALUES (?,?,?,?,?)",
                        money_rows)
    for (guild, uid), bal in balances.items():
        s = stats[(guild, uid)]
        await c.execute("""INSERT INTO users (guild_id, user_id, balance, games_played, wins, losses, total_wagered,
                                              total_won, biggest_win) VALUES (?,?,?,?,?,?,?,?,?)""",
                        (guild, uid, bal, s["games"], s["wins"], s["losses"], s["wagered"], s["won"], s["best"]))
    await c.execute("DELETE FROM balance_log")
    await c.executemany("INSERT INTO balance_log (guild_id, user_id, ts, balance, delta) VALUES (?,?,?,?,?)",
                        [(g, u, ts, b, d) for g, u, ts, b, d in bal_rows])
    await c.commit()
    print(f"Seeded {len(balances)} players, {len(game_rows)} games, {len(money_rows)} other movements "
          f"from {datetime.date.fromtimestamp(start)} on.")


async def live(db: Database) -> None:
    r = random.Random()
    while True:
        await asyncio.sleep(r.uniform(1, 4))
        uid = 1000 + r.randrange(12)
        bal = await db.get_balance(GUILD, uid)
        if bal < 10:
            continue
        game = r.choice(list(GAMES))
        bet = max(10, min(bal, int(bal * r.choice([0.01, 0.02, 0.05]))))
        payout = play(r, game, bet)
        async with db.lock:
            await db.conn.execute("UPDATE users SET balance = balance + ? WHERE guild_id=? AND user_id=?",
                                  (payout - bet, GUILD, uid))
            await db.conn.commit()
        await db.record_game(GUILD, uid, bet, payout, game)


async def main(port: int, db_path: str, do_seed: bool, do_live: bool) -> None:
    if os.path.basename(db_path) == "gamble.db":
        sys.exit("Use a test database, not the bot's gamble.db.")
    db = Database(db_path)
    await db.connect()
    if do_seed:
        await seed(db)
    panel = AdminPanel(db, "dev", FakeDirectory(), context={"db": db})
    await panel.start("127.0.0.1", port)
    print(f"Admin panel dev server: http://127.0.0.1:{port}  (password: dev, database: {db_path})")
    tasks = [asyncio.create_task(live(db))] if do_live else []
    try:
        await asyncio.Event().wait()
    finally:
        for t in tasks:
            t.cancel()
        await panel.stop()
        await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8781)
    parser.add_argument("--db", default="admin_dev.db")
    parser.add_argument("--seed", action="store_true", help="fill an empty test database with made-up data")
    parser.add_argument("--live", action="store_true", help="keep playing made-up games for the Live views")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    try:
        asyncio.run(main(args.port, args.db, args.seed, args.live))
    except KeyboardInterrupt:
        pass

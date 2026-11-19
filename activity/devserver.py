"""Runs the Activity (game menu, Spin Frenzy, Banger Bash, Poker, Blackjack, Reef Rush, Raccoon Heist) locally, without Discord, for testing in a browser.

    python -m activity.devserver [--port 8766] [--db activity_dev.db]

Open http://localhost:8766/?user=1&name=Alice and, in another tab, ?user=2&name=Bob. Logins need no Discord
account, so this uses its own database (activity_dev.db by default, never the bot's gamble.db unless you pass
it) and only listens on 127.0.0.1. Don't expose it to the internet.
"""
import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# games still being built (off in the bot until switched on in .env) are always on in the dev server
os.environ.setdefault("REEF_RUSH_ENABLED", "1")
os.environ.setdefault("RACCOON_HEIST_ENABLED", "1")

import config  # noqa: E402
from activity.server import ActivityServer, Settings  # noqa: E402
from database import Database  # noqa: E402

DEV_GUILD = 1


def force_results(spots: list[str]) -> None:
    """Testing aid: the wheel lands on these spots in turn (e.g. --force coin,grid,drop,frenzy)."""
    from activity import engine
    from utils.rng import rng
    order = iter(spots * 10_000)

    def forced() -> int:
        spot = next(order)
        return rng.choice([i for i, s in enumerate(engine.WHEEL) if s == spot])

    engine.spin_wheel = forced


def force_banger(items: list[str]) -> None:
    """Testing aid: Banger Bash spins get these in turn (e.g. --banger flame,mystery,bonus,golden).
    Random features: flame, mystery, mega, hot, charcoal, sure, respin. A bonus: bonus (3 grills), bonus4, bonus5,
    or a feature name (potato, picnic, grillspins, twinlinks, golden) for a bonus that awards it. Bonus buys are
    never forced."""
    from activity import banger
    from utils import banger_engine as E
    order = iter(items * 10_000)
    real_round = E.Round

    class Forced(real_round):
        def __init__(self, r, mode="spin"):
            if mode != "spin":
                super().__init__(r, mode)
                return
            self.mode = mode
            what = next(order)
            tier = E.TIERS.index(what) if what in E.TIERS else None
            if what in E.FEATURES:
                self.spin = E._feature_spin(r, what)
            elif what == "respin":
                self.spin = E.base_spin(r)
                while self.spin.feature != "respin":
                    self.spin = E.base_spin(r)
            else:
                n = {"bonus4": 4, "bonus5": 5}.get(what, 3)
                grid = E.random_grid(r, grill_odds={n: 1.0})
                self.spin = E.Spin(grid, grid, E.evaluate(grid))
            grills = len(self.spin.grills)
            self.bonus = E.Bonus(r, grills) if grills >= 3 else None
            if self.bonus and tier is not None:
                self.bonus.awarded = self.bonus.tier = tier

    banger.E.Round = Forced


def force_reef(items: list[str]) -> None:
    """Testing aid: Reef Rush spins get these in turn (e.g. --reef kraken,whirlpool,bubble). Each item is
    win, big (10x+), surge (any), whirlpool, riptide, undertow, shift, kraken or bubble; spins are drawn
    until one matches. Bonus buys are never forced."""
    from activity import reef
    from utils import reef_engine as E
    order = iter(items * 10_000)
    real_round = E.Round
    tests = {"win": lambda r: r.x > 0, "big": lambda r: r.x >= 10, "surge": lambda r: r.surge is not None,
             "kraken": lambda r: r.kraken_landed, "bubble": lambda r: r.bubble,
             **{k: (lambda k: lambda r: r.surge == k)(k) for k in E.SURGES}}

    def forced(r, mode="spin"):
        if mode != "spin":
            return real_round(r, mode)
        test = tests.get(next(order), lambda _: True)
        for _ in range(200_000):
            rnd = real_round(r)
            if test(rnd):
                return rnd
        return rnd

    reef.E.Round = forced


def force_heist(items: list[str]) -> None:
    """Testing aid: Raccoon Heist paid spins get these in turn (e.g. --heist boss,bonus,grab). Each item is
    win, big (10x+), raid3 (3+ raids), scuffle, teamup, wall, paint, hook, boss, grab or bonus; spins are drawn until
    one matches. Bonus buys are never forced."""
    from activity import heist
    from utils import heist_engine as E
    order = iter(items * 10_000)
    real_round = E.Round

    def has(kind):
        return lambda r: any(s["t"] == kind for s in r.steps)

    def grabbed(key):
        return lambda r: any(key in g for s in r.steps for g in s.get("grabs", []))
    tests = {"win": lambda r: r.raw_x > 0, "big": lambda r: r.raw_x >= 10,
             "raid3": lambda r: sum(s["t"] == "raid" for s in r.steps) >= 3, "scuffle": has("scuffle"),
             "teamup": has("teamup"), "wall": grabbed("wall"), "paint": grabbed("paint"), "hook": grabbed("hook"),
             "boss": lambda r: r.bosses > 0, "grab": lambda r: r.grabs > 0, "bonus": lambda r: r.bonus_kind}

    def forced(r, mode="spin", play_bonus=True):
        if mode != "spin":
            return real_round(r, mode, play_bonus)
        test = tests.get(next(order), lambda _: True)
        for _ in range(400_000):
            rnd = real_round(r, mode, play_bonus)
            if test(rnd):
                return rnd
        return rnd

    heist.E.Round = forced


def force_blackjack(ranks: list[str]) -> None:
    """Testing aid: live blackjack hands out these ranks first, in dealing order (players' first cards, the dealer's
    face-up card, players' second cards, the hole card, then every hit), e.g. --blackjack 8,8,6,8,K,3,A,K"""
    from activity import blackjack
    from utils.cards import SUITS, Card
    from utils.rng import rng
    names = {"J": 11, "Q": 12, "K": 13, "A": 14}
    order = iter([names.get(r.upper(), None) or int(r) for r in ranks])
    real_draw, real_upcard = blackjack.Table.draw, blackjack.draw_upcard

    def forced(fallback):
        def take(*args):
            rank = next(order, None)
            return Card(rank, rng.choice(SUITS)) if rank else fallback(*args)
        return take

    blackjack.Table.draw = forced(real_draw)
    blackjack.draw_upcard = forced(real_upcard)


async def main(port: int, db_path: str, bet_seconds: int) -> None:
    db = Database(db_path)
    await db.connect()
    await db.refund_all_escrow()

    async def resolve_member(guild_id: int, user_id: int) -> dict:
        return {"name": f"Tester {user_id}", "avatar_url": None}

    settings = Settings(rtp=config.SPIN_FRENZY_RTP, bonus_weight=config.SPIN_FRENZY_BONUS_WEIGHT,
                        bonus_rtp=config.SPIN_FRENZY_BONUS_PAYBACK, bet_seconds=bet_seconds,
                        max_win_mult=config.SPIN_FRENZY_MAX_WIN, min_bet=config.MIN_BET, max_bet=config.MAX_BET)
    server = ActivityServer(db, settings, resolve_member, "", "", lambda: None, dev_guild_id=DEV_GUILD)
    await server.start("127.0.0.1", port)
    print(f"Spin Frenzy dev server: http://localhost:{port}/?user=1&name=Alice  (database: {db_path})")
    try:
        await asyncio.Event().wait()
    finally:
        await server.stop()
        await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--db", default="activity_dev.db")
    parser.add_argument("--bet-seconds", type=int, default=config.SPIN_FRENZY_BET_SECONDS)
    parser.add_argument("--force", default="", help="comma-separated spots the wheel lands on in turn (testing)")
    parser.add_argument("--banger", default="", help="comma-separated Banger Bash features/bonuses for spins in turn "
                                                       "(testing): flame, mystery, mega, hot, charcoal, sure, respin, "
                                                       "bonus, bonus4, bonus5, potato, picnic, grillspins, twinlinks, golden")
    parser.add_argument("--reef", default="", help="comma-separated Reef Rush outcomes for spins in turn (testing): "
                                                     "win, big, surge, whirlpool, riptide, undertow, shift, kraken, bubble")
    parser.add_argument("--heist", default="", help="comma-separated Raccoon Heist outcomes for paid spins in turn "
                                                      "(testing): win, big, raid3, scuffle, teamup, wall, paint, "
                                                      "hook, boss, grab, bonus")
    parser.add_argument("--blackjack", default="", help="comma-separated card ranks live blackjack deals first, in "
                                                         "dealing order (testing), e.g. 8,8,6,8,K,3")
    args = parser.parse_args()
    if args.blackjack:
        force_blackjack([s.strip() for s in args.blackjack.split(",") if s.strip()])
    if args.force:
        force_results([s.strip() for s in args.force.split(",") if s.strip()])
    if args.reef:
        force_reef([s.strip() for s in args.reef.split(",") if s.strip()])
    if args.heist:
        force_heist([s.strip() for s in args.heist.split(",") if s.strip()])
    if args.banger:
        force_banger([s.strip() for s in args.banger.split(",") if s.strip()])
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    try:
        asyncio.run(main(args.port, args.db, args.bet_seconds))
    except KeyboardInterrupt:
        pass

"""Loads bot configuration from the .env file."""
import os

from dotenv import load_dotenv

load_dotenv()


def _int_list(name: str) -> set[int]:
    raw = os.getenv(name, "")
    return {int(x) for x in raw.replace(" ", "").split(",") if x.strip().isdigit()}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw.lstrip("-").isdigit() else default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip())
    except ValueError:
        return default


TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GUILD_ID = _int("GUILD_ID", 0) or None

ADMIN_USER_IDS = _int_list("ADMIN_USER_IDS")
ADMIN_ROLE_IDS = _int_list("ADMIN_ROLE_IDS")
GAMBLING_CHANNEL_IDS = _int_list("GAMBLING_CHANNEL_IDS")

STARTING_BALANCE = _int("STARTING_BALANCE", 20000)
DAILY_AMOUNT = _int("DAILY_AMOUNT", 400)
DAILY_COOLDOWN = 24 * 60 * 60
MIN_BET = _int("MIN_BET", 10)

# Restart: a player whose balance drops below MIN_BET can press a button RESTART_PRESSES times
# to get RESTART_AMOUNT credits. Not available for RESTART_PAY_BLOCK_SECONDS after sending credits
# with /pay, so nobody can farm restarts by giving their credits away (0 = restarts are never locked).
RESTART_AMOUNT = _int("RESTART_AMOUNT", 500)
RESTART_PRESSES = max(1, _int("RESTART_PRESSES", 1))
RESTART_PAY_BLOCK = max(0, _int("RESTART_PAY_BLOCK_SECONDS", 3600))  # in seconds
MAX_BET = _int("MAX_BET", 0)  # 0 = unlimited

HOUSE_EDGE = min(max(_float("HOUSE_EDGE", 0.02), 0.0), 0.5)
POKER_RAKE = min(max(_float("POKER_RAKE", 0.0), 0.0), 0.5)
# Average share of bets the Cosmic Lab slot pays back (0.99 = 99%). Separate from HOUSE_EDGE.
SLOTS_RTP = min(max(_float("SLOTS_RTP", 0.99), 0.5), 1.2)
# Average share of bets the Banger Bash slot pays back (chat command and Activity alike).
BANGER_RTP = min(max(_float("BANGER_RTP", 0.99), 0.5), 1.2)
# /bangerbash opens Banger Bash in the Activity. Set to 1 to make it play in the text channel instead (the older
# chat version, with a bet option), as it did before.
BANGERBASH_IN_CHAT = os.getenv("BANGERBASH_IN_CHAT", "0").strip().lower() in ("1", "true", "yes", "on")
# Reef Rush (the 7x7 cluster slot in the Activity): off until REEF_RUSH_ENABLED=1, so it stays out of the game menu
# and /casino until it's switched on. REEF_RTP is its average payback (0.99 = 99%).
REEF_RUSH_ENABLED = os.getenv("REEF_RUSH_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")
REEF_RTP = min(max(_float("REEF_RTP", 0.99), 0.5), 1.2)
# Raccoon Heist (the 8x8 vault raid slot in the Activity): off until RACCOON_HEIST_ENABLED=1, like Reef Rush.
# HEIST_RTP is its average payback (0.99 = 99%), for paid spins and every bonus buy alike. HEIST_BUYS=0 hides the
# bonus buys.
RACCOON_HEIST_ENABLED = os.getenv("RACCOON_HEIST_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")
HEIST_RTP = min(max(_float("HEIST_RTP", 0.99), 0.5), 1.2)
HEIST_BUYS = os.getenv("HEIST_BUYS", "1").strip().lower() not in ("0", "false", "no", "off")

LOTTERY_TICKET_PRICE = _int("LOTTERY_TICKET_PRICE", 100)
LOTTERY_INTERVAL = _int("LOTTERY_INTERVAL_HOURS", 24) * 60 * 60

# Cockfight: your chicken's win chance starts at COCKFIGHT_BASE_CHANCE and grows by COCKFIGHT_CHANCE_STEP
# with every fight it wins, up to COCKFIGHT_MAX_CHANCE. A win pays 2x minus the house edge; a loss kills it.
COCKFIGHT_BASE_CHANCE = min(max(_float("COCKFIGHT_BASE_CHANCE", 0.5), 0.01), 0.99)
COCKFIGHT_CHANCE_STEP = min(max(_float("COCKFIGHT_CHANCE_STEP", 0.01), 0.0), 0.5)
COCKFIGHT_MAX_CHANCE = min(max(_float("COCKFIGHT_MAX_CHANCE", 0.7), COCKFIGHT_BASE_CHANCE), 0.99)

# Shop: default prices. Mute and deafen share the "voice" prices. Admins can override them per server with
# /config shop-price; a price of 0 takes that option off sale.
SHOP_PRICES = {
    "chicken": _int("SHOP_CHICKEN_PRICE", 1000),
    "disconnect": _int("SHOP_DISCONNECT_PRICE", 5000),
    "voice1": _int("SHOP_VOICE_1MIN_PRICE", 1000),
    "voice5": _int("SHOP_VOICE_5MIN_PRICE", 4000),
    "timeout5": _int("SHOP_TIMEOUT_5MIN_PRICE", 8000),
    "timeout15": _int("SHOP_TIMEOUT_15MIN_PRICE", 20000),
    "timeout30": _int("SHOP_TIMEOUT_30MIN_PRICE", 35000),
    "nickname": _int("SHOP_NICKNAME_PRICE", 10000),
    "randomkick": _int("SHOP_RANDOMKICK_PRICE", 2500),
}
# A random kick fires at a random moment between 0 and this many seconds after it is bought.
RANDOM_KICK_MAX_DELAY = max(0, _int("RANDOM_KICK_MAX_DELAY", 300))

# Spin Frenzy (the Discord Activity in voice calls). The bot runs a small web server for it on
# ACTIVITY_HOST:ACTIVITY_PORT, which has to be reachable over HTTPS (see the README, e.g. with a Cloudflare Tunnel).
# DISCORD_CLIENT_SECRET is the OAuth2 client secret from the Developer Portal; players can't sign in without it.
ACTIVITY_ENABLED = os.getenv("ACTIVITY_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")
ACTIVITY_HOST = os.getenv("ACTIVITY_HOST", "127.0.0.1").strip() or "127.0.0.1"
ACTIVITY_PORT = _int("ACTIVITY_PORT", 8765)
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "").strip()
# The live blackjack dealer's voice, made on this machine from this voice model: Kokoro (kokoro-v1.0.onnx with
# voices-v1.0.bin next to it, pip install kokoro-onnx) or any Piper model (pip install piper-tts). Empty turns the
# server voice off (devices with their own speech voices still speak; Android's Discord app doesn't).
DEALER_VOICE_MODEL = os.getenv("DEALER_VOICE_MODEL", "voices/kokoro-v1.0.onnx").strip()
# Which Kokoro speaker (af_heart, af_bella, bf_emma, ...). Piper models have one voice and ignore this.
DEALER_VOICE_NAME = os.getenv("DEALER_VOICE_NAME", "af_heart").strip()
# Average share of bets Spin Frenzy pays back on the number spots 1, 2, 5 and 10 (0.97 = 97%)
SPIN_FRENZY_RTP = min(max(_float("SPIN_FRENZY_RTP", 0.97), 0.85), 1.0)
# Average share of bets paid back on the bonus spots (1.0 = break-even). The prize tables are reshaped to hit it.
SPIN_FRENZY_BONUS_PAYBACK = min(max(_float("SPIN_FRENZY_BONUS_PAYBACK", 1.0), 0.85), 1.25)
# How much more often bonus segments win than their share of the wheel (1 = fair share, 3 = three times as often).
SPIN_FRENZY_BONUS_WEIGHT = min(max(_float("SPIN_FRENZY_BONUS_WEIGHT", 1.5), 1.0), 3.0)
SPIN_FRENZY_BET_SECONDS = min(max(_int("SPIN_FRENZY_BET_SECONDS", 15), 8), 60)
# Most a single bet can win, as a multiple of that bet (0 = no limit)
SPIN_FRENZY_MAX_WIN = max(0, _int("SPIN_FRENZY_MAX_WIN", 10000))

DATABASE_PATH = os.getenv("DATABASE_PATH", "gamble.db").strip() or "gamble.db"

CURRENCY = "credits"
COIN = "🪙"

# Logging: console level (DEBUG, INFO, WARNING). The log file in LOG_DIR always records DEBUG detail.
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"
LOG_DIR = os.getenv("LOG_DIR", "logs").strip() or "logs"

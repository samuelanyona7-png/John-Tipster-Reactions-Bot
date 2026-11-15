import os
import io
import re
import csv
import json
import hmac
import base64
import hashlib
import asyncio
import secrets
import sqlite3
import logging
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from contextlib import asynccontextmanager, contextmanager
from html import escape as html_escape
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import FastAPI, Request, HTTPException, Header, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    psycopg2 = None

# --------------------------------------------------
# PRIME TIPS EMPIRE — STAGE 1 PLATFORM (@isla_olivia0)
# --------------------------------------------------

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("prime-tips-empire")

PROJECT_NAME = "PRIME TIPS EMPIRE"
BRAND_TAG = "@isla_olivia0"
DEFAULT_WEBSITE_URL = "https://primetips.site"
DEFAULT_MPESA_TILL = "6076817"
DEFAULT_AIRTEL_NUMBER = "+254754490056"


def env_str(name, default=""):
    return (os.getenv(name) or default).strip()


BOT_TOKEN = env_str("TELEGRAM_BOT_TOKEN")
ADMIN_USER_ID = env_str("ADMIN_USER_ID")
CHANNEL_ID = env_str("CHANNEL_ID")
VIP_CHANNEL_ID = env_str("VIP_CHANNEL_ID")
MAIN_CHANNEL_URL = env_str("MAIN_CHANNEL_URL")
WEBHOOK_SECRET = env_str("WEBHOOK_SECRET")

PAYHERO_AUTH_TOKEN = env_str("PAYHERO_AUTH_TOKEN")
PAYHERO_API_USERNAME = env_str("PAYHERO_API_USERNAME")
PAYHERO_API_PASSWORD = env_str("PAYHERO_API_PASSWORD")
PAYHERO_CHANNEL_ID = env_str("PAYHERO_CHANNEL_ID")
PAYHERO_CREDENTIAL_ID = env_str("PAYHERO_CREDENTIAL_ID")

# Optional CryptAPI configuration (can also be configured in Admin Dashboard -> CryptAPI Settings)
CRYPTAPI_CALLBACK_BASE_URL = env_str("CRYPTAPI_CALLBACK_BASE_URL")
CRYPTAPI_CONFIRMATIONS = env_str("CRYPTAPI_CONFIRMATIONS", "1")
CRYPTAPI_VERIFY_FILE_NAME = env_str("CRYPTAPI_VERIFY_FILE_NAME")
CRYPTAPI_VERIFY_FILE_CONTENT = env_str("CRYPTAPI_VERIFY_FILE_CONTENT")
CRYPTAPI_WALLETS_JSON = env_str("CRYPTAPI_WALLETS_JSON")

EXCHANGE_RATE_API_KEY = env_str("EXCHANGE_RATE_API_KEY")
FOOTBALL_API_KEY = env_str("FOOTBALL_API_KEY")
AI_API_KEY = env_str("AI_API_KEY")

PUBLIC_URL = env_str("RENDER_EXTERNAL_URL") or env_str("PUBLIC_URL")
WEBSITE_URL_ENV = env_str("WEBSITE_URL", DEFAULT_WEBSITE_URL)
DATABASE_URL = env_str("DATABASE_URL")
DB_PATH = env_str("DB_PATH", "john_tipster.db")
WEBSITE_API_KEY = env_str("WEBSITE_API_KEY")
CORS_ORIGINS_RAW = env_str("CORS_ORIGINS", "https://primetips.site,https://www.primetips.site")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://"):]

USE_POSTGRES = bool(DATABASE_URL)
PAYHERO_URL = "https://backend.payhero.co.ke/api/v2"
CRYPTAPI_URL = "https://api.cryptapi.io"
STK_COOLDOWN_SECONDS = 45
FX_CACHE_HOURS = 6
CRYPTAPI_CACHE_MINUTES = 30
REACTION_EMOJIS = ["🔥", "❤️", "💯", "🙏", "👍", "😍"]
BUTTON_STYLES = ["primary", "success", "danger"]

bot_username = None
http_client: httpx.AsyncClient | None = None
_cryptapi_catalog_cache = {"fetched_at": None, "coins": {}, "networks": {}}

# --------------------------------------------------
# INTERNATIONAL COUNTRIES & SUPPORTED CURRENCIES
# --------------------------------------------------

COUNTRIES = {
    "KE": {
        "flag": "🇰🇪", "name": "Kenya", "currency": "KES", "tz": "Africa/Nairobi",
        "methods": ["mpesa", "mpesa_till", "airtel", "crypto"],
        "how": (
            f"1️⃣ <b>M-Pesa STK Push (PayHero)</b> for instant automatic checkout.\n"
            f"2️⃣ Or pay manually via <b>Lipa na M-Pesa Buy Goods Till: <code>{DEFAULT_MPESA_TILL}</code></b>.\n"
            f"3️⃣ Or use <b>Airtel Money: <code>{DEFAULT_AIRTEL_NUMBER}</code></b>.\n"
            "4️⃣ Or pay instantly with <b>Crypto (CryptAPI — 100+ Coins)</b>.\n"
            "5️⃣ Share your payment screenshot or PDF receipt if paying manually."
        ),
    },
    "TZ": {
        "flag": "🇹🇿", "name": "Tanzania", "currency": "TZS", "tz": "Africa/Dar_es_Salaam",
        "methods": ["crypto", "airtel", "mpesa_tz", "tigo"],
        "how": (
            f"1️⃣ Pay instantly via <b>Crypto (CryptAPI — USDT TRC20/BEP20, BTC, SOL & 100+ coins)</b> for automatic activation.\n"
            f"2️⃣ Or select <b>Airtel Money</b> and send to: <code>{DEFAULT_AIRTEL_NUMBER}</code> (or contact @isla_olivia0 for M-Pesa TZ / Mixx by Yas).\n"
            "3️⃣ Send the payment screenshot or receipt file here for manual confirmation."
        ),
    },
    "UG": {
        "flag": "🇺🇬", "name": "Uganda", "currency": "UGX", "tz": "Africa/Kampala",
        "methods": ["crypto", "airtel", "mtn"],
        "how": (
            f"1️⃣ Pay instantly via <b>Crypto (CryptAPI — USDT, BTC, LTC & 100+ coins)</b> for automatic activation.\n"
            f"2️⃣ Or select <b>Airtel Money</b> and send to: <code>{DEFAULT_AIRTEL_NUMBER}</code> (or contact @isla_olivia0 for MTN MoMo).\n"
            "3️⃣ Send your payment screenshot or receipt file here for confirmation."
        ),
    },
    "ZM": {
        "flag": "🇿🇲", "name": "Zambia", "currency": "ZMW", "tz": "Africa/Lusaka",
        "methods": ["crypto", "airtel", "mtn"],
        "how": (
            f"1️⃣ Pay instantly via <b>Crypto (CryptAPI — 100+ coins)</b> for automatic activation.\n"
            f"2️⃣ Or select <b>Airtel Money</b> and send to: <code>{DEFAULT_AIRTEL_NUMBER}</code> (or contact @isla_olivia0 for MTN MoMo).\n"
            "3️⃣ Send your payment screenshot or receipt file here for confirmation."
        ),
    },
    "NG": {
        "flag": "🇳🇬", "name": "Nigeria", "currency": "NGN", "tz": "Africa/Lagos",
        "methods": ["crypto", "ng_bank"],
        "how": "1️⃣ Pay automatically via <b>Crypto (CryptAPI — USDT TRC20/BEP20, BTC, SOL, TON & 100+ coins)</b>.\n2️⃣ Or select Nigeria Bank / OPay / PalmPay & contact @isla_olivia0 for Naira transfer details.",
    },
    "GH": {
        "flag": "🇬🇭", "name": "Ghana", "currency": "GHS", "tz": "Africa/Accra",
        "methods": ["crypto", "airtel", "mtn"],
        "how": f"1️⃣ Pay automatically via <b>Crypto (CryptAPI — 100+ coins)</b>.\n2️⃣ Or select AirtelTigo Money (<code>{DEFAULT_AIRTEL_NUMBER}</code>) or MTN MoMo via @isla_olivia0.",
    },
    "ZA": {
        "flag": "🇿🇦", "name": "South Africa", "currency": "ZAR", "tz": "Africa/Johannesburg",
        "methods": ["crypto", "za_eft"],
        "how": "1️⃣ Pay automatically via <b>Crypto (CryptAPI — 100+ coins)</b>.\n2️⃣ Or select Instant EFT / Capitec & contact @isla_olivia0 for EFT details.",
    },
    "US": {
        "flag": "🇺🇸", "name": "United States", "currency": "USD", "tz": "America/New_York",
        "methods": ["crypto", "intl_transfer"],
        "how": "1️⃣ Pay automatically via <b>Crypto (CryptAPI — BTC, ETH, USDT, USDC, SOL & 100+ coins)</b>.\n2️⃣ Or select Card / PayPal / Transfer via @isla_olivia0.",
    },
    "GB": {
        "flag": "🇬🇧", "name": "United Kingdom", "currency": "GBP", "tz": "Europe/London",
        "methods": ["crypto", "intl_transfer"],
        "how": "1️⃣ Pay automatically via <b>Crypto (CryptAPI — 100+ coins)</b>.\n2️⃣ Or select UK Bank / PayPal via @isla_olivia0.",
    },
    "EU": {
        "flag": "🇪🇺", "name": "Europe (Eurozone)", "currency": "EUR", "tz": "Europe/Berlin",
        "methods": ["crypto", "intl_transfer"],
        "how": "1️⃣ Pay automatically via <b>Crypto (CryptAPI — 100+ coins)</b>.\n2️⃣ Or select SEPA / PayPal via @isla_olivia0.",
    },
    "IN": {
        "flag": "🇮🇳", "name": "India", "currency": "INR", "tz": "Asia/Kolkata",
        "methods": ["crypto", "intl_transfer"],
        "how": "1️⃣ Pay automatically via <b>Crypto (CryptAPI — USDT TRC20/BEP20/Polygon, BTC & 100+ coins)</b>.\n2️⃣ Or select UPI / Transfer via @isla_olivia0.",
    },
    "CA": {
        "flag": "🇨🇦", "name": "Canada", "currency": "CAD", "tz": "America/Toronto",
        "methods": ["crypto", "intl_transfer"],
        "how": "1️⃣ Pay automatically via <b>Crypto (CryptAPI — 100+ coins)</b>.\n2️⃣ Or select Interac / PayPal via @isla_olivia0.",
    },
    "AU": {
        "flag": "🇦🇺", "name": "Australia", "currency": "AUD", "tz": "Australia/Sydney",
        "methods": ["crypto", "intl_transfer"],
        "how": "1️⃣ Pay automatically via <b>Crypto (CryptAPI — 100+ coins)</b>.\n2️⃣ Or select PayID / PayPal via @isla_olivia0.",
    },
}

CURRENCIES = {
    "KES": {"symbol": "KES ", "decimals": 0},
    "TZS": {"symbol": "TZS ", "decimals": 0},
    "UGX": {"symbol": "UGX ", "decimals": 0},
    "ZMW": {"symbol": "ZMW ", "decimals": 2},
    "NGN": {"symbol": "₦", "decimals": 0},
    "GHS": {"symbol": "GHS ", "decimals": 2},
    "ZAR": {"symbol": "R", "decimals": 2},
    "USD": {"symbol": "$", "decimals": 2},
    "GBP": {"symbol": "£", "decimals": 2},
    "EUR": {"symbol": "€", "decimals": 2},
    "INR": {"symbol": "₹", "decimals": 0},
    "CAD": {"symbol": "CA$", "decimals": 2},
    "AUD": {"symbol": "AU$", "decimals": 2},
}

EUROZONE_ISO = {"AT", "BE", "CY", "EE", "FI", "FR", "DE", "GR", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PT", "SK", "SI", "ES", "HR"}

METHOD_LABELS = {
    "mpesa": ("📲 M-Pesa STK Push (PayHero)", "success"),
    "mpesa_till": (f"🏪 Manual M-Pesa Till ({DEFAULT_MPESA_TILL})", "primary"),
    "airtel": (f"🔴 Airtel Money ({DEFAULT_AIRTEL_NUMBER})", "danger"),
    "crypto": ("🪙 Crypto (CryptAPI — 100+ Coins)", "success"),
    "mtn": ("🟡 MTN Mobile Money", "primary"),
    "mpesa_tz": ("🇹🇿 Vodacom M-Pesa TZ", "primary"),
    "tigo": ("🔵 Mixx / Tigo Pesa", "primary"),
    "ng_bank": ("🇳🇬 Bank / OPay / PalmPay", "success"),
    "za_eft": ("🇿🇦 Instant EFT / Capitec", "primary"),
    "intl_transfer": ("💲 Card / PayPal / Bank Transfer", "primary"),
}

ACCUM_CATEGORIES = ["3-5 Odds", "5-10 Odds", "10-15 Odds", "15-20 Odds", "20+ Odds"]

CRYPTO_WALLET_GROUPS = [
    ("evm", "🔷 EVM Chains (ETH, ERC20, BEP20, Polygon, Arbitrum, Optimism, Base, AVAX, etc.)", "CRYPTAPI_EVM_ADDRESS"),
    ("tron", "🔴 TRON & TRC20 (TRX, USDT-TRC20, USDC-TRC20, etc.)", "CRYPTAPI_TRON_ADDRESS"),
    ("sol", "🟣 Solana & SPL Tokens (SOL, USDT-SOL, USDC-SOL, etc.)", "CRYPTAPI_SOL_ADDRESS"),
    ("ton", "💎 TON & Jettons (TON, USDT-TON)", "CRYPTAPI_TON_ADDRESS"),
    ("btc", "🟠 Bitcoin (BTC)", "CRYPTAPI_BTC_ADDRESS"),
    ("ltc", "⚪ Litecoin (LTC)", "CRYPTAPI_LTC_ADDRESS"),
    ("bch", "🟢 Bitcoin Cash (BCH)", "CRYPTAPI_BCH_ADDRESS"),
    ("doge", "🟡 Dogecoin (DOGE)", "CRYPTAPI_DOGE_ADDRESS"),
    ("zec", "🛡️ Zcash (ZEC)", "CRYPTAPI_ZEC_ADDRESS"),
]

CRYPTO_NETWORK_LABELS = {
    "coins": "🪙 Native Coins",
    "trc20": "🔴 Tron (TRC-20)",
    "bep20": "🟡 BNB Smart Chain (BEP-20)",
    "erc20": "🔷 Ethereum (ERC-20)",
    "polygon": "🟣 Polygon",
    "sol": "🧬 Solana",
    "ton": "💎 TON",
    "arbitrum": "🔵 Arbitrum One",
    "optimism": "🔺 Optimism",
    "base": "🟦 Base",
    "avax-c": "❄️ Avalanche-C",
    "linea": "📏 Linea",
    "monad": "🟪 Monad",
    "berachain": "🐻 Berachain",
}

POPULAR_CRYPTO_TICKERS = [
    "trc20/usdt", "bep20/usdt", "sol/usdt", "ton/usdt", "polygon/usdt", "erc20/usdt",
    "trc20/usdc", "bep20/usdc", "sol/usdc", "base/usdc", "btc", "eth", "ltc", "trx", "sol/sol", "ton/ton", "doge", "bch",
]


def check_settings():
    settings = {
        "TELEGRAM_BOT_TOKEN": BOT_TOKEN,
        "ADMIN_USER_ID": ADMIN_USER_ID,
        "CHANNEL_ID": CHANNEL_ID,
        "VIP_CHANNEL_ID": VIP_CHANNEL_ID,
        "WEBHOOK_SECRET": WEBHOOK_SECRET,
        "PAYHERO_CHANNEL_ID": PAYHERO_CHANNEL_ID,
        "PUBLIC_URL / RENDER_EXTERNAL_URL": PUBLIC_URL,
    }
    if not PAYHERO_AUTH_TOKEN and not (PAYHERO_API_USERNAME and PAYHERO_API_PASSWORD):
        settings["PAYHERO_AUTH_TOKEN (or PAYHERO_API_USERNAME + PAYHERO_API_PASSWORD)"] = ""
    missing = [k for k, v in settings.items() if not v]
    if missing:
        raise RuntimeError("Missing environment variables: " + ", ".join(missing))
    if not ADMIN_USER_ID.isdigit():
        raise RuntimeError("ADMIN_USER_ID must be numeric")
    if not PAYHERO_CHANNEL_ID.isdigit():
        raise RuntimeError("PAYHERO_CHANNEL_ID must be numeric")
    if USE_POSTGRES and psycopg2 is None:
        raise RuntimeError("DATABASE_URL is set; add psycopg2-binary to requirements.txt")


def utcnow():
    return datetime.now(timezone.utc)


def parse_utc(dt_or_iso):
    dt = datetime.fromisoformat(dt_or_iso) if isinstance(dt_or_iso, str) else dt_or_iso
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def format_local_time(dt_or_iso, tz_name="UTC"):
    try:
        tz = ZoneInfo(tz_name or "UTC")
    except ZoneInfoNotFoundError:
        tz = timezone.utc
    dt_local = parse_utc(dt_or_iso).astimezone(tz)
    return dt_local.strftime("%d %b %Y, %I:%M %p ") + (tz_name.split("/")[-1] if tz_name != "UTC" else "UTC")


async def get_http():
    global http_client
    if http_client is None or http_client.is_closed:
        http_client = httpx.AsyncClient(timeout=35)
    return http_client


# --------------------------------------------------
# STRICT ADMIN ACCESS CONTROL (ADMIN ONLY)
# --------------------------------------------------

def is_admin(user_id):
    return bool(ADMIN_USER_ID) and ADMIN_USER_ID.isdigit() and str(user_id) == str(ADMIN_USER_ID)


def is_admin_private_chat(user_id, chat_id, chat_type="private"):
    return is_admin(user_id) and chat_type == "private" and str(chat_id) == str(ADMIN_USER_ID)


# --------------------------------------------------
# REAL-TIME DATABASE LAYER (PostgreSQL + SQLite)
# --------------------------------------------------

class DBConn:
    def __init__(self, raw_con):
        self.raw = raw_con

    def execute(self, sql, params=()):
        adapted = sql.replace("?", "%s") if USE_POSTGRES else sql
        if USE_POSTGRES:
            cur = self.raw.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(adapted, params)
            return cur
        return self.raw.execute(adapted, params)


def insert_returning_id(db, sql, params):
    if USE_POSTGRES:
        cur = db.execute(sql + " RETURNING id", params)
        return int(cur.fetchone()["id"])
    cur = db.execute(sql, params)
    return int(cur.lastrowid)


@contextmanager
def connect_db():
    if USE_POSTGRES:
        raw = psycopg2.connect(DATABASE_URL, connect_timeout=10)
    else:
        raw = sqlite3.connect(DB_PATH, timeout=30)
        raw.row_factory = sqlite3.Row
        raw.execute("PRAGMA journal_mode=WAL")
        raw.execute("PRAGMA synchronous=NORMAL")
    db = DBConn(raw)
    try:
        yield db
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()


def ensure_column(db, table, column, definition):
    if USE_POSTGRES:
        db.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}")
    else:
        cols = [r["name"] for r in db.execute(f"PRAGMA table_info({table})").fetchall()]
        if column not in cols:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db():
    pk_auto = "SERIAL PRIMARY KEY" if USE_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"
    with connect_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                country TEXT NOT NULL DEFAULT '',
                country_confirmed INTEGER NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'USD',
                timezone_name TEXT NOT NULL DEFAULT 'UTC',
                referred_by BIGINT,
                referral_rewarded INTEGER NOT NULL DEFAULT 0,
                is_blocked INTEGER NOT NULL DEFAULT 0,
                notify_free INTEGER NOT NULL DEFAULT 1,
                notify_vip INTEGER NOT NULL DEFAULT 1,
                notify_news INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS plans (
                name TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                price INTEGER NOT NULL,
                days INTEGER NOT NULL,
                benefits TEXT NOT NULL DEFAULT '',
                is_lifetime INTEGER NOT NULL DEFAULT 0
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                user_id BIGINT PRIMARY KEY,
                state TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}'
            )
        """)

        if USE_POSTGRES:
            has_old_sessions = db.execute(
                "SELECT 1 FROM information_schema.columns WHERE table_name='sessions' AND column_name='plan'"
            ).fetchone()
        else:
            has_old_sessions = "plan" in [r["name"] for r in db.execute("PRAGMA table_info(sessions)").fetchall()]
        if has_old_sessions:
            db.execute("DROP TABLE sessions")
            db.execute("CREATE TABLE sessions (user_id BIGINT PRIMARY KEY, state TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}')")

        db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                external_ref TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                plan TEXT NOT NULL,
                amount INTEGER NOT NULL,
                currency TEXT NOT NULL DEFAULT 'KES',
                local_amount TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT 'mpesa',
                status TEXT NOT NULL,
                payhero_reference TEXT,
                checkout_id TEXT,
                mpesa_receipt TEXT,
                proof_file_id TEXT,
                proof_type TEXT NOT NULL DEFAULT 'photo',
                crypto_ticker TEXT,
                crypto_address_in TEXT,
                crypto_amount TEXT,
                crypto_txid TEXT,
                crypto_uuid TEXT,
                callback_nonce TEXT,
                created_at TEXT NOT NULL
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id BIGINT PRIMARY KEY,
                plan TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                status TEXT NOT NULL,
                is_lifetime INTEGER NOT NULL DEFAULT 0,
                reminded INTEGER NOT NULL DEFAULT 0
            )
        """)

        db.execute(f"""
            CREATE TABLE IF NOT EXISTS matches (
                id {pk_auto},
                tier TEXT NOT NULL,
                match_name TEXT NOT NULL,
                market TEXT NOT NULL DEFAULT '1X2',
                tip TEXT NOT NULL,
                odds TEXT NOT NULL,
                match_date TEXT NOT NULL,
                kickoff_time TEXT NOT NULL,
                publish_status TEXT NOT NULL DEFAULT 'QUEUED',
                result TEXT NOT NULL DEFAULT 'PENDING',
                scheduled_for TEXT,
                channel_msg_id BIGINT,
                created_at TEXT NOT NULL
            )
        """)

        db.execute(f"""
            CREATE TABLE IF NOT EXISTS accumulators (
                id {pk_auto},
                category TEXT NOT NULL,
                selections_json TEXT NOT NULL,
                total_odds TEXT NOT NULL,
                risk_note TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'PENDING_ADMIN',
                created_by BIGINT NOT NULL,
                channel_msg_id BIGINT,
                created_at TEXT NOT NULL
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS post_reactions (
                channel_msg_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                emoji TEXT NOT NULL,
                PRIMARY KEY (channel_msg_id, user_id, emoji)
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS exchange_rates (
                currency TEXT PRIMARY KEY,
                rate_from_kes DOUBLE PRECISION NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        db.execute(f"""
            CREATE TABLE IF NOT EXISTS custom_buttons (
                id {pk_auto},
                label TEXT NOT NULL,
                url TEXT NOT NULL DEFAULT '',
                button_type TEXT NOT NULL DEFAULT 'TICKET',
                price INTEGER NOT NULL DEFAULT 0,
                days INTEGER NOT NULL DEFAULT 1,
                description TEXT NOT NULL DEFAULT '',
                style TEXT NOT NULL DEFAULT 'primary',
                is_active INTEGER NOT NULL DEFAULT 1
            )
        """)

        db.execute(f"""
            CREATE TABLE IF NOT EXISTS ticket_media (
                id {pk_auto},
                offer_key TEXT NOT NULL,
                file_id TEXT NOT NULL,
                media_type TEXT NOT NULL DEFAULT 'photo',
                caption TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS channel_posts (
                channel_msg_id BIGINT PRIMARY KEY,
                button_ids TEXT NOT NULL DEFAULT '[]',
                include_reactions INTEGER NOT NULL DEFAULT 1,
                include_footer INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        db.execute(f"""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id {pk_auto},
                actor_id BIGINT NOT NULL,
                action TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)

        # Migrations for pre-existing tables
        ensure_column(db, "users", "country", "TEXT NOT NULL DEFAULT ''")
        ensure_column(db, "users", "country_confirmed", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(db, "users", "currency", "TEXT NOT NULL DEFAULT 'USD'")
        ensure_column(db, "users", "timezone_name", "TEXT NOT NULL DEFAULT 'UTC'")
        ensure_column(db, "plans", "title", "TEXT NOT NULL DEFAULT ''")
        ensure_column(db, "plans", "benefits", "TEXT NOT NULL DEFAULT ''")
        ensure_column(db, "plans", "is_lifetime", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(db, "orders", "provider", "TEXT NOT NULL DEFAULT 'mpesa'")
        ensure_column(db, "orders", "mpesa_receipt", "TEXT")
        ensure_column(db, "orders", "proof_file_id", "TEXT")
        ensure_column(db, "orders", "proof_type", "TEXT NOT NULL DEFAULT 'photo'")
        ensure_column(db, "orders", "currency", "TEXT NOT NULL DEFAULT 'KES'")
        ensure_column(db, "orders", "local_amount", "TEXT NOT NULL DEFAULT ''")
        ensure_column(db, "orders", "crypto_ticker", "TEXT")
        ensure_column(db, "orders", "crypto_address_in", "TEXT")
        ensure_column(db, "orders", "crypto_amount", "TEXT")
        ensure_column(db, "orders", "crypto_txid", "TEXT")
        ensure_column(db, "orders", "crypto_uuid", "TEXT")
        ensure_column(db, "orders", "callback_nonce", "TEXT")
        ensure_column(db, "subscriptions", "is_lifetime", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(db, "subscriptions", "reminded", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(db, "matches", "market", "TEXT NOT NULL DEFAULT '1X2'")
        ensure_column(db, "custom_buttons", "button_type", "TEXT NOT NULL DEFAULT 'URL'")
        ensure_column(db, "custom_buttons", "price", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(db, "custom_buttons", "days", "INTEGER NOT NULL DEFAULT 1")
        ensure_column(db, "custom_buttons", "description", "TEXT NOT NULL DEFAULT ''")
        ensure_column(db, "custom_buttons", "style", "TEXT NOT NULL DEFAULT 'primary'")
        ensure_column(db, "custom_buttons", "is_active", "INTEGER NOT NULL DEFAULT 1")

        defaults_plans = [
            ("daily", "Daily VIP", 100, 1, "24 Hours VIP access • Daily single & combo picks", 0),
            ("weekly", "Weekly VIP", 300, 7, "7 Days VIP access • Daily slips & analysis alerts", 0),
            ("monthly", "Monthly VIP", 1000, 30, "30 Days VIP access • Full ticket access • Best value", 0),
            ("premium", "Premium VIP", 2500, 90, "90 Days VIP access • All accumulator slips • Priority support", 0),
            ("lifetime", "Lifetime VIP", 10000, 36500, "Permanent VIP access • Never expires • All future updates", 1),
        ]
        for row in defaults_plans:
            db.execute(
                "INSERT INTO plans(name,title,price,days,benefits,is_lifetime) VALUES(?,?,?,?,?,?) ON CONFLICT(name) DO NOTHING",
                row,
            )

        default_settings = {
            "main_channel_id": CHANNEL_ID,
            "vip_channel_id": VIP_CHANNEL_ID,
            "main_channel_url": MAIN_CHANNEL_URL or (f"https://t.me/{CHANNEL_ID.lstrip('@')}" if CHANNEL_ID.startswith("@") else ""),
            "payhero_channel_id": PAYHERO_CHANNEL_ID,
            "env_payhero_channel_id": PAYHERO_CHANNEL_ID,
            "mpesa_till_number": DEFAULT_MPESA_TILL,
            "airtel_money_number": DEFAULT_AIRTEL_NUMBER,
            "auto_confirm_screenshot": "0",
            "referral_reward_days": "2",
            "vip_odds_info": "🔥 Daily VIP Matches: Curated Football Picks & Multi-Bet Tickets",
            "admin_contact_url": f"https://t.me/{BRAND_TAG.lstrip('@')}",
            "website_url": WEBSITE_URL_ENV or DEFAULT_WEBSITE_URL,
            "auto_channel_buttons": "0",
            "cryptapi_wallets": "{}",
            "cryptapi_confirmations": CRYPTAPI_CONFIRMATIONS or "1",
            "cryptapi_callback_base": CRYPTAPI_CALLBACK_BASE_URL,
        }
        for k, v in default_settings.items():
            db.execute("INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO NOTHING", (k, v))

        # Auto-sync PAYHERO_CHANNEL_ID if environment variable was updated between deploys
        prev_env_ph = db.execute("SELECT value FROM app_settings WHERE key='env_payhero_channel_id'").fetchone()
        if PAYHERO_CHANNEL_ID and (not prev_env_ph or prev_env_ph["value"] != PAYHERO_CHANNEL_ID):
            db.execute(
                "INSERT INTO app_settings(key,value) VALUES('payhero_channel_id',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (PAYHERO_CHANNEL_ID,),
            )
            db.execute(
                "INSERT INTO app_settings(key,value) VALUES('env_payhero_channel_id',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (PAYHERO_CHANNEL_ID,),
            )

        seeded = db.execute("SELECT value FROM app_settings WHERE key='seeded_ticket_buttons'").fetchone()
        if not seeded:
            default_ticket_buttons = [
                ("🔵 Get Blue Ticket", "", "TICKET", 500, 1, "Blue VIP Ticket — Curated high-confidence picks", "primary"),
                ("🎫 Get Daily Ticket", "", "TICKET", 200, 1, "Daily Ticket — Today's top selections", "success"),
                ("🥇 Golden Ticket", "", "TICKET", 1000, 3, "Golden VIP Ticket — Premium multi-match slip", "danger"),
                ("🇲🇰 Get Macedonia Ticket", "", "TICKET", 1500, 1, "Macedonia Special Ticket — Exclusive fixture slip", "primary"),
                ("🇪🇺 Euro Ticket", "", "TICKET", 800, 1, "European Leagues Ticket — Top Euro picks", "success"),
            ]
            for lbl, url, btype, price, days, desc, style in default_ticket_buttons:
                db.execute(
                    """
                    INSERT INTO custom_buttons(label,url,button_type,price,days,description,style,is_active)
                    VALUES(?,?,?,?,?,?,?,1)
                    """,
                    (lbl, url, btype, price, days, desc, style),
                )
            db.execute(
                "INSERT INTO app_settings(key,value) VALUES('seeded_ticket_buttons','1') ON CONFLICT(key) DO UPDATE SET value='1'"
            )

        db.execute(
            "INSERT INTO exchange_rates(currency,rate_from_kes,updated_at) VALUES('KES',1.0,?) ON CONFLICT(currency) DO NOTHING",
            (utcnow().isoformat(),),
        )


def log_audit(actor_id, action, details=""):
    try:
        with connect_db() as db:
            db.execute(
                "INSERT INTO audit_logs(actor_id,action,details,created_at) VALUES(?,?,?,?)",
                (int(actor_id), action, str(details)[:500], utcnow().isoformat()),
            )
    except Exception:
        log.exception("Audit log write failed")


def get_setting(key, fallback=""):
    with connect_db() as db:
        row = db.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    val = row["value"] if row else fallback
    return val if (val is not None and val != "") else fallback


def set_setting(key, value):
    with connect_db() as db:
        db.execute(
            "INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )


def get_website_url():
    url = get_setting("website_url", DEFAULT_WEBSITE_URL).strip()
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def get_user(user_id):
    with connect_db() as db:
        return db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def register_user(user, referred_by=None):
    user_id = user.get("id")
    if not user_id:
        return
    username = user.get("username") or ""
    first_name = user.get("first_name") or "Member"

    if referred_by == user_id or (referred_by and not get_user(referred_by)):
        referred_by = None

    with connect_db() as db:
        existing = db.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,)).fetchone()
        if existing:
            db.execute("UPDATE users SET username=?, first_name=? WHERE user_id=?", (username, first_name, user_id))
        else:
            db.execute(
                """
                INSERT INTO users(user_id,username,first_name,country,country_confirmed,currency,timezone_name,referred_by,created_at)
                VALUES(?,?,?,'',0,'KES','UTC',?,?)
                """,
                (user_id, username, first_name, referred_by, utcnow().isoformat()),
            )


def set_session(user_id, state, payload=""):
    with connect_db() as db:
        db.execute(
            """
            INSERT INTO sessions(user_id,state,payload) VALUES(?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET state=excluded.state, payload=excluded.payload
            """,
            (user_id, state, payload),
        )


def get_session(user_id):
    with connect_db() as db:
        return db.execute("SELECT * FROM sessions WHERE user_id=?", (user_id,)).fetchone()


def clear_session(user_id):
    with connect_db() as db:
        db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))


def has_active_vip(user_id):
    with connect_db() as db:
        sub = db.execute("SELECT * FROM subscriptions WHERE user_id=?", (user_id,)).fetchone()
    if not sub or sub["status"] != "ACTIVE":
        return False
    if sub["is_lifetime"]:
        return True
    return parse_utc(sub["expires_at"]) > utcnow()


# --------------------------------------------------
# CUSTOM BUTTONS / TICKET IMAGES HELPERS
# --------------------------------------------------

def get_all_custom_buttons(only_active=True):
    with connect_db() as db:
        if only_active:
            return db.execute("SELECT * FROM custom_buttons WHERE is_active=1 ORDER BY id").fetchall()
        return db.execute("SELECT * FROM custom_buttons ORDER BY id").fetchall()


def get_custom_button(btn_id):
    with connect_db() as db:
        return db.execute("SELECT * FROM custom_buttons WHERE id=?", (int(btn_id),)).fetchone()


def get_offer_media(offer_key):
    with connect_db() as db:
        return db.execute("SELECT * FROM ticket_media WHERE offer_key=? ORDER BY id", (str(offer_key),)).fetchall()


def count_offer_media(offer_key):
    with connect_db() as db:
        return int(db.execute("SELECT COUNT(*) AS n FROM ticket_media WHERE offer_key=?", (str(offer_key),)).fetchone()["n"])


def resolve_offer(plan_or_btn):
    if isinstance(plan_or_btn, str) and plan_or_btn.startswith("btn:"):
        raw_id = plan_or_btn.split(":", 1)[1]
        if not raw_id.isdigit():
            return None
        b = get_custom_button(int(raw_id))
        if not b or not b["is_active"] or b["button_type"] != "TICKET" or int(b["price"]) <= 0:
            return None
        return {
            "kind": "button",
            "key": plan_or_btn,
            "btn_id": int(b["id"]),
            "title": b["label"],
            "price": int(b["price"]),
            "days": int(b["days"]),
            "is_lifetime": 0,
            "description": b["description"] or "",
        }
    p = get_plan(plan_or_btn)
    if not p:
        return None
    return {
        "kind": "plan",
        "key": p["name"],
        "btn_id": None,
        "title": p["title"] or f"{p['name'].title()} VIP",
        "price": int(p["price"]),
        "days": int(p["days"]),
        "is_lifetime": int(p["is_lifetime"]),
        "description": p["benefits"] or "",
    }


def format_order_item_name(plan_key):
    if isinstance(plan_key, str) and plan_key.startswith("btn:"):
        raw_id = plan_key.split(":", 1)[1]
        if raw_id.isdigit():
            b = get_custom_button(int(raw_id))
            if b:
                return b["label"]
        return f"Ticket #{raw_id}"
    p = get_plan(plan_key)
    if p and p["title"]:
        return p["title"]
    return str(plan_key).title()


# --------------------------------------------------
# LIVE EXCHANGE RATES & COUNTRY HELPERS
# --------------------------------------------------

async def refresh_exchange_rates(force=False):
    with connect_db() as db:
        usd_row = db.execute("SELECT updated_at FROM exchange_rates WHERE currency='USD'").fetchone()

    if not force and usd_row:
        age_hours = (utcnow() - parse_utc(usd_row["updated_at"])).total_seconds() / 3600.0
        if age_hours < FX_CACHE_HOURS:
            return True

    url = (
        f"https://v6.exchangerate-api.com/v6/{EXCHANGE_RATE_API_KEY}/latest/KES"
        if EXCHANGE_RATE_API_KEY
        else "https://open.er-api.com/v6/latest/KES"
    )
    try:
        client = await get_http()
        resp = await client.get(url, timeout=15)
        data = resp.json()
        rates = data.get("conversion_rates") or data.get("rates") or {}
        if not rates:
            return False

        now_iso = utcnow().isoformat()
        with connect_db() as db:
            for code in CURRENCIES:
                val = 1.0 if code == "KES" else rates.get(code)
                if val and float(val) > 0:
                    db.execute(
                        """
                        INSERT INTO exchange_rates(currency,rate_from_kes,updated_at) VALUES(?,?,?)
                        ON CONFLICT(currency) DO UPDATE SET rate_from_kes=excluded.rate_from_kes, updated_at=excluded.updated_at
                        """,
                        (code, float(val), now_iso),
                    )
        log.info("Exchange rates updated from live API")
        return True
    except Exception as e:
        log.warning("Could not refresh exchange rates: %s", e)
        return False


def get_cached_rate(currency_code):
    code = (currency_code or "KES").upper()
    if code == "KES":
        return 1.0
    with connect_db() as db:
        row = db.execute("SELECT rate_from_kes FROM exchange_rates WHERE currency=?", (code,)).fetchone()
    return float(row["rate_from_kes"]) if row else None


def kes_to_usd(kes_amount):
    rate = get_cached_rate("USD")
    if not rate or rate <= 0:
        rate = 1.0 / 129.0  # Safe emergency fallback only if FX API hasn't responded yet
    return round(float(kes_amount) * rate, 2)


def format_converted_price(kes_amount, display_currency):
    code = (display_currency or "KES").upper()
    rate = get_cached_rate(code)
    if code == "KES" or rate is None:
        return f"KES {int(kes_amount):,}", False
    meta = CURRENCIES.get(code, {"symbol": f"{code} ", "decimals": 2})
    converted = float(kes_amount) * rate
    if meta["decimals"] == 0:
        formatted = f"{meta['symbol']}{int(round(converted)):,}"
    else:
        formatted = f"{meta['symbol']}{converted:,.2f}"
    return f"{formatted} {code} (KES {int(kes_amount):,})", True


def get_user_prefs(user_id):
    u = get_user(user_id)
    if not u:
        return {"country": "", "confirmed": False, "currency": "KES", "tz": "UTC"}
    code = u["country"] if u["country"] in COUNTRIES else ""
    curr = u["currency"] if u["currency"] in CURRENCIES else "KES"
    tz = u["timezone_name"] or "UTC"
    return {"country": code, "confirmed": bool(u["country_confirmed"]), "currency": curr, "tz": tz}


async def reverse_geocode_country(lat, lon):
    url = "https://nominatim.openstreetmap.org/reverse"
    client = await get_http()
    resp = await client.get(
        url,
        params={"format": "jsonv2", "lat": lat, "lon": lon, "zoom": 3},
        headers={"User-Agent": "PrimeTipsEmpireBot/1.0 (https://primetips.site)"},
        timeout=15,
    )
    if resp.status_code != 200:
        return None
    data = resp.json()
    addr = data.get("address") or {}
    iso2 = (addr.get("country_code") or "").upper()
    if iso2 in EUROZONE_ISO:
        return "EU"
    if iso2 in COUNTRIES:
        return iso2
    return None


# --------------------------------------------------
# TELEGRAM API
# --------------------------------------------------

async def telegram(method, data):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    client = await get_http()
    response = await client.post(url, json=data)
    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {result.get('description', result)}")
    return result.get("result")


async def ensure_bot_username():
    global bot_username
    if not bot_username:
        me = await telegram("getMe", {})
        bot_username = me["username"]
    return bot_username


async def telegram_send_document(chat_id, filename, content_bytes, caption=""):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    client = await get_http()
    resp = await client.post(
        url,
        data={"chat_id": str(chat_id), "caption": caption, "parse_mode": "HTML"},
        files={"document": (filename, content_bytes, "text/csv")},
    )
    res = resp.json()
    if not res.get("ok"):
        raise RuntimeError(f"sendDocument failed: {res.get('description', res)}")
    return res.get("result")


async def telegram_send_photo_bytes(chat_id, filename, photo_bytes, caption="", keyboard=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    client = await get_http()
    data = {"chat_id": str(chat_id), "caption": caption[:1020], "parse_mode": "HTML"}
    if keyboard:
        data["reply_markup"] = json.dumps(keyboard)
    resp = await client.post(
        url,
        data=data,
        files={"photo": (filename, photo_bytes, "image/png")},
    )
    res = resp.json()
    if not res.get("ok"):
        raise RuntimeError(f"sendPhoto failed: {res.get('description', res)}")
    return res.get("result")


def split_telegram_text(text, limit=3900):
    if len(text) <= limit:
        return [text]
    chunks, remaining = [], text
    while len(remaining) > limit:
        cut = remaining.rfind("\n\n", 0, limit)
        if cut < 500:
            cut = remaining.rfind("\n", 0, limit)
        if cut < 200:
            cut = limit
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


async def send_message(chat_id, text, keyboard=None):
    chunks = split_telegram_text(text)
    last_res = None
    for i, chunk in enumerate(chunks):
        data = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if keyboard and i == len(chunks) - 1:
            data["reply_markup"] = keyboard
        last_res = await telegram("sendMessage", data)
    return last_res


async def edit_or_send(chat_id, message_id, text, keyboard=None):
    if message_id:
        try:
            payload = {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text[:4090],
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            if keyboard:
                payload["reply_markup"] = keyboard
            return await telegram("editMessageText", payload)
        except Exception:
            pass
    return await send_message(chat_id, text, keyboard)


async def answer_callback(callback_id, text=""):
    try:
        await telegram("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})
    except Exception:
        log.exception("Could not answer callback")


async def report_unauthorized_admin_attempt(user_id, context_label):
    log_audit(user_id, "UNAUTHORIZED_ADMIN_ATTEMPT", context_label)
    try:
        u = get_user(user_id)
        uname = f"@{u['username']}" if (u and u["username"]) else str(user_id)
        await send_message(
            ADMIN_USER_ID,
            f"🚨 <b>UNAUTHORIZED ADMIN ACCESS BLOCKED</b>\n"
            f"👤 User: {html_escape(uname)} (<code>{user_id}</code>)\n"
            f"🔎 Attempt: <code>{html_escape(context_label)}</code>",
        )
    except Exception:
        pass


# --------------------------------------------------
# KEYBOARDS (MENU + GENUINE REACTIONS + CHANNEL BUTTONS)
# --------------------------------------------------

def main_menu_keyboard(user_id=None):
    ch_url = get_setting("main_channel_url") or get_website_url()
    portal_url = PUBLIC_URL.rstrip("/") + "/app"
    portal_btn = (
        {"text": "🌐 Open Empire Web Portal", "web_app": {"url": portal_url}, "style": "primary"}
        if portal_url.startswith("https://")
        else {"text": "🌐 Open Empire Web Portal", "url": portal_url, "style": "primary"}
    )
    rows = [
        [
            {"text": "⚽ Today's Predictions", "callback_data": "menu:today", "style": "success"},
            {"text": "🤖 AI Match Analysis", "callback_data": "menu:ai_analysis", "style": "primary"},
        ],
        [
            {"text": "🎯 Accumulator Builder", "callback_data": "menu:accum", "style": "primary"},
            {"text": "📊 Football Statistics", "callback_data": "menu:stats", "style": "success"},
        ],
        [
            {"text": "🏆 Results & Accuracy", "callback_data": "menu:results", "style": "primary"},
            {"text": "👑 VIP Membership", "callback_data": "menu:vip", "style": "danger"},
        ],
        [
            {"text": "💳 My Subscription", "callback_data": "menu:subscription", "style": "success"},
            {"text": "🌍 Country & Currency Settings", "callback_data": "menu:country", "style": "primary"},
        ],
        [
            {"text": "📢 Join Our Channel", "url": ch_url, "style": "primary"},
            {"text": "💬 Contact Admin", "callback_data": "menu:contact", "style": "success"},
        ],
        [portal_btn],
    ]
    if user_id and is_admin(user_id):
        rows.append([{"text": "⚡ ADMIN DASHBOARD", "callback_data": "admin:home", "style": "danger"}])
    return {"inline_keyboard": rows}


def country_welcome_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "🌍 Detect My Country", "callback_data": "country:detect_ask", "style": "primary"}],
            [{"text": "🗺️ Select Country Manually", "callback_data": "country:manual", "style": "success"}],
            [{"text": "⏭️ Continue to Free Features", "callback_data": "menu:home"}],
        ]
    }


def country_manual_keyboard():
    items = list(COUNTRIES.items())
    rows = []
    for i in range(0, len(items), 2):
        pair = []
        for code, info in items[i:i + 2]:
            pair.append({
                "text": f"{info['flag']} {info['name']} ({info['currency']})",
                "callback_data": f"country:pick:{code}",
                "style": "primary",
            })
        rows.append(pair)
    rows.append([{"text": "💱 Change Currency Only", "callback_data": "currency:menu", "style": "success"}])
    rows.append([{"text": "🏠 Main Menu", "callback_data": "menu:home"}])
    return {"inline_keyboard": rows}


def currency_manual_keyboard():
    items = list(CURRENCIES.keys())
    rows = []
    for i in range(0, len(items), 3):
        rows.append([
            {"text": f"💱 {c}", "callback_data": f"currency:set:{c}", "style": "primary"}
            for c in items[i:i + 3]
        ])
    rows.append([{"text": "⬅️ Back", "callback_data": "menu:country"}])
    return {"inline_keyboard": rows}


def plans_keyboard(display_currency="KES"):
    with connect_db() as db:
        plans = db.execute("SELECT * FROM plans ORDER BY price").fetchall()

    style_map = {"daily": "success", "weekly": "primary", "monthly": "primary", "premium": "danger", "lifetime": "danger"}
    rows = []
    for p in plans:
        duration = "Lifetime" if p["is_lifetime"] else f"{p['days']}d"
        title = p["title"] or f"{p['name'].title()} VIP"
        price_str, _ = format_converted_price(p["price"], display_currency)
        rows.append([{
            "text": f"👑 {title} ({duration}) — {price_str}",
            "callback_data": f"plan:{p['name']}",
            "style": style_map.get(p["name"], "primary"),
        }])
    rows.append([{"text": "🌍 Country & Currency Settings", "callback_data": "menu:country", "style": "primary"}])
    rows.append([{"text": "🏠 Main Menu", "callback_data": "menu:home"}])
    return {"inline_keyboard": rows}


def get_reaction_counts(channel_msg_id):
    counts = {e: 0 for e in REACTION_EMOJIS}
    if not channel_msg_id:
        return counts
    with connect_db() as db:
        rows = db.execute(
            "SELECT emoji, COUNT(*) AS cnt FROM post_reactions WHERE channel_msg_id=? GROUP BY emoji",
            (channel_msg_id,),
        ).fetchall()
    for r in rows:
        if r["emoji"] in counts:
            counts[r["emoji"]] = int(r["cnt"])
    return counts


def save_channel_post_meta(channel_msg_id, selected_btn_ids, include_reactions, include_footer):
    with connect_db() as db:
        db.execute(
            """
            INSERT INTO channel_posts(channel_msg_id,button_ids,include_reactions,include_footer,created_at)
            VALUES(?,?,?,?,?)
            ON CONFLICT(channel_msg_id) DO UPDATE SET
                button_ids=excluded.button_ids,
                include_reactions=excluded.include_reactions,
                include_footer=excluded.include_footer
            """,
            (
                int(channel_msg_id),
                json.dumps([int(x) for x in (selected_btn_ids or [])]),
                1 if include_reactions else 0,
                1 if include_footer else 0,
                utcnow().isoformat(),
            ),
        )


def channel_post_keyboard(channel_msg_id=None, selected_btn_ids=None, include_footer=True, include_reactions=True):
    if channel_msg_id and selected_btn_ids is None:
        with connect_db() as db:
            meta = db.execute("SELECT * FROM channel_posts WHERE channel_msg_id=?", (int(channel_msg_id),)).fetchone()
        if meta:
            try:
                selected_btn_ids = json.loads(meta["button_ids"])
            except Exception:
                selected_btn_ids = []
            include_reactions = bool(meta["include_reactions"])
            include_footer = bool(meta["include_footer"])

    contact_url = get_setting("admin_contact_url", f"https://t.me/{BRAND_TAG.lstrip('@')}")
    join_url = f"https://t.me/{bot_username}?start=vip" if bot_username else contact_url
    website_url = get_website_url()

    rows = []
    if include_reactions:
        counts = get_reaction_counts(channel_msg_id)
        rows.append([
            {"text": f"{e} {counts[e]}", "callback_data": f"react:{idx}"}
            for idx, e in enumerate(REACTION_EMOJIS[:3])
        ])
        rows.append([
            {"text": f"{e} {counts[e]}", "callback_data": f"react:{idx + 3}"}
            for idx, e in enumerate(REACTION_EMOJIS[3:])
        ])

    if selected_btn_ids:
        with connect_db() as db:
            placeholders = ",".join(["?"] * len(selected_btn_ids))
            btns = db.execute(
                f"SELECT * FROM custom_buttons WHERE id IN ({placeholders}) AND is_active=1 ORDER BY id",
                tuple(int(x) for x in selected_btn_ids),
            ).fetchall()
        btn_map = {int(b["id"]): b for b in btns}
        for bid in selected_btn_ids:
            b = btn_map.get(int(bid))
            if not b:
                continue
            style = b["style"] if b["style"] in BUTTON_STYLES else "primary"
            if b["button_type"] == "URL" and b["url"]:
                target_url = b["url"]
            elif bot_username:
                target_url = f"https://t.me/{bot_username}?start=btn_{b['id']}"
            else:
                target_url = contact_url
            rows.append([{"text": b["label"], "url": target_url, "style": style}])

    if include_footer:
        rows.append([
            {"text": "👑 JOIN VIP NOW", "url": join_url, "style": "danger"},
            {"text": "💬 Contact Admin", "url": contact_url, "style": "success"},
        ])
        if website_url:
            short_domain = re.sub(r"^https?://", "", website_url).rstrip("/")
            rows.append([{"text": f"🌐 {short_domain}", "url": website_url, "style": "primary"}])

    return {"inline_keyboard": rows} if rows else None


def admin_keyboard(user_id):
    if not is_admin(user_id):
        return {"inline_keyboard": []}
    rows = [
        [
            {"text": "📊 Overview & Stats", "callback_data": "admin:stats", "style": "primary"},
            {"text": "⚽ Fixtures & Matches", "callback_data": "admin:matches", "style": "success"},
        ],
        [
            {"text": "🎯 Accumulator Tickets", "callback_data": "admin:accums", "style": "primary"},
            {"text": "🧾 Transactions", "callback_data": "admin:transactions", "style": "success"},
        ],
        [
            {"text": "👥 Users & VIPs", "callback_data": "admin:users", "style": "primary"},
            {"text": "🎛️ Channel Buttons & Tickets", "callback_data": "admin:buttons", "style": "primary"},
        ],
        [
            {"text": "📝 Post to Main Channel", "callback_data": "admin:post_channel", "style": "success"},
            {"text": "💎 Post to VIP Channel", "callback_data": "admin:post_vip_channel", "style": "danger"},
        ],
        [
            {"text": "📢 VIP Promo Post", "callback_data": "admin:post", "style": "primary"},
            {"text": "📣 Broadcast", "callback_data": "admin:broadcast", "style": "danger"},
        ],
        [
            {"text": "💰 Manage VIP Plans", "callback_data": "admin:packages", "style": "success"},
            {"text": "⚙️ Channels & Payment Settings", "callback_data": "admin:channels", "style": "primary"},
        ],
        [
            {"text": "📥 Export CSV", "callback_data": "admin:export", "style": "success"},
            {"text": "🛡️ Audit Logs", "callback_data": "admin:audit", "style": "primary"},
        ],
        [{"text": "🏠 Main Menu", "callback_data": "menu:home"}],
    ]
    return {"inline_keyboard": rows}


# --------------------------------------------------
# PAYHERO KENYA API v2 (FIXED AUTH, CHANNEL SYNC, DIAGNOSTICS & CALLBACKS)
# --------------------------------------------------

def payhero_headers():
    if PAYHERO_API_USERNAME and PAYHERO_API_PASSWORD and not PAYHERO_AUTH_TOKEN:
        raw_creds = f"{PAYHERO_API_USERNAME}:{PAYHERO_API_PASSWORD}".encode("utf-8")
        return {"Authorization": "Basic " + base64.b64encode(raw_creds).decode("utf-8"), "Content-Type": "application/json"}

    token = PAYHERO_AUTH_TOKEN.strip().strip('"').strip("'").strip()
    token = re.sub(r"^(basic|bearer)\s+", "", token, flags=re.IGNORECASE).strip()
    if ":" in token:
        encoded = base64.b64encode(token.encode("utf-8")).decode("utf-8")
        authorization = f"Basic {encoded}"
    else:
        authorization = f"Basic {token}"
    return {"Authorization": authorization, "Content-Type": "application/json"}


def normalize_phone(phone):
    phone = re.sub(r"[\s+\-()]", "", phone)
    if phone.startswith("2540") and len(phone) == 13:
        phone = "0" + phone[4:]
    elif phone.startswith("254") and len(phone) == 12:
        phone = "0" + phone[3:]
    elif (phone.startswith("7") or phone.startswith("1")) and len(phone) == 9:
        phone = "0" + phone
    return phone if re.fullmatch(r"0[17]\d{8}", phone) else None


def get_plan(plan_name):
    with connect_db() as db:
        return db.execute("SELECT * FROM plans WHERE name=?", (plan_name,)).fetchone()


def recent_stk_cooldown_remaining(user_id):
    with connect_db() as db:
        last = db.execute(
            "SELECT created_at FROM orders WHERE user_id=? AND provider='mpesa' ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    if not last:
        return 0
    return max(0, int(STK_COOLDOWN_SECONDS - (utcnow() - parse_utc(last["created_at"])).total_seconds()))


async def send_manual_till_fallback(user_id, external_ref, item_title, amount, reason_note=""):
    till_no = get_setting("mpesa_till_number", DEFAULT_MPESA_TILL)
    with connect_db() as db:
        db.execute("UPDATE orders SET provider='mpesa_till', status='PENDING_PROOF' WHERE external_ref=?", (external_ref,))
    set_session(user_id, "WAIT_PAYMENT_PROOF", external_ref)

    prefix = f"⚠️ <i>{html_escape(reason_note)}</i>\n\n" if reason_note else ""
    await send_message(
        user_id,
        f"{prefix}"
        f"🏪 <b>PAY VIA M-PESA TILL NUMBER {html_escape(till_no)}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎫 Item: <b>{html_escape(item_title)}</b>\n"
        f"💰 Amount: <b>KES {int(amount):,}</b>\n"
        f"🔢 Till Number (Buy Goods): <code>{html_escape(till_no)}</code>\n"
        f"🔗 Order Ref: <code>{external_ref}</code>\n\n"
        f"📝 <b>Steps:</b>\n"
        f"1️⃣ Go to <b>M-Pesa → Lipa na M-Pesa → Buy Goods and Services</b>\n"
        f"2️⃣ Enter Till Number: <code>{html_escape(till_no)}</code>\n"
        f"3️⃣ Enter Amount: <b>KES {int(amount):,}</b> & enter your M-Pesa PIN\n\n"
        f"📸 <b>After paying, send your payment screenshot (photo or PDF/file) right here</b> for confirmation & automatic delivery!",
        {
            "inline_keyboard": [
                [{"text": "📸 Send Payment Screenshot / PDF", "callback_data": f"proof:{external_ref}", "style": "success"}],
                [{"text": f"💬 Contact {BRAND_TAG}", "url": get_setting("admin_contact_url"), "style": "primary"}],
                [{"text": "🏠 Main Menu", "callback_data": "menu:home"}],
            ]
        },
    )


async def create_manual_order_and_prompt(user_id, plan_name, method_key):
    offer = resolve_offer(plan_name)
    if not offer:
        await send_message(user_id, "⚠️ Selected package/ticket is currently unavailable.")
        return

    prefs = get_user_prefs(user_id)
    local_str, _ = format_converted_price(offer["price"], prefs["currency"])
    external_ref = "MAN" + secrets.token_hex(6).upper()
    amount = int(offer["price"])

    with connect_db() as db:
        db.execute(
            """
            INSERT INTO orders(external_ref,user_id,plan,amount,currency,local_amount,phone,provider,status,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (external_ref, user_id, offer["key"], amount, prefs["currency"], local_str, method_key, method_key, "PENDING_PROOF", utcnow().isoformat()),
        )

    set_session(user_id, "WAIT_PAYMENT_PROOF", external_ref)

    if method_key == "mpesa_till":
        await send_manual_till_fallback(user_id, external_ref, offer["title"], amount)
        return

    if method_key == "airtel":
        airtel_no = get_setting("airtel_money_number", DEFAULT_AIRTEL_NUMBER)
        await send_message(
            user_id,
            f"🔴 <b>AIRTEL MONEY PAYMENT</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 Item: <b>{html_escape(offer['title'])}</b>\n"
            f"💰 Amount: <b>{html_escape(local_str)}</b>\n"
            f"📞 Airtel Money Number: <code>{html_escape(airtel_no)}</code>\n"
            f"🔗 Order Ref: <code>{external_ref}</code>\n\n"
            f"📝 <b>How to Pay (Tanzania / Uganda / Zambia / Kenya):</b>\n"
            f"1️⃣ Send <b>{html_escape(local_str)}</b> via Airtel Money to <code>{html_escape(airtel_no)}</code>\n"
            f"2️⃣ <b>Send your payment screenshot (photo or PDF/file) right here</b> after paying for confirmation & automatic delivery!",
            {
                "inline_keyboard": [
                    [{"text": "📸 Send Payment Screenshot / PDF", "callback_data": f"proof:{external_ref}", "style": "success"}],
                    [{"text": f"💬 Contact {BRAND_TAG}", "url": get_setting("admin_contact_url"), "style": "primary"}],
                    [{"text": "🏠 Main Menu", "callback_data": "menu:home"}],
                ]
            },
        )
        return

    await send_message(
        user_id,
        f"💳 <b>{html_escape(method_key.upper())} PAYMENT</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎫 Item: <b>{html_escape(offer['title'])}</b>\n"
        f"💰 Amount: <b>{html_escape(local_str)}</b>\n"
        f"🔗 Order Ref: <code>{external_ref}</code>\n\n"
        f"1️⃣ Contact Admin <b>{BRAND_TAG}</b> for payment details.\n"
        f"2️⃣ <b>Send your payment screenshot (photo or PDF/file)</b> (or <code>Phone/Email | TransactionID</code>) right here after paying!",
        {
            "inline_keyboard": [
                [{"text": f"💬 Contact {BRAND_TAG}", "url": get_setting("admin_contact_url"), "style": "primary"}],
                [{"text": "📸 Send Payment Screenshot / PDF", "callback_data": f"proof:{external_ref}", "style": "success"}],
                [{"text": "🏠 Main Menu", "callback_data": "menu:home"}],
            ]
        },
    )


async def fetch_payhero_diagnostics():
    client = await get_http()
    headers = payhero_headers()
    ch_res, wal_res = {}, {}
    try:
        r1 = await client.get(PAYHERO_URL + "/payment_channels", headers=headers, params={"is_active": "true"}, timeout=20)
        ch_res = {"status": r1.status_code, "data": r1.json() if r1.headers.get("content-type", "").startswith("application/json") else r1.text[:300]}
    except Exception as e:
        ch_res = {"status": 0, "error": str(e)}
    try:
        r2 = await client.get(PAYHERO_URL + "/wallets", headers=headers, params={"wallet_type": "service_wallet"}, timeout=20)
        wal_res = {"status": r2.status_code, "data": r2.json() if r2.headers.get("content-type", "").startswith("application/json") else r2.text[:300]}
    except Exception as e:
        wal_res = {"status": 0, "error": str(e)}
    return {"channels": ch_res, "wallet": wal_res}


async def start_mpesa_payment(user_id, plan_name, phone, notify_telegram=True):
    offer = resolve_offer(plan_name)
    if not offer:
        if notify_telegram:
            await send_message(user_id, "⚠️ Selected package/ticket is currently unavailable.")
        return {"ok": False, "error": "Offer unavailable"}

    wait_sec = recent_stk_cooldown_remaining(user_id)
    if wait_sec > 0:
        msg = f"Please wait {wait_sec}s before requesting another M-Pesa STK prompt."
        if notify_telegram:
            await send_message(user_id, f"⏳ {msg}")
        return {"ok": False, "error": msg}

    external_ref = "PTE" + secrets.token_hex(7).upper()
    amount = max(1, int(offer["price"]))
    raw_ch = get_setting("payhero_channel_id", PAYHERO_CHANNEL_ID)
    if not str(raw_ch).isdigit():
        raw_ch = PAYHERO_CHANNEL_ID
    ph_channel = int(raw_ch)
    item_title = offer["title"]

    db_user = get_user(user_id)
    cust_name = (db_user["first_name"] if db_user and db_user["first_name"] else "Prime Tips Member")[:40]

    with connect_db() as db:
        db.execute(
            """
            INSERT INTO orders(external_ref,user_id,plan,amount,currency,local_amount,phone,provider,status,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (external_ref, user_id, offer["key"], amount, "KES", f"KES {amount:,}", phone, "mpesa", "PENDING", utcnow().isoformat()),
        )

    payload = {
        "amount": amount,
        "phone_number": phone,
        "channel_id": ph_channel,
        "provider": "m-pesa",
        "external_reference": external_ref,
        "customer_name": cust_name,
        "callback_url": PUBLIC_URL.rstrip("/") + "/payhero/callback",
    }
    if PAYHERO_CREDENTIAL_ID:
        payload["credential_id"] = PAYHERO_CREDENTIAL_ID

    try:
        client = await get_http()
        response = await client.post(PAYHERO_URL + "/payments", headers=payhero_headers(), json=payload)
        try:
            result = response.json()
        except Exception:
            result = {"raw": response.text[:350]}

        is_ok = (response.status_code in (200, 201)) and (bool(result.get("success")) or bool(result.get("reference")))
        if not is_ok:
            log.error("PayHero STK failed (HTTP %s, channel=%s): %s", response.status_code, ph_channel, result)
            err_msg = result.get("error_message") or result.get("message") or result.get("detail") or str(result)[:220]
            try:
                await send_message(
                    ADMIN_USER_ID,
                    f"⚠️ <b>PayHero STK Push Failed (HTTP {response.status_code})</b>\n"
                    f"Channel ID used: <code>{ph_channel}</code>\n"
                    f"Phone: <code>{html_escape(phone)}</code> | Amount: <b>KES {amount}</b>\n"
                    f"Error: <code>{html_escape(str(err_msg))}</code>\n\n"
                    "💡 Open Admin → Channels & Payment Settings → <b>🩺 PayHero Diagnostics</b> to inspect your auth token, active channel IDs, and service wallet balance.",
                )
            except Exception:
                pass
            if notify_telegram:
                await send_manual_till_fallback(
                    user_id,
                    external_ref,
                    item_title,
                    amount,
                    reason_note=f"Instant STK Push could not be reached — use Manual Till {get_setting('mpesa_till_number', DEFAULT_MPESA_TILL)} below!",
                )
            return {"ok": False, "fallback": "till", "external_ref": external_ref, "error": str(err_msg)}

        reference = result.get("reference")
        checkout_id = result.get("CheckoutRequestID") or result.get("checkout_request_id")
        if not reference:
            log.error("PayHero response missing reference: %s", result)
            if notify_telegram:
                await send_manual_till_fallback(
                    user_id,
                    external_ref,
                    item_title,
                    amount,
                    reason_note=f"Instant STK Push unavailable — use Manual Till {get_setting('mpesa_till_number', DEFAULT_MPESA_TILL)} below!",
                )
            return {"ok": False, "fallback": "till", "external_ref": external_ref, "error": "Missing PayHero reference"}

        with connect_db() as db:
            db.execute("UPDATE orders SET payhero_reference=?, checkout_id=? WHERE external_ref=?", (reference, checkout_id, external_ref))

        till_no = get_setting("mpesa_till_number", DEFAULT_MPESA_TILL)
        if notify_telegram:
            await send_message(
                user_id,
                "📲 <b>M-PESA STK PUSH SENT!</b>\n\n"
                f"🎫 Item: <b>{html_escape(item_title)}</b>\n"
                f"💳 Transaction Amount: <b>KES {amount:,}</b>\n"
                f"📞 Phone: <code>{html_escape(phone)}</code>\n"
                f"🔗 Ref: <code>{external_ref}</code>\n\n"
                "🔐 Enter your M-Pesa PIN on your phone. Your ticket/VIP activates automatically!\n"
                f"<i>If you don't see the PIN prompt, tap <b>Pay via Till {html_escape(till_no)}</b> or upload your screenshot/PDF below.</i>",
                {
                    "inline_keyboard": [
                        [{"text": "🔄 Verify Payment Status", "callback_data": f"verify:{external_ref}", "style": "success"}],
                        [{"text": f"🏪 Use Till {till_no} Instead", "callback_data": f"manualtill:{external_ref}", "style": "primary"}],
                        [{"text": "📸 Upload Payment Screenshot / PDF", "callback_data": f"proof:{external_ref}", "style": "primary"}],
                        [{"text": "🏠 Main Menu", "callback_data": "menu:home"}],
                    ]
                },
            )
        return {"ok": True, "external_ref": external_ref, "status": "PENDING"}
    except Exception as exc:
        log.exception("Payment initiation failed: %s", exc)
        if notify_telegram:
            await send_manual_till_fallback(
                user_id,
                external_ref,
                item_title,
                amount,
                reason_note=f"Instant STK Push timed out — use Manual Till {get_setting('mpesa_till_number', DEFAULT_MPESA_TILL)} below!",
            )
        return {"ok": False, "fallback": "till", "external_ref": external_ref, "error": "Payment initiation failed"}


async def verify_payhero_transaction(reference):
    client = await get_http()
    response = await client.get(
        PAYHERO_URL + "/transaction-status",
        headers=payhero_headers(),
        params={"reference": reference},
    )
    response.raise_for_status()
    return response.json()


# --------------------------------------------------
# CRYPTAPI INTERNATIONAL CRYPTO PAYMENTS (ALL SUPPORTED COINS & NETWORKS)
# --------------------------------------------------

def get_cryptapi_wallets_map():
    merged = {}
    env_map = {
        "evm": env_str("CRYPTAPI_EVM_ADDRESS"),
        "tron": env_str("CRYPTAPI_TRON_ADDRESS"),
        "sol": env_str("CRYPTAPI_SOL_ADDRESS"),
        "ton": env_str("CRYPTAPI_TON_ADDRESS"),
        "btc": env_str("CRYPTAPI_BTC_ADDRESS"),
        "ltc": env_str("CRYPTAPI_LTC_ADDRESS"),
        "bch": env_str("CRYPTAPI_BCH_ADDRESS"),
        "doge": env_str("CRYPTAPI_DOGE_ADDRESS"),
        "zec": env_str("CRYPTAPI_ZEC_ADDRESS"),
    }
    for k, v in env_map.items():
        if v:
            merged[k] = v
    if CRYPTAPI_WALLETS_JSON:
        try:
            parsed = json.loads(CRYPTAPI_WALLETS_JSON)
            if isinstance(parsed, dict):
                for k, v in parsed.items():
                    if v:
                        merged[str(k).lower().strip()] = str(v).strip()
        except Exception:
            log.warning("Invalid CRYPTAPI_WALLETS_JSON ignored")

    raw_db = get_setting("cryptapi_wallets", "{}")
    try:
        db_parsed = json.loads(raw_db)
        if isinstance(db_parsed, dict):
            for k, v in db_parsed.items():
                if v:
                    merged[str(k).lower().strip()] = str(v).strip()
    except Exception:
        pass
    return merged


def save_cryptapi_wallet(key, address):
    key = key.lower().strip()
    raw_db = get_setting("cryptapi_wallets", "{}")
    try:
        data = json.loads(raw_db) if raw_db else {}
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    if address and address.strip():
        data[key] = address.strip()
    else:
        data.pop(key, None)
    set_setting("cryptapi_wallets", json.dumps(data))


def get_wallet_for_ticker(ticker):
    t = (ticker or "").lower().strip()
    wallets = get_cryptapi_wallets_map()
    if t in wallets and wallets[t]:
        return wallets[t]

    network = t.split("/", 1)[0] if "/" in t else "coins"
    if t in ("btc", "bch", "ltc", "doge", "zec"):
        return wallets.get(t, "")
    if t in ("trx",) or network == "trc20":
        return wallets.get("tron", "")
    if t in ("sol",) or network == "sol":
        return wallets.get("sol", "")
    if t in ("ton",) or network == "ton":
        return wallets.get("ton", "")
    if t == "eth" or network in ("erc20", "bep20", "bsc", "polygon", "arbitrum", "optimism", "base", "avax-c", "avax", "linea", "monad", "berachain", "bera"):
        return wallets.get("evm", "")
    return wallets.get(network, "")


def _parse_cryptapi_info_payload(raw):
    coins = {}
    networks = {}

    def add_coin(ticker_guess, info_dict, net_key):
        t = (info_dict.get("ticker") or ticker_guess or "").lower().strip()
        if not t:
            return
        name = info_dict.get("coin") or t.upper()
        min_coin = info_dict.get("minimum_transaction_coin")
        if min_coin is None and info_dict.get("minimum_transaction") is not None:
            min_coin = info_dict.get("minimum_transaction")
        prices = info_dict.get("prices") or {}
        usd_price = None
        if isinstance(prices, dict) and prices.get("USD") is not None:
            try:
                usd_price = float(prices["USD"])
            except (ValueError, TypeError):
                usd_price = None
        entry = {
            "ticker": t,
            "name": str(name),
            "network": net_key,
            "min_coin": float(min_coin) if min_coin not in (None, "") else 0.0,
            "usd_price": usd_price,
            "fee_percent": info_dict.get("fee_percent"),
        }
        coins[t] = entry
        networks.setdefault(net_key, []).append(entry)

    for top_k, top_v in (raw or {}).items():
        if top_k in ("fee_tiers", "status", "minimum_fee") or not isinstance(top_v, dict):
            continue
        if "coin" in top_v or "minimum_transaction_coin" in top_v or "ticker" in top_v:
            add_coin(top_k, top_v, "coins")
        else:
            for sub_k, sub_v in top_v.items():
                if isinstance(sub_v, dict) and ("coin" in sub_v or "minimum_transaction_coin" in sub_v or "ticker" in sub_v):
                    add_coin(f"{top_k}/{sub_k}", sub_v, top_k)

    for net_k in networks:
        networks[net_k].sort(key=lambda x: x["name"])
    return coins, networks


async def fetch_cryptapi_catalog(force=False):
    now = utcnow()
    fetched_at = _cryptapi_catalog_cache.get("fetched_at")
    if not force and fetched_at and _cryptapi_catalog_cache["coins"]:
        age_min = (now - fetched_at).total_seconds() / 60.0
        if age_min < CRYPTAPI_CACHE_MINUTES:
            return _cryptapi_catalog_cache["coins"], _cryptapi_catalog_cache["networks"]

    try:
        client = await get_http()
        resp = await client.get(f"{CRYPTAPI_URL}/info/", params={"prices": "1"}, timeout=20)
        if resp.status_code == 200:
            raw = resp.json()
            coins, networks = _parse_cryptapi_info_payload(raw)
            if coins:
                _cryptapi_catalog_cache["fetched_at"] = now
                _cryptapi_catalog_cache["coins"] = coins
                _cryptapi_catalog_cache["networks"] = networks
                return coins, networks
    except Exception as e:
        log.warning("CryptAPI /info/ fetch failed: %s", e)

    if _cryptapi_catalog_cache["coins"]:
        return _cryptapi_catalog_cache["coins"], _cryptapi_catalog_cache["networks"]

    fallback_list = [
        ("btc", "Bitcoin", "coins", 0.00008),
        ("eth", "Ethereum", "coins", 0.0015),
        ("ltc", "Litecoin", "coins", 0.002),
        ("bch", "Bitcoin Cash", "coins", 0.0005),
        ("doge", "Dogecoin", "coins", 10.0),
        ("zec", "Zcash", "coins", 0.0025),
        ("trx", "TRON", "coins", 10.0),
        ("trc20/usdt", "USDT (TRC20)", "trc20", 1.0),
        ("bep20/usdt", "USDT (BEP20)", "bep20", 1.0),
        ("erc20/usdt", "USDT (ERC20)", "erc20", 5.0),
        ("polygon/usdt", "USDT (Polygon)", "polygon", 1.0),
        ("sol/usdt", "USDT (Solana)", "sol", 1.0),
        ("ton/usdt", "USDT (TON)", "ton", 1.0),
        ("sol/sol", "Solana", "sol", 0.01),
        ("ton/ton", "Toncoin", "ton", 0.2),
        ("bep20/bnb", "BNB (BEP20)", "bep20", 0.002),
        ("polygon/pol", "POL (Polygon)", "polygon", 1.0),
    ]
    coins, networks = {}, {}
    for t, name, net, mn in fallback_list:
        e = {"ticker": t, "name": name, "network": net, "min_coin": mn, "usd_price": None, "fee_percent": "1.0"}
        coins[t] = e
        networks.setdefault(net, []).append(e)
    return coins, networks


def get_cryptapi_callback_base():
    base = get_setting("cryptapi_callback_base", "").strip() or CRYPTAPI_CALLBACK_BASE_URL or PUBLIC_URL
    return base.rstrip("/")


async def cryptapi_convert_usd_to_coin(ticker, usd_amount):
    client = await get_http()
    resp = await client.get(
        f"{CRYPTAPI_URL}/{ticker}/convert/",
        params={"value": f"{usd_amount:.2f}", "from": "USD"},
        timeout=20,
    )
    data = resp.json()
    if resp.status_code != 200 or data.get("status") == "error":
        raise RuntimeError(data.get("error") or f"Convert failed (HTTP {resp.status_code})")
    return float(data["value_coin"]), float(data.get("exchange_rate") or 0)


async def cryptapi_create_address(ticker, address_out, callback_url, confirmations=1):
    client = await get_http()
    resp = await client.get(
        f"{CRYPTAPI_URL}/{ticker}/create/",
        params={
            "callback": callback_url,
            "address": address_out,
            "pending": "1",
            "confirmations": str(confirmations),
            "post": "1",
        },
        timeout=25,
    )
    try:
        data = resp.json()
    except Exception:
        data = {"error": resp.text[:300]}
    if resp.status_code != 200 or data.get("status") == "error":
        err = data.get("error") or data.get("detail") or str(data)
        raise RuntimeError(err)
    return data


async def cryptapi_get_qrcode(ticker, address_in, value_coin=None):
    client = await get_http()
    params = {"address": address_in, "size": "512"}
    if value_coin is not None:
        params["value"] = str(value_coin)
    resp = await client.get(f"{CRYPTAPI_URL}/{ticker}/qrcode/", params=params, timeout=20)
    data = resp.json()
    if resp.status_code != 200 or not data.get("qr_code"):
        raise RuntimeError(data.get("error") or "QR generation failed")
    return base64.b64decode(data["qr_code"]), data.get("payment_uri", "")


async def cryptapi_get_logs(ticker, callback_url):
    client = await get_http()
    resp = await client.get(
        f"{CRYPTAPI_URL}/{ticker}/logs/",
        params={"callback": callback_url},
        timeout=20,
    )
    if resp.status_code != 200:
        return None
    return resp.json()


def build_cryptapi_callback_url(external_ref, nonce):
    base = get_cryptapi_callback_base()
    qs = urlencode({"order_ref": external_ref, "nonce": nonce})
    return f"{base}/cryptapi/callback?{qs}"


async def show_crypto_checkout_menu(chat_id, user_id, offer_key, view="home", page=0, message_id=None):
    coins, networks = await fetch_cryptapi_catalog()
    enabled_coins = {t: info for t, info in coins.items() if get_wallet_for_ticker(t)}

    if not enabled_coins:
        kb = {
            "inline_keyboard": [
                [{"text": f"💬 Contact {BRAND_TAG}", "url": get_setting("admin_contact_url"), "style": "primary"}],
                [{"text": "🏠 Main Menu", "callback_data": "menu:home"}],
            ]
        }
        await edit_or_send(
            chat_id,
            message_id,
            "⚠️ <b>Crypto payments are not configured yet.</b>\n\n"
            + ("Admin: Go to <b>Admin → Channels & Payment Settings → 🪙 CryptAPI Crypto Settings</b> to add your wallet addresses." if is_admin(user_id)
               else f"Please choose another payment method or contact <b>{BRAND_TAG}</b>."),
            kb,
        )
        return

    offer = resolve_offer(offer_key)
    if not offer:
        await send_message(chat_id, "⚠️ Selected item is unavailable.")
        return

    usd_est = kes_to_usd(offer["price"])
    rows = []

    if view == "home":
        pop_rows = []
        pop_list = [t for t in POPULAR_CRYPTO_TICKERS if t in enabled_coins]
        for i in range(0, len(pop_list), 2):
            pair = []
            for t in pop_list[i:i + 2]:
                c = enabled_coins[t]
                pair.append({"text": f"🪙 {c['name']}", "callback_data": f"cryptopay|{offer_key}|{t}", "style": "success"})
            pop_rows.append(pair)
        rows.extend(pop_rows)
        rows.append([
            {"text": "💵 All Stablecoins", "callback_data": f"cryptolist|{offer_key}|stable|0", "style": "primary"},
            {"text": "🌐 Browse All Networks", "callback_data": f"cryptolist|{offer_key}|nets|0", "style": "primary"},
        ])
        rows.append([{"text": "⬅️ Back", "callback_data": f"plan:{offer_key}" if not offer_key.startswith("btn:") else f"openbtn:{offer_key.split(':', 1)[1]}"}])
        await edit_or_send(
            chat_id,
            message_id,
            f"🪙 <b>CRYPTO CHECKOUT (CRYPTAPI)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 Item: <b>{html_escape(offer['title'])}</b>\n"
            f"💰 Price: <b>KES {offer['price']:,}</b> (~<b>${usd_est:.2f} USD</b>)\n"
            f"✅ Enabled Assets: <b>{len(enabled_coins)} coins & tokens</b>\n\n"
            "👇 Choose a coin or browse by network below (automatic blockchain confirmation):",
            {"inline_keyboard": rows},
        )
        return

    if view == "nets":
        active_nets = []
        for net_k, net_items in networks.items():
            cnt = sum(1 for item in net_items if item["ticker"] in enabled_coins)
            if cnt > 0:
                active_nets.append((net_k, cnt))
        for i in range(0, len(active_nets), 2):
            pair = []
            for nk, cnt in active_nets[i:i + 2]:
                lbl = CRYPTO_NETWORK_LABELS.get(nk, nk.upper())
                pair.append({"text": f"{lbl} ({cnt})", "callback_data": f"cryptolist|{offer_key}|net:{nk}|0", "style": "primary"})
            rows.append(pair)
        rows.append([{"text": "⬅️ Popular Coins", "callback_data": f"cryptolist|{offer_key}|home|0"}])
        await edit_or_send(
            chat_id,
            message_id,
            f"🌐 <b>ALL CRYPTAPI NETWORKS</b>\nItem: <b>{html_escape(offer['title'])}</b> (~${usd_est:.2f} USD)\n\nSelect a blockchain network:",
            {"inline_keyboard": rows},
        )
        return

    if view == "stable":
        items = [c for t, c in enabled_coins.items() if any(sym in t for sym in ("usdt", "usdc", "dai", "busd", "pyusd", "eurc", "usd1", "fdusd"))]
        items.sort(key=lambda x: x["name"])
        title = "💵 <b>ALL STABLECOINS</b>"
    elif view.startswith("net:"):
        net_k = view.split(":", 1)[1]
        items = [c for c in networks.get(net_k, []) if c["ticker"] in enabled_coins]
        title = f"🌐 <b>{html_escape(CRYPTO_NETWORK_LABELS.get(net_k, net_k.upper()))}</b>"
    else:
        items = sorted(enabled_coins.values(), key=lambda x: x["name"])
        title = "🪙 <b>ALL SUPPORTED COINS</b>"

    per_page = 12
    total_pages = max(1, (len(items) + per_page - 1) // per_page)
    page = max(0, min(int(page), total_pages - 1))
    slice_items = items[page * per_page:(page + 1) * per_page]

    for i in range(0, len(slice_items), 2):
        pair = []
        for c in slice_items[i:i + 2]:
            pair.append({"text": c["name"], "callback_data": f"cryptopay|{offer_key}|{c['ticker']}", "style": "success"})
        rows.append(pair)

    nav = []
    if page > 0:
        nav.append({"text": "⬅️ Prev", "callback_data": f"cryptolist|{offer_key}|{view}|{page - 1}"})
    if page < total_pages - 1:
        nav.append({"text": "Next ➡️", "callback_data": f"cryptolist|{offer_key}|{view}|{page + 1}"})
    if nav:
        rows.append(nav)
    rows.append([{"text": "⬅️ Back to Crypto Menu", "callback_data": f"cryptolist|{offer_key}|home|0"}])

    await edit_or_send(
        chat_id,
        message_id,
        f"{title} (Page {page + 1}/{total_pages})\n"
        f"Item: <b>{html_escape(offer['title'])}</b> (~${usd_est:.2f} USD)\n\nSelect coin/token to pay:",
        {"inline_keyboard": rows},
    )


async def start_cryptapi_payment(user_id, chat_id, offer_key, ticker):
    offer = resolve_offer(offer_key)
    if not offer:
        await send_message(chat_id, "⚠️ Selected package/ticket is unavailable.")
        return

    ticker = ticker.lower().strip()
    address_out = get_wallet_for_ticker(ticker)
    if not address_out:
        await send_message(chat_id, "⚠️ No receiving wallet is configured for this coin yet.")
        return

    coins, _ = await fetch_cryptapi_catalog()
    coin_meta = coins.get(ticker, {"name": ticker.upper(), "min_coin": 0.0, "ticker": ticker})

    usd_amount = kes_to_usd(offer["price"])
    if usd_amount <= 0:
        await send_message(chat_id, "⚠️ Invalid order amount.")
        return

    try:
        value_coin, _ = await cryptapi_convert_usd_to_coin(ticker, usd_amount)
    except Exception as e:
        await send_message(chat_id, f"⚠️ Could not convert price to {html_escape(coin_meta['name'])}: <code>{html_escape(str(e))}</code>")
        return

    min_coin = float(coin_meta.get("min_coin") or 0)
    if min_coin > 0 and value_coin < min_coin:
        await send_message(
            chat_id,
            f"⚠️ <b>Order below network minimum for {html_escape(coin_meta['name'])}</b>\n\n"
            f"• Required order amount: <code>{value_coin:.8f}</code> (~${usd_amount:.2f})\n"
            f"• CryptAPI network minimum: <code>{min_coin}</code>\n\n"
            "💡 Please choose a lower-fee network like <b>USDT (TRC20 / BEP20 / Polygon / Solana / TON)</b>, <b>LTC</b>, <b>TRX</b>, or <b>DOGE</b>!",
            {"inline_keyboard": [[{"text": "🔄 Choose Another Coin", "callback_data": f"cryptolist|{offer_key}|home|0", "style": "primary"}]]},
        )
        return

    external_ref = "CRY" + secrets.token_hex(6).upper()
    nonce = secrets.token_hex(12)
    callback_url = build_cryptapi_callback_url(external_ref, nonce)
    confirmations = int(get_setting("cryptapi_confirmations", "1") or "1")

    try:
        created = await cryptapi_create_address(ticker, address_out, callback_url, confirmations=confirmations)
    except Exception as e:
        err_s = str(e)
        log.error("CryptAPI create failed for %s: %s", ticker, err_s)
        if "verify your domain" in err_s.lower():
            try:
                await send_message(
                    ADMIN_USER_ID,
                    f"🚨 <b>CryptAPI Domain Verification Required!</b>\n\n"
                    f"Callback base: <code>{html_escape(get_cryptapi_callback_base())}</code>\n"
                    "Verify your domain once at <b>https://cryptapi.io/verify_domain/</b> (via DNS TXT or HTTPS file), then CryptAPI will activate immediately!",
                )
            except Exception:
                pass
        await send_message(
            chat_id,
            f"⚠️ Could not create crypto deposit address right now: <code>{html_escape(err_s[:180])}</code>\n"
            "Please try another coin or contact admin.",
        )
        return

    address_in = created["address_in"]
    coin_amount_str = f"{value_coin:.8f}".rstrip("0").rstrip(".")
    if "." not in coin_amount_str:
        coin_amount_str += ".0"
    min_tx_display = created.get("minimum_transaction_coin") or min_coin

    with connect_db() as db:
        db.execute(
            """
            INSERT INTO orders(
                external_ref,user_id,plan,amount,currency,local_amount,phone,provider,status,
                crypto_ticker,crypto_address_in,crypto_amount,callback_nonce,created_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                external_ref, user_id, offer["key"], int(offer["price"]),
                "USD", f"${usd_amount:.2f} ({coin_amount_str} {coin_meta['name']})",
                ticker, "crypto", "PENDING",
                ticker, address_in, coin_amount_str, nonce, utcnow().isoformat(),
            ),
        )

    caption = (
        f"🪙 <b>PAY WITH {html_escape(coin_meta['name'].upper())}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎫 Item: <b>{html_escape(offer['title'])}</b>\n"
        f"💵 Fiat Value: <b>${usd_amount:.2f} USD</b> (KES {offer['price']:,})\n"
        f"💰 Send EXACT Amount: <code>{coin_amount_str}</code> <b>{html_escape(coin_meta['name'])}</b>\n"
        f"📥 Deposit Address:\n<code>{html_escape(address_in)}</code>\n\n"
        f"🔗 Order Ref: <code>{external_ref}</code>\n"
        f"⚠️ Minimum Deposit: <code>{min_tx_display}</code> (transfers below minimum cannot be forwarded).\n"
        f"⚡ <i>Activates automatically after {confirmations} blockchain confirmation(s)!</i>"
    )
    kb = {
        "inline_keyboard": [
            [{"text": "🔄 Check Crypto Payment", "callback_data": f"cryptocheck:{external_ref}", "style": "success"}],
            [{"text": "🔄 Switch Coin / Network", "callback_data": f"cryptolist|{offer_key}|home|0", "style": "primary"}],
            [{"text": "🏠 Main Menu", "callback_data": "menu:home"}],
        ]
    }

    try:
        qr_bytes, _ = await cryptapi_get_qrcode(ticker, address_in, value_coin=coin_amount_str)
        await telegram_send_photo_bytes(chat_id, f"{external_ref}.png", qr_bytes, caption=caption, keyboard=kb)
    except Exception:
        await send_message(chat_id, caption, kb)


async def process_cryptapi_event(order, payload_data):
    external_ref = order["external_ref"]
    pending = str(payload_data.get("pending", "0")) in ("1", "true", "True")
    txid_in = payload_data.get("txid_in") or ""
    tx_uuid = payload_data.get("uuid") or ""
    try:
        paid_coin = float(payload_data.get("value_coin") or 0)
    except (ValueError, TypeError):
        paid_coin = 0.0
    try:
        expected_coin = float(order["crypto_amount"] or 0)
    except (ValueError, TypeError):
        expected_coin = 0.0

    if pending:
        with connect_db() as db:
            db.execute("UPDATE orders SET crypto_txid=COALESCE(?, crypto_txid) WHERE external_ref=?", (txid_in or None, external_ref))
        try:
            await send_message(
                int(order["user_id"]),
                f"⏳ <b>CRYPTO PAYMENT DETECTED!</b>\n\n"
                f"🔗 Ref: <code>{external_ref}</code>\n"
                f"🧾 TxID: <code>{html_escape(txid_in or 'Pending')}</code>\n"
                "⛓️ Waiting for blockchain confirmation — your ticket/VIP will activate automatically!",
            )
        except Exception:
            pass
        return "PENDING_CONFIRMATION"

    # Confirmed (pending == 0)
    if expected_coin > 0 and paid_coin > 0 and paid_coin < (expected_coin * 0.97):
        with connect_db() as db:
            db.execute(
                "UPDATE orders SET status='PENDING_REVIEW', crypto_txid=?, crypto_uuid=?, mpesa_receipt=? WHERE external_ref=? AND status!='PAID'",
                (txid_in, tx_uuid, txid_in, external_ref),
            )
        try:
            await send_message(
                int(order["user_id"]),
                f"⚠️ <b>PARTIAL CRYPTO PAYMENT RECEIVED</b>\n\n"
                f"Expected: <code>{expected_coin}</code> | Received: <code>{paid_coin}</code>\n"
                f"Admin ({BRAND_TAG}) has been notified to review Ref <code>{external_ref}</code>.",
            )
            await send_message(
                ADMIN_USER_ID,
                f"⚠️ <b>UNDERPAID CRYPTO ORDER</b>\nRef: <code>{external_ref}</code> | User: <code>{order['user_id']}</code>\n"
                f"Expected: <code>{expected_coin}</code> | Received: <code>{paid_coin}</code> ({order['crypto_ticker']})\n"
                f"TxID: <code>{html_escape(txid_in)}</code>",
                {
                    "inline_keyboard": [[
                        {"text": "✅ Approve & Deliver Anyway", "callback_data": f"admin:approve:{external_ref}", "style": "success"},
                        {"text": "❌ Reject", "callback_data": f"admin:reject:{external_ref}", "style": "danger"},
                    ]]
                },
            )
        except Exception:
            pass
        return "UNDERPAID"

    with connect_db() as db:
        db.execute(
            "UPDATE orders SET crypto_txid=?, crypto_uuid=?, mpesa_receipt=? WHERE external_ref=?",
            (txid_in, tx_uuid, txid_in or f"CRYPTO-{external_ref}", external_ref),
        )
        fresh_order = db.execute("SELECT * FROM orders WHERE external_ref=?", (external_ref,)).fetchone()

    await finalize_paid_order(fresh_order, callback_data={"MpesaReceiptNumber": txid_in or f"CRYPTO-{external_ref}"}, manual_admin=True)
    return "PAID"


async def verify_cryptapi_order_via_logs(order):
    if not order["crypto_ticker"] or not order["callback_nonce"]:
        return "NOT_CRYPTO"
    cb_url = build_cryptapi_callback_url(order["external_ref"], order["callback_nonce"])
    logs_data = await cryptapi_get_logs(order["crypto_ticker"], cb_url)
    if not logs_data or logs_data.get("status") == "error":
        return "NO_TX"
    callbacks = logs_data.get("callbacks") or []
    if not callbacks:
        return "NO_TX"

    # Prefer confirmed callback (pending == 0)
    confirmed = [c for c in callbacks if str(c.get("pending", "1")) in ("0", "false", "False")]
    if confirmed:
        return await process_cryptapi_event(order, confirmed[-1])
    return await process_cryptapi_event(order, callbacks[-1])


# --------------------------------------------------
# VIP ACCESS, TICKET IMAGE DELIVERY, REFERRALS & ORDER FINALIZATION
# --------------------------------------------------

async def send_offer_ticket_media(user_id, offer_key, title, receipt=None):
    media_rows = get_offer_media(offer_key)
    if not media_rows:
        return 0
    receipt_txt = f"\n🧾 Receipt: <code>{html_escape(receipt)}</code>" if receipt else ""
    sent = 0
    for idx, m in enumerate(media_rows):
        cap = m["caption"].strip() if m["caption"] else f"🎫 <b>{html_escape(title)}</b> — Confirmed!{receipt_txt if idx == 0 else ''}"
        try:
            if m["media_type"] == "document":
                await telegram("sendDocument", {
                    "chat_id": user_id,
                    "document": m["file_id"],
                    "caption": cap[:1020],
                    "parse_mode": "HTML",
                })
            else:
                await telegram("sendPhoto", {
                    "chat_id": user_id,
                    "photo": m["file_id"],
                    "caption": cap[:1020],
                    "parse_mode": "HTML",
                })
            sent += 1
        except Exception:
            log.exception("Failed delivering ticket media %s to %s", m["id"], user_id)
    return sent


async def give_vip_access(user_id, plan_name, days_override=None, receipt=None, is_lifetime_override=False, title_override=None):
    if days_override is not None:
        days = int(days_override)
        display_title = title_override or f"{days}-Day VIP"
        is_lifetime = 1 if is_lifetime_override else 0
    else:
        offer = resolve_offer(plan_name)
        if not offer:
            return
        days = int(offer["days"])
        display_title = title_override or offer["title"]
        is_lifetime = int(offer["is_lifetime"])

    contact_url = get_setting("admin_contact_url", f"https://t.me/{BRAND_TAG.lstrip('@')}")
    receipt_line = f"🧾 Receipt: <code>{html_escape(receipt)}</code>\n" if receipt else ""

    delivered_media = await send_offer_ticket_media(user_id, plan_name, display_title, receipt=receipt)

    if days <= 0 and not is_lifetime:
        media_note = (
            f"🖼️ Delivered <b>{delivered_media}</b> ticket image(s) above!"
            if delivered_media > 0
            else f"✅ Payment confirmed! Admin ({BRAND_TAG}) has been notified."
        )
        await send_message(
            user_id,
            f"🎉 <b>PAYMENT CONFIRMED!</b>\n\n"
            f"👑 <b>{PROJECT_NAME} ({BRAND_TAG})</b>\n"
            f"🎫 Ticket: <b>{html_escape(display_title)}</b>\n"
            f"{receipt_line}\n"
            f"{media_note}",
            {
                "inline_keyboard": [
                    [{"text": f"💬 Contact {BRAND_TAG}", "url": contact_url, "style": "success"}],
                    [{"text": "🏠 Main Menu", "callback_data": "menu:home", "style": "primary"}],
                ]
            },
        )
        return

    with connect_db() as db:
        existing = db.execute("SELECT * FROM subscriptions WHERE user_id=?", (user_id,)).fetchone()

    now = utcnow()
    if is_lifetime:
        expiry = datetime(2099, 12, 31, 23, 59, tzinfo=timezone.utc)
    else:
        start = max(now, parse_utc(existing["expires_at"])) if (existing and existing["status"] == "ACTIVE" and not existing["is_lifetime"]) else now
        expiry = start + timedelta(days=days)

    with connect_db() as db:
        db.execute(
            """
            INSERT INTO subscriptions(user_id,plan,expires_at,status,is_lifetime,reminded)
            VALUES(?,?,?,'ACTIVE',?,0)
            ON CONFLICT(user_id) DO UPDATE SET
                plan=excluded.plan, expires_at=excluded.expires_at, status='ACTIVE',
                is_lifetime=excluded.is_lifetime, reminded=0
            """,
            (user_id, plan_name, expiry.isoformat(), is_lifetime),
        )

    vip_chat = get_setting("vip_channel_id", VIP_CHANNEL_ID)
    try:
        await telegram("unbanChatMember", {"chat_id": vip_chat, "user_id": user_id, "only_if_banned": True})
    except Exception:
        pass

    prefs = get_user_prefs(user_id)
    try:
        invite_payload = {"chat_id": vip_chat, "member_limit": 1, "name": f"Prime Tips Empire {user_id}"}
        if not is_lifetime:
            invite_payload["expire_date"] = int(expiry.timestamp())
        invite = await telegram("createChatInviteLink", invite_payload)
        exp_text = "Never (Lifetime)" if is_lifetime else format_local_time(expiry, prefs["tz"])
        media_line = f"🖼️ Sent <b>{delivered_media}</b> ticket image(s) above!\n" if delivered_media > 0 else ""

        await send_message(
            user_id,
            f"🎉 <b>VIP ACCESS ACTIVATED!</b>\n\n"
            f"👑 <b>{PROJECT_NAME} ({BRAND_TAG})</b>\n"
            f"💎 Item: <b>{html_escape(display_title)}</b>\n"
            f"{receipt_line}{media_line}"
            f"📅 Expires: <b>{exp_text}</b>\n\n"
            "👇 Tap below to enter the VIP channel:",
            {
                "inline_keyboard": [
                    [{"text": "👑 ENTER VIP CHANNEL", "url": invite["invite_link"], "style": "danger"}],
                    [{"text": "🏠 Main Menu", "callback_data": "menu:home", "style": "primary"}],
                ]
            },
        )
    except Exception:
        log.exception("Could not send VIP invite to %s", user_id)
        await send_message(ADMIN_USER_ID, f"⚠️ VIP activated for <code>{user_id}</code>, invite creation failed.")


async def reward_referrer_if_eligible(payer_user_id):
    payer = get_user(payer_user_id)
    if not payer or not payer["referred_by"] or payer["referral_rewarded"]:
        return
    referrer_id = int(payer["referred_by"])
    if referrer_id == int(payer_user_id) or not get_user(referrer_id):
        return

    with connect_db() as db:
        cur = db.execute(
            "UPDATE users SET referral_rewarded=1 WHERE user_id=? AND referral_rewarded=0",
            (int(payer_user_id),),
        )
        if cur.rowcount == 0:
            return

    try:
        reward_days = int(get_setting("referral_reward_days", "2") or "2")
    except ValueError:
        reward_days = 2

    if reward_days > 0:
        try:
            await give_vip_access(
                referrer_id,
                f"referral-{reward_days}d",
                days_override=reward_days,
                title_override=f"🎁 Referral Bonus (+{reward_days} Days VIP)",
            )
            await send_message(
                referrer_id,
                f"🎁 <b>REFERRAL REWARD EARNED!</b>\n"
                f"A member you invited just completed a payment — you received <b>+{reward_days} Day(s) of VIP Access</b>!",
            )
            log_audit(referrer_id, "REFERRAL_REWARD", f"from_user={payer_user_id} +{reward_days}d")
        except Exception:
            log.exception("Referral payout failed for referrer %s", referrer_id)


def _extract_payhero_status(verified):
    if not isinstance(verified, dict):
        return ""
    nested = verified.get("data") if isinstance(verified.get("data"), dict) else {}
    raw = (
        verified.get("status")
        or verified.get("transaction_status")
        or verified.get("Status")
        or nested.get("status")
        or nested.get("transaction_status")
        or ""
    )
    return str(raw).upper().strip()


async def finalize_paid_order(order, verified=None, callback_data=None, manual_admin=False):
    external_ref = order["external_ref"]
    if not manual_admin:
        status = _extract_payhero_status(verified)
        cb_status = str((callback_data or {}).get("Status") or (callback_data or {}).get("status") or "").upper()
        cb_rc = (callback_data or {}).get("ResultCode")

        if status in ("FAILED", "CANCELLED", "CANCELED", "EXPIRED") or (cb_rc not in (None, 0, "0") and cb_status in ("FAILED", "CANCELLED", "CANCELED", "")):
            with connect_db() as db:
                cur = db.execute("UPDATE orders SET status='FAILED' WHERE external_ref=? AND status='PENDING'", (external_ref,))
            if cur.rowcount > 0:
                try:
                    await send_message(
                        int(order["user_id"]),
                        "❌ <b>STK Push failed or was cancelled.</b>\nYou can still pay manually via Till <code>"
                        + html_escape(get_setting("mpesa_till_number", DEFAULT_MPESA_TILL))
                        + "</code> and send your screenshot, or pay via Crypto!",
                        {
                            "inline_keyboard": [
                                [{"text": f"🏪 Pay via Till {get_setting('mpesa_till_number', DEFAULT_MPESA_TILL)}", "callback_data": f"manualtill:{external_ref}", "style": "success"}],
                                [{"text": "🏠 Main Menu", "callback_data": "menu:home"}],
                            ]
                        },
                    )
                except Exception:
                    pass
            return "FAILED"
        if status not in ("SUCCESS", "COMPLETED", "PAID"):
            return "PENDING"
        if callback_data and callback_data.get("Amount") is not None:
            try:
                if int(float(callback_data["Amount"])) != int(order["amount"]):
                    return "PENDING"
            except (ValueError, TypeError):
                return "PENDING"

    v_nested = (verified or {}).get("data") if isinstance((verified or {}).get("data"), dict) else {}
    receipt = (
        (callback_data or {}).get("MpesaReceiptNumber")
        or (callback_data or {}).get("MPESA_Reference")
        or (verified or {}).get("provider_reference")
        or (verified or {}).get("transaction_reference")
        or v_nested.get("provider_reference")
        or order["mpesa_receipt"]
    )
    with connect_db() as db:
        cur = db.execute(
            "UPDATE orders SET status='PAID', mpesa_receipt=COALESCE(?, mpesa_receipt) WHERE external_ref=? AND status IN ('PENDING','PENDING_PROOF','PENDING_REVIEW')",
            (receipt, external_ref),
        )
        updated = cur.rowcount
    if updated == 0:
        return "ALREADY_PAID"

    offer = resolve_offer(order["plan"])
    item_title = offer["title"] if offer else format_order_item_name(order["plan"])

    if offer and offer["kind"] == "button":
        await give_vip_access(
            int(order["user_id"]),
            order["plan"],
            days_override=offer["days"],
            receipt=receipt,
            title_override=item_title,
        )
    else:
        await give_vip_access(int(order["user_id"]), order["plan"], receipt=receipt, title_override=item_title)

    await reward_referrer_if_eligible(int(order["user_id"]))

    amt_display = order["local_amount"] or f"KES {order['amount']}"
    await send_message(
        ADMIN_USER_ID,
        f"✅ <b>{PROJECT_NAME} PAYMENT CONFIRMED</b>\n\n"
        f"🆔 User: <code>{order['user_id']}</code>\n"
        f"💳 Method: <code>{html_escape(order['provider'].upper())}</code>\n"
        f"🎫 Item: <b>{html_escape(item_title)}</b> — {html_escape(amt_display)}\n"
        f"🧾 Receipt/Tx: <code>{html_escape(receipt or 'N/A')}</code>",
    )
    return "PAID"


async def process_payhero_callback(payload):
    data = payload.get("response") if isinstance(payload.get("response"), dict) else payload
    external_ref = data.get("ExternalReference") or data.get("external_reference") or payload.get("external_reference")
    checkout_id = data.get("CheckoutRequestID") or data.get("checkout_request_id") or payload.get("CheckoutRequestID")
    ph_ref = data.get("reference") or payload.get("reference")

    order = None
    with connect_db() as db:
        if external_ref:
            order = db.execute("SELECT * FROM orders WHERE external_ref=?", (external_ref,)).fetchone()
        if not order and checkout_id:
            order = db.execute("SELECT * FROM orders WHERE checkout_id=?", (checkout_id,)).fetchone()
        if not order and ph_ref:
            order = db.execute("SELECT * FROM orders WHERE payhero_reference=?", (ph_ref,)).fetchone()

    if not order or order["status"] == "PAID":
        return

    if not order["payhero_reference"]:
        # Only mark FAILED if PayHero explicitly reports failure
        if str(data.get("Status") or "").upper() == "FAILED" or data.get("ResultCode") not in (None, 0, "0"):
            await finalize_paid_order(order, verified={"status": "FAILED"}, callback_data=data)
        return

    try:
        verified = await verify_payhero_transaction(order["payhero_reference"])
    except Exception as e:
        log.warning("PayHero callback status check failed for %s: %s", order["external_ref"], e)
        if str(data.get("Status") or "").upper() == "FAILED" or data.get("ResultCode") not in (None, 0, "0"):
            await finalize_paid_order(order, verified={"status": "FAILED"}, callback_data=data)
        return

    # If PayHero callback arrived slightly ahead of transaction-status indexing, re-check once after 2s
    if _extract_payhero_status(verified) in ("QUEUED", "PENDING", "") and (
        str(data.get("Status") or "").upper() == "SUCCESS" or str(data.get("ResultCode")) == "0"
    ):
        await asyncio.sleep(2)
        try:
            verified = await verify_payhero_transaction(order["payhero_reference"])
        except Exception:
            pass

    await finalize_paid_order(order, verified=verified, callback_data=data)


async def handle_payment_proof_submission(user_id, chat_id, external_ref, text, photo_file_id, doc_file_id=None):
    with connect_db() as db:
        order = db.execute("SELECT * FROM orders WHERE external_ref=? AND user_id=?", (external_ref, user_id)).fetchone()
    if not order:
        await send_message(chat_id, "⚠️ Order not found. Please start checkout again.", main_menu_keyboard(user_id))
        return
    if order["status"] == "PAID":
        await send_message(chat_id, "✅ This order is already confirmed and delivered!", main_menu_keyboard(user_id))
        return

    media_fid = photo_file_id or doc_file_id
    media_kind = "photo" if photo_file_id else ("document" if doc_file_id else "text")

    if not media_fid and not text:
        await send_message(chat_id, "⚠️ Please send your payment screenshot (photo), PDF/file receipt, or transaction code.")
        return

    receipt_note = text[:120] if text else ("DOCUMENT" if doc_file_id else "SCREENSHOT")
    with connect_db() as db:
        db.execute(
            """
            UPDATE orders
            SET status='PENDING_REVIEW',
                proof_file_id=COALESCE(?, proof_file_id),
                proof_type=?,
                mpesa_receipt=?
            WHERE external_ref=?
            """,
            (media_fid, media_kind, receipt_note, external_ref),
        )
        order = db.execute("SELECT * FROM orders WHERE external_ref=?", (external_ref,)).fetchone()

    item_title = format_order_item_name(order["plan"])
    amt_display = order["local_amount"] or f"KES {order['amount']:,}"
    u = get_user(user_id)
    uname = f"@{u['username']}" if (u and u["username"]) else (u["first_name"] if u else str(user_id))

    auto_deliver = get_setting("auto_confirm_screenshot", "0") == "1" and bool(media_fid)

    admin_caption = (
        f"📸 <b>PAYMENT PROOF ({media_kind.upper()}) {'(AUTO-DELIVERED)' if auto_deliver else 'FOR CONFIRMATION'}</b>\n\n"
        f"👤 User: {html_escape(uname)} (<code>{user_id}</code>)\n"
        f"🎫 Item: <b>{html_escape(item_title)}</b>\n"
        f"💰 Amount: <b>{html_escape(amt_display)}</b>\n"
        f"💳 Method: <code>{html_escape(order['provider'].upper())}</code>\n"
        f"🔗 Ref: <code>{external_ref}</code>\n"
        + (f"📝 Note/Code: <code>{html_escape(text[:200])}</code>" if text else "")
    )

    admin_kb = (
        None
        if auto_deliver
        else {
            "inline_keyboard": [
                [
                    {"text": "✅ Confirm & Deliver Now", "callback_data": f"admin:approve:{external_ref}", "style": "success"},
                    {"text": "❌ Reject", "callback_data": f"admin:reject:{external_ref}", "style": "danger"},
                ],
                [{"text": "💬 Reply to User", "callback_data": f"admin:reply:{user_id}", "style": "primary"}],
            ]
        }
    )

    if photo_file_id:
        await telegram("sendPhoto", {
            "chat_id": ADMIN_USER_ID,
            "photo": photo_file_id,
            "caption": admin_caption[:1020],
            "parse_mode": "HTML",
            **({"reply_markup": admin_kb} if admin_kb else {}),
        })
    elif doc_file_id:
        await telegram("sendDocument", {
            "chat_id": ADMIN_USER_ID,
            "document": doc_file_id,
            "caption": admin_caption[:1020],
            "parse_mode": "HTML",
            **({"reply_markup": admin_kb} if admin_kb else {}),
        })
    else:
        await send_message(ADMIN_USER_ID, admin_caption, admin_kb)

    if auto_deliver:
        await finalize_paid_order(order, manual_admin=True)
    else:
        await send_message(
            chat_id,
            "✅ <b>PAYMENT PROOF RECEIVED!</b>\n\n"
            f"🎫 Item: <b>{html_escape(item_title)}</b>\n"
            f"🔗 Order Ref: <code>{external_ref}</code>\n\n"
            "⏳ Confirming your payment now — once confirmed, you will <b>automatically receive</b> your ticket images & VIP access right here!",
            {"inline_keyboard": [[{"text": "🏠 Main Menu", "callback_data": "menu:home", "style": "primary"}]]},
        )


# --------------------------------------------------
# MATCHES, ACCUMULATORS & CHANNEL PUBLISHING
# --------------------------------------------------

def format_match_block(m, index=None):
    badges = {"PENDING": "⏳ PENDING", "WON": "✅ WON", "LOST": "❌ LOST", "VOID": "⚪ VOID"}
    prefix = f"<b>#{index}</b> " if index else ""
    return (
        f"{prefix}⚽ <b>{html_escape(m['match_name'])}</b>\n"
        f"🎯 {html_escape(m['market'])}: <b>{html_escape(m['tip'])}</b>  |  📈 Odds: <b>{html_escape(m['odds'])}</b>\n"
        f"🗓 {html_escape(m['match_date'])} ⏰ {html_escape(m['kickoff_time'])}  •  {badges.get(m['result'], m['result'])}\n"
    )


def render_batch_text(tier, rows):
    vip_odds_info = get_setting("vip_odds_info")
    header = (
        f"💎 <b>{PROJECT_NAME} — VIP PICKS</b> ({BRAND_TAG})\n━━━━━━━━━━━━━━━━━━━━\n\n"
        if tier == "VIP"
        else f"⚽ <b>{PROJECT_NAME} — TODAY'S PREDICTIONS</b> ({BRAND_TAG})\n━━━━━━━━━━━━━━━━━━━━\n\n"
    )
    body = "\n".join(format_match_block(m, i + 1) for i, m in enumerate(rows))
    footer = f"\n━━━━━━━━━━━━━━━━━━━━\n🎯 {html_escape(vip_odds_info)}\n⚠️ <i>Football predictions involve risk. No guaranteed wins.</i>\n🔥 <b>{PROJECT_NAME} ({BRAND_TAG})</b>"
    return header + body + footer, body


async def sync_channel_message_for_match(match_id):
    with connect_db() as db:
        m = db.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
        if not m or not m["channel_msg_id"]:
            return
        msg_id, tier = m["channel_msg_id"], m["tier"]
        rows = db.execute("SELECT * FROM matches WHERE channel_msg_id=? ORDER BY id", (msg_id,)).fetchall()
    if not rows:
        return
    target_chat = get_setting("vip_channel_id", VIP_CHANNEL_ID) if tier == "VIP" else get_setting("main_channel_id", CHANNEL_ID)
    full_text, _ = render_batch_text(tier, rows)
    try:
        await telegram("editMessageText", {
            "chat_id": target_chat,
            "message_id": msg_id,
            "parse_mode": "HTML",
            "text": full_text[:4090],
            "reply_markup": channel_post_keyboard(channel_msg_id=msg_id, include_reactions=(tier == "FREE")),
            "disable_web_page_preview": True,
        })
    except Exception as e:
        log.warning("Could not sync channel msg %s: %s", msg_id, e)


async def publish_match_batch(tier, match_ids=None, selected_btn_ids=None, include_footer=True, include_reactions=True):
    await ensure_bot_username()
    with connect_db() as db:
        if match_ids:
            ph = ",".join(["?"] * len(match_ids))
            rows = db.execute(f"SELECT * FROM matches WHERE id IN ({ph}) ORDER BY id", tuple(match_ids)).fetchall()
        else:
            rows = db.execute("SELECT * FROM matches WHERE tier=? AND publish_status='QUEUED' ORDER BY id", (tier,)).fetchall()
    if not rows:
        return None

    target_chat = get_setting("vip_channel_id", VIP_CHANNEL_ID) if tier == "VIP" else get_setting("main_channel_id", CHANNEL_ID)
    full_text, _ = render_batch_text(tier, rows)
    show_reacts = bool(include_reactions) and (tier == "FREE")
    sel_btns = selected_btn_ids or []

    msg = await telegram("sendMessage", {
        "chat_id": target_chat,
        "parse_mode": "HTML",
        "text": full_text[:4090],
        "reply_markup": channel_post_keyboard(selected_btn_ids=sel_btns, include_footer=include_footer, include_reactions=False),
        "disable_web_page_preview": True,
    })
    msg_id = msg["message_id"]
    save_channel_post_meta(msg_id, sel_btns, show_reacts, include_footer)

    with connect_db() as db:
        for r in rows:
            db.execute("UPDATE matches SET publish_status='PUBLISHED', channel_msg_id=? WHERE id=?", (msg_id, r["id"]))

    if show_reacts:
        try:
            await telegram("editMessageReplyMarkup", {
                "chat_id": target_chat,
                "message_id": msg_id,
                "reply_markup": channel_post_keyboard(channel_msg_id=msg_id),
            })
        except Exception:
            pass
    return msg


async def publish_vip_post(selected_btn_ids=None, include_footer=True, include_reactions=True):
    await ensure_bot_username()
    main_chat = get_setting("main_channel_id", CHANNEL_ID)
    sel_btns = selected_btn_ids or []
    msg = await telegram("sendMessage", {
        "chat_id": main_chat,
        "parse_mode": "HTML",
        "text": (
            f"👑 <b>{PROJECT_NAME}</b> ({BRAND_TAG})\n\n"
            f"🎯 {html_escape(get_setting('vip_odds_info'))}\n\n"
            "⚡ Data-driven football analysis, VIP picks & accumulator tickets.\n"
            "🌍 Supports M-Pesa, Airtel Money & 100+ Cryptocurrencies (CryptAPI).\n\n"
            "👇 <b>TAP BELOW TO JOIN VIP OR GET A TICKET</b>"
        ),
        "reply_markup": channel_post_keyboard(selected_btn_ids=sel_btns, include_footer=include_footer, include_reactions=False),
    })
    msg_id = msg["message_id"]
    save_channel_post_meta(msg_id, sel_btns, include_reactions, include_footer)

    if include_reactions:
        try:
            await telegram("editMessageReplyMarkup", {
                "chat_id": main_chat,
                "message_id": msg_id,
                "reply_markup": channel_post_keyboard(channel_msg_id=msg_id),
            })
        except Exception:
            pass
    return msg


async def publish_custom_main_post(text_body="", photo_file_id=None, selected_btn_ids=None, include_footer=True, include_reactions=True):
    await ensure_bot_username()
    main_chat = get_setting("main_channel_id", CHANNEL_ID)
    sel_btns = selected_btn_ids or []
    formatted = f"{html_escape(text_body)}\n\n🔥 <b>{PROJECT_NAME}</b> ({BRAND_TAG})" if text_body else f"🔥 <b>{PROJECT_NAME}</b> ({BRAND_TAG})"

    kb = channel_post_keyboard(selected_btn_ids=sel_btns, include_footer=include_footer, include_reactions=False)
    if photo_file_id:
        payload = {"chat_id": main_chat, "photo": photo_file_id, "caption": formatted[:1020], "parse_mode": "HTML"}
        if kb:
            payload["reply_markup"] = kb
        msg = await telegram("sendPhoto", payload)
    else:
        payload = {"chat_id": main_chat, "parse_mode": "HTML", "text": formatted[:4090], "disable_web_page_preview": True}
        if kb:
            payload["reply_markup"] = kb
        msg = await telegram("sendMessage", payload)

    msg_id = msg["message_id"]
    save_channel_post_meta(msg_id, sel_btns, include_reactions, include_footer)

    if include_reactions:
        try:
            await telegram("editMessageReplyMarkup", {
                "chat_id": main_chat,
                "message_id": msg_id,
                "reply_markup": channel_post_keyboard(channel_msg_id=msg_id),
            })
        except Exception:
            pass
    return msg


async def publish_vip_channel_post(text_body="", photo_file_id=None):
    vip_chat = get_setting("vip_channel_id", VIP_CHANNEL_ID)
    formatted = (
        f"💎 <b>{PROJECT_NAME} — VIP EXCLUSIVE</b> ({BRAND_TAG})\n\n{html_escape(text_body)}"
        if text_body
        else f"💎 <b>{PROJECT_NAME} — VIP EXCLUSIVE</b> ({BRAND_TAG})"
    )
    if photo_file_id:
        return await telegram("sendPhoto", {
            "chat_id": vip_chat,
            "photo": photo_file_id,
            "caption": formatted[:1020],
            "parse_mode": "HTML",
        })
    return await telegram("sendMessage", {
        "chat_id": vip_chat,
        "text": formatted[:4090],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })


async def publish_accumulator_to_channel(accum_id, target="MAIN"):
    await ensure_bot_username()
    with connect_db() as db:
        a = db.execute("SELECT * FROM accumulators WHERE id=?", (int(accum_id),)).fetchone()
    if not a:
        return None

    try:
        sels = json.loads(a["selections_json"])
    except Exception:
        sels = []

    legs_text = "\n".join(
        f"<b>{i + 1}.</b> ⚽ <b>{html_escape(s.get('match', ''))}</b>\n"
        f"    🎯 Tip: <b>{html_escape(s.get('tip', ''))}</b>  |  📈 Odds: <b>{html_escape(str(s.get('odds', '')))}</b>"
        for i, s in enumerate(sels)
    )
    risk = a["risk_note"] or "Multi-leg accumulators carry higher variance (never guaranteed)."
    is_vip = (target.upper() == "VIP")
    target_chat = get_setting("vip_channel_id", VIP_CHANNEL_ID) if is_vip else get_setting("main_channel_id", CHANNEL_ID)

    msg_text = (
        f"🎯 <b>{PROJECT_NAME} — {'VIP ' if is_vip else ''}ACCUMULATOR TICKET #{a['id']}</b> ({BRAND_TAG})\n"
        f"📊 Category: <b>{html_escape(a['category'])}</b>  |  📈 Combined Odds: <b>{html_escape(str(a['total_odds']))}x</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{legs_text}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ <i>{html_escape(risk)}</i>\n"
        f"🔥 <b>{PROJECT_NAME} ({BRAND_TAG})</b>"
    )

    msg = await telegram("sendMessage", {
        "chat_id": target_chat,
        "parse_mode": "HTML",
        "text": msg_text[:4090],
        "reply_markup": channel_post_keyboard(selected_btn_ids=[], include_footer=not is_vip, include_reactions=False),
        "disable_web_page_preview": True,
    })
    msg_id = msg["message_id"]
    save_channel_post_meta(msg_id, [], not is_vip, not is_vip)

    if not is_vip:
        try:
            await telegram("editMessageReplyMarkup", {
                "chat_id": target_chat,
                "message_id": msg_id,
                "reply_markup": channel_post_keyboard(channel_msg_id=msg_id),
            })
        except Exception:
            pass

    new_status = "PUBLISHED_VIP" if is_vip else "PUBLISHED_MAIN"
    with connect_db() as db:
        db.execute("UPDATE accumulators SET status=?, channel_msg_id=? WHERE id=?", (new_status, msg_id, int(accum_id)))

    if int(a["created_by"]) != int(ADMIN_USER_ID):
        try:
            await send_message(
                int(a["created_by"]),
                f"🎉 Your Accumulator Ticket <b>#{a['id']}</b> ({html_escape(str(a['total_odds']))}x) was published to the <b>{'VIP' if is_vip else 'Main'} Channel</b>!",
            )
        except Exception:
            pass
    return msg


# --------------------------------------------------
# USER VIEWS (WELCOME, COUNTRY, TICKET CHECKOUT, ACCUM, VIP, STATS)
# --------------------------------------------------

async def show_country_welcome(chat_id):
    await send_message(
        chat_id,
        "🌍 <b>Welcome to Prime Tips Empire!</b>\n\n"
        "Choose your country to personalise your experience, currency and payment options.",
        country_welcome_keyboard(),
    )


async def show_main_menu(chat_id, user_id):
    prefs = get_user_prefs(user_id)
    c_info = COUNTRIES.get(prefs["country"])
    country_line = f"{c_info['flag']} {c_info['name']} ({prefs['currency']})" if (prefs["confirmed"] and c_info) else f"Not set ({prefs['currency']})"
    vip_badge = "💎 <b>VIP ACTIVE</b>" if has_active_vip(user_id) else "🆓 <b>FREE MEMBER</b>"
    await send_message(
        chat_id,
        f"👑 <b>{PROJECT_NAME}</b>  •  {BRAND_TAG}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Status: {vip_badge}  |  🌍 {country_line}\n"
        f"🎯 {html_escape(get_setting('vip_odds_info'))}\n\n"
        "⚽ <i>International Football Predictions & Analytics Platform</i>",
        main_menu_keyboard(user_id),
    )


async def show_ticket_button_checkout(chat_id, user_id, btn_id):
    b = get_custom_button(btn_id)
    if not b or not b["is_active"]:
        await send_message(chat_id, "⚠️ This ticket/button is no longer active.", main_menu_keyboard(user_id))
        return

    contact_url = get_setting("admin_contact_url", f"https://t.me/{BRAND_TAG.lstrip('@')}")
    till_no = get_setting("mpesa_till_number", DEFAULT_MPESA_TILL)
    airtel_no = get_setting("airtel_money_number", DEFAULT_AIRTEL_NUMBER)
    prefs = get_user_prefs(user_id)
    price = int(b["price"])
    days = int(b["days"])
    desc = (b["description"] or "").strip()

    if b["button_type"] == "URL" and b["url"]:
        await send_message(
            chat_id,
            f"🔗 <b>{html_escape(b['label'])}</b>\n\n{html_escape(desc) if desc else 'Tap below to open:'}",
            {
                "inline_keyboard": [
                    [{"text": b["label"], "url": b["url"], "style": "primary"}],
                    [{"text": "🏠 Main Menu", "callback_data": "menu:home"}],
                ]
            },
        )
        return

    price_line = f"KES {price:,}"
    if price > 0 and prefs["currency"] != "KES":
        conv, ok = format_converted_price(price, prefs["currency"])
        if ok:
            price_line = conv

    img_cnt = count_offer_media(f"btn:{b['id']}")
    days_line = f"⏳ Access Duration: <b>{days} Day{'s' if days != 1 else ''} VIP</b>\n" if days > 0 else "🎟️ Type: <b>Instant Ticket Image Delivery</b>\n"
    img_line = f"🖼️ Ready for Auto-Delivery: <b>{img_cnt} Ticket Image(s)</b>\n" if img_cnt > 0 else ""
    desc_line = f"📝 {html_escape(desc)}\n" if desc else ""

    kb_rows = []
    if price > 0:
        kb_rows.append([{"text": f"📲 Kenya: M-Pesa STK Push (KES {price:,})", "callback_data": f"buybtn:{b['id']}:mpesa", "style": "success"}])
        kb_rows.append([{"text": f"🏪 Kenya: Manual M-Pesa Till {till_no}", "callback_data": f"buybtn:{b['id']}:mpesa_till", "style": "primary"}])
        kb_rows.append([{"text": f"🔴 TZ / UG / ZM: Airtel Money ({airtel_no})", "callback_data": f"buybtn:{b['id']}:airtel", "style": "danger"}])
        kb_rows.append([{"text": "🪙 International: Crypto (CryptAPI — 100+ Coins)", "callback_data": f"buybtn:{b['id']}:crypto", "style": "success"}])
        kb_rows.append([{"text": f"🌍 Other Countries: Contact {BRAND_TAG}", "url": contact_url, "style": "primary"}])
        kb_rows.append([{"text": "📸 Send Payment Screenshot / PDF", "callback_data": f"buybtn:{b['id']}:intl", "style": "primary"}])
    else:
        kb_rows.append([{"text": f"💬 Contact {BRAND_TAG} to Get Ticket", "url": contact_url, "style": "success"}])
    kb_rows.append([{"text": "🏠 Main Menu", "callback_data": "menu:home"}])

    await send_message(
        chat_id,
        f"🎫 <b>{html_escape(b['label'])}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Price: <b>{html_escape(price_line if price > 0 else 'Contact Admin')}</b>\n"
        f"{days_line}{img_line}{desc_line}\n"
        f"💳 <b>HOW TO PAY:</b>\n"
        f"🇰🇪 <b>Kenya:</b> Use instant <b>M-Pesa STK Push (PayHero)</b> OR pay manually to Buy Goods Till <code>{html_escape(till_no)}</code>.\n"
        f"🇹🇿🇺🇬🇿🇲 <b>Tanzania, Uganda & Zambia:</b> Send via <b>Airtel Money</b> to <code>{html_escape(airtel_no)}</code>.\n"
        f"🪙 <b>International (Instant Auto-Delivery):</b> Pay via <b>CryptAPI Crypto</b> (USDT, BTC, ETH, SOL, TON, LTC & 100+ coins).\n"
        f"🌍 <b>Other Methods:</b> Contact Admin <b>{BRAND_TAG}</b>.\n\n"
        f"📸 <i>After paying manually, send your payment screenshot (photo or PDF/file) here for confirmation & automatic delivery!</i>",
        {"inline_keyboard": kb_rows},
    )


async def show_accumulator_builder(chat_id, user_id):
    sess = get_session(user_id)
    draft = {"category": "3-5 Odds", "match_ids": []}
    if sess and sess["state"] == "ACCUM_BUILD":
        try:
            draft = json.loads(sess["payload"])
        except Exception:
            pass
    else:
        set_session(user_id, "ACCUM_BUILD", json.dumps(draft))

    with connect_db() as db:
        avail = db.execute("SELECT * FROM matches WHERE publish_status='PUBLISHED' AND result='PENDING' ORDER BY id DESC LIMIT 8").fetchall()

    selected_rows = [m for m in avail if m["id"] in draft["match_ids"]]
    total_odds = 1.0
    risky_flags = []
    for m in selected_rows:
        try:
            o = float(m["odds"])
            total_odds *= o
            if o >= 2.50:
                risky_flags.append(f"⚠️ High-odds leg: {m['match_name']} ({o:.2f})")
        except ValueError:
            pass

    if len(selected_rows) >= 5 or total_odds >= 10.0:
        risky_flags.append("⚠️ Multi-leg accumulators carry higher variance (never guaranteed).")

    cat_row = [{"text": ("✅ " if draft["category"] == c else "") + c.replace(" Odds", ""), "callback_data": f"accum:cat:{c}"} for c in ACCUM_CATEGORIES[:3]]
    cat_row2 = [{"text": ("✅ " if draft["category"] == c else "") + c.replace(" Odds", ""), "callback_data": f"accum:cat:{c}"} for c in ACCUM_CATEGORIES[3:]]

    rows = [cat_row, cat_row2]
    for m in avail:
        picked = m["id"] in draft["match_ids"]
        rows.append([{
            "text": f"{'✅' if picked else '➕'} {m['match_name']} | {m['tip']} ({m['odds']})",
            "callback_data": f"accum:toggle:{m['id']}",
            "style": "success" if picked else "primary",
        }])

    if selected_rows:
        rows.append([
            {"text": "🎟️ Submit Accumulator Ticket", "callback_data": "accum:save", "style": "danger"},
            {"text": "🗑 Clear", "callback_data": "accum:clear"},
        ])
    rows.append([{"text": "🏠 Main Menu", "callback_data": "menu:home"}])

    sel_text = "\n".join(f"• <b>{html_escape(m['match_name'])}</b> — {html_escape(m['tip'])} (<code>{m['odds']}</code>)" for m in selected_rows) or "<i>No matches selected yet. Tap matches below to add/remove.</i>"
    risk_text = ("\n" + "\n".join(risky_flags)) if risky_flags else ""

    await send_message(
        chat_id,
        f"🎯 <b>ACCUMULATOR BUILDER</b> ({PROJECT_NAME})\n"
        f"Category: <b>{html_escape(draft['category'])}</b>\n"
        f"Combined Odds: <b>{total_odds:.2f}x</b> ({len(selected_rows)} legs)\n\n"
        f"<b>Your Ticket:</b>\n{sel_text}{risk_text}",
        {"inline_keyboard": rows},
    )


async def show_vip_screen(chat_id, user_id):
    prefs = get_user_prefs(user_id)
    if not prefs["confirmed"]:
        await send_message(
            chat_id,
            "🌍 Please confirm your country or display currency first so we can show accurate VIP prices & payment methods:",
            country_welcome_keyboard(),
        )
        return

    c = COUNTRIES.get(prefs["country"], COUNTRIES["US"])
    with connect_db() as db:
        plans = db.execute("SELECT * FROM plans ORDER BY price").fetchall()

    details = "\n".join(
        f"👑 <b>{html_escape(p['title'])}</b> — <b>{format_converted_price(p['price'], prefs['currency'])[0]}</b>\n<i>   {html_escape(p['benefits'])}</i>"
        for p in plans
    )
    await send_message(
        chat_id,
        f"👑 <b>{PROJECT_NAME} — VIP MEMBERSHIP</b>\n"
        f"{c['flag']} Country: <b>{c['name']}</b>  •  Display Currency: <b>{prefs['currency']}</b>\n"
        f"<i>Note: Converted prices are for display; actual payment is processed in the provider's transaction currency.</i>\n\n"
        f"{details}\n\n👇 Select a package below:",
        plans_keyboard(prefs["currency"]),
    )


async def show_stats_and_results(chat_id, user_id, only_settled=False):
    with connect_db() as db:
        total = db.execute("SELECT COUNT(*) AS n FROM matches").fetchone()["n"]
        won = db.execute("SELECT COUNT(*) AS n FROM matches WHERE result='WON'").fetchone()["n"]
        lost = db.execute("SELECT COUNT(*) AS n FROM matches WHERE result='LOST'").fetchone()["n"]
        void = db.execute("SELECT COUNT(*) AS n FROM matches WHERE result='VOID'").fetchone()["n"]
        pending = db.execute("SELECT COUNT(*) AS n FROM matches WHERE result='PENDING'").fetchone()["n"]
        rows = db.execute(
            "SELECT * FROM matches WHERE result!='PENDING' ORDER BY id DESC LIMIT 12" if only_settled
            else "SELECT * FROM matches WHERE publish_status='PUBLISHED' ORDER BY id DESC LIMIT 12"
        ).fetchall()

    decided = won + lost
    win_pct = f"{(won / decided * 100):.1f}%" if decided > 0 else "N/A"
    body = "\n".join(format_match_block(m, i + 1) for i, m in enumerate(rows)) if rows else "<i>No published records yet.</i>"
    title = "🏆 <b>RESULTS & ACCURACY</b>" if only_settled else "📊 <b>FOOTBALL STATISTICS</b>"
    await send_message(
        chat_id,
        f"{title} ({PROJECT_NAME})\n"
        f"📈 Win Rate: <b>{win_pct}</b> | ✅ {won} Won · ❌ {lost} Lost · ⚪ {void} Void · ⏳ {pending} Pending (Total: {total})\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n{body}",
        {"inline_keyboard": [[{"text": "🏠 Main Menu", "callback_data": "menu:home", "style": "primary"}]]},
    )


# --------------------------------------------------
# ADMIN BUTTONS MANAGER, TICKET IMAGES & BUTTON PICKER (ADMIN ONLY)
# --------------------------------------------------

async def show_admin_buttons(chat_id, message_id=None):
    if str(chat_id) != str(ADMIN_USER_ID):
        return
    buttons = get_all_custom_buttons(only_active=False)
    lines = []
    rows = []
    for b in buttons:
        status_dot = "🟢" if b["is_active"] else "⚫"
        if b["button_type"] == "URL":
            info = "🔗 Link"
        else:
            img_c = count_offer_media(f"btn:{b['id']}")
            info = f"💰 KES {int(b['price']):,} · {int(b['days'])}d · 🖼️{img_c}"
        lines.append(f"{status_dot} <b>{html_escape(b['label'])}</b> — {info}")
        rows.append([{"text": f"⚙️ {b['label']} ({info})", "callback_data": f"admin:btn_view:{b['id']}", "style": b["style"] if b["style"] in BUTTON_STYLES else "primary"}])

    rows.append([
        {"text": "➕ Add Ticket Button", "callback_data": "admin:btn_add_ticket", "style": "success"},
        {"text": "🔗 Add Link Button", "callback_data": "admin:btn_add_url", "style": "primary"},
    ])
    rows.append([{"text": "⬅️ Back to Admin", "callback_data": "admin:home"}])

    text = (
        "🎛️ <b>CHANNEL BUTTONS & TICKETS MANAGER</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Tap any button to set its <b>price</b>, upload <b>ticket images</b> (auto-sent after payment confirmation), edit label/color, or remove it.\n\n"
        + ("\n".join(lines) if lines else "<i>No buttons configured yet.</i>")
    )
    await edit_or_send(chat_id, message_id, text, {"inline_keyboard": rows})


async def show_admin_button_detail(chat_id, btn_id, message_id=None):
    if str(chat_id) != str(ADMIN_USER_ID):
        return
    b = get_custom_button(btn_id)
    if not b:
        await show_admin_buttons(chat_id, message_id)
        return

    style_name = {"primary": "🔵 Blue (primary)", "success": "🟢 Green (success)", "danger": "🔴 Red (danger)"}.get(b["style"], b["style"])
    if b["button_type"] == "URL":
        detail_text = (
            f"🔗 <b>LINK BUTTON #{b['id']}</b>\n"
            f"Label: <b>{html_escape(b['label'])}</b>\n"
            f"URL: <code>{html_escape(b['url'])}</code>\n"
            f"Color: {style_name}\n"
            f"Status: {'🟢 Active' if b['is_active'] else '⚫ Hidden'}"
        )
        rows = [
            [{"text": "✏️ Rename Label", "callback_data": f"admin:btn_rename:{b['id']}", "style": "primary"}],
            [{"text": f"🎨 Color: {b['style'].title()}", "callback_data": f"admin:btn_style:{b['id']}", "style": b["style"] if b["style"] in BUTTON_STYLES else "primary"}],
            [{"text": "🗑️ Remove Button", "callback_data": f"admin:btn_del:{b['id']}", "style": "danger"}],
            [{"text": "⬅️ All Buttons", "callback_data": "admin:buttons"}],
        ]
    else:
        img_count = count_offer_media(f"btn:{b['id']}")
        days_str = f"{b['days']} day(s) VIP access" if int(b["days"]) > 0 else "0 (Ticket images delivery only)"
        detail_text = (
            f"🎫 <b>TICKET BUTTON #{b['id']}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Label: <b>{html_escape(b['label'])}</b>\n"
            f"Payment (KE PayHero / Till {get_setting('mpesa_till_number', DEFAULT_MPESA_TILL)}): <b>KES {int(b['price']):,}</b>\n"
            f"TZ / UG / ZM (Airtel): <code>{html_escape(get_setting('airtel_money_number', DEFAULT_AIRTEL_NUMBER))}</code>\n"
            f"International: <b>CryptAPI Crypto (~${kes_to_usd(b['price']):.2f}) / Contact {BRAND_TAG}</b>\n"
            f"VIP Days on Pay: <b>{days_str}</b>\n"
            f"Ticket Images Uploaded: <b>{img_count}</b>\n"
            f"Color: {style_name}\n"
            f"Description: <i>{html_escape(b['description'] or 'None')}</i>"
        )
        rows = [
            [{"text": f"🖼️ Ticket Images ({img_count}) — Upload/Manage", "callback_data": f"admin:btn_media:{b['id']}", "style": "success"}],
            [
                {"text": f"💰 Set Price (KES {int(b['price']):,})", "callback_data": f"admin:btn_setprice:{b['id']}", "style": "success"},
                {"text": f"📅 VIP Days ({b['days']}d)", "callback_data": f"admin:btn_setdays:{b['id']}", "style": "primary"},
            ],
            [
                {"text": "✏️ Rename Label", "callback_data": f"admin:btn_rename:{b['id']}", "style": "primary"},
                {"text": "📝 Edit Description", "callback_data": f"admin:btn_setdesc:{b['id']}", "style": "primary"},
            ],
            [
                {"text": f"🎨 Color: {b['style'].title()}", "callback_data": f"admin:btn_style:{b['id']}", "style": b["style"] if b["style"] in BUTTON_STYLES else "primary"},
                {"text": "🗑️ Remove Button", "callback_data": f"admin:btn_del:{b['id']}", "style": "danger"},
            ],
            [{"text": "⬅️ All Buttons", "callback_data": "admin:buttons"}],
        ]
    await edit_or_send(chat_id, message_id, detail_text, {"inline_keyboard": rows})


async def show_admin_button_media(chat_id, btn_id, message_id=None):
    if str(chat_id) != str(ADMIN_USER_ID):
        return
    b = get_custom_button(btn_id)
    if not b:
        await show_admin_buttons(chat_id, message_id)
        return
    media_rows = get_offer_media(f"btn:{btn_id}")
    rows = [
        [{"text": "➕ Upload Ticket Image(s)", "callback_data": f"admin:btn_media_add:{btn_id}", "style": "success"}],
    ]
    if media_rows:
        rows.append([
            {"text": f"👁️ Preview ({len(media_rows)})", "callback_data": f"admin:btn_media_prev:{btn_id}", "style": "primary"},
            {"text": "🗑️ Clear All Images", "callback_data": f"admin:btn_media_clr:{btn_id}", "style": "danger"},
        ])
    rows.append([{"text": "⬅️ Back to Button", "callback_data": f"admin:btn_view:{btn_id}"}])

    text = (
        f"🖼️ <b>TICKET IMAGES — {html_escape(b['label'])}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Saved Images: <b>{len(media_rows)}</b>\n\n"
        "Any ticket images uploaded here are <b>automatically delivered</b> to customers the moment their payment is confirmed!"
    )
    await edit_or_send(chat_id, message_id, text, {"inline_keyboard": rows})


async def show_post_button_picker(chat_id, user_id, picker, message_id=None):
    if not is_admin(user_id) or str(chat_id) != str(ADMIN_USER_ID):
        return
    set_session(user_id, "POST_PICKER", json.dumps(picker))
    buttons = get_all_custom_buttons(only_active=True)
    selected = set(int(x) for x in picker.get("btn_ids", []))

    mode = picker.get("mode", "custom")
    mode_titles = {
        "custom": "📝 POST TO MAIN CHANNEL",
        "vip_promo": "📢 VIP PROMO POST",
        "free_matches": "⚽ POST QUEUED FREE MATCHES",
    }
    action_labels = {
        "custom": "➡️ Next: Write Post Text / Send Photo",
        "vip_promo": "🚀 Publish VIP Promo Now",
        "free_matches": "🚀 Publish FREE Matches Now",
    }

    rows = []
    for b in buttons:
        is_on = int(b["id"]) in selected
        price_tag = f" (KES {int(b['price']):,})" if (b["button_type"] == "TICKET" and int(b["price"]) > 0) else ""
        rows.append([{
            "text": f"{'✅' if is_on else '⬜'} {b['label']}{price_tag}",
            "callback_data": f"postpick:toggle:{b['id']}",
            "style": "success" if is_on else "primary",
        }])

    rows.append([
        {"text": "✅ Select All", "callback_data": "postpick:all", "style": "success"},
        {"text": "⬜ Clear All", "callback_data": "postpick:none", "style": "primary"},
    ])
    rows.append([
        {"text": f"👑 VIP/Contact Footer: {'ON' if picker.get('footer', True) else 'OFF'}", "callback_data": "postpick:footer"},
        {"text": f"🔥 Reactions: {'ON' if picker.get('react', True) else 'OFF'}", "callback_data": "postpick:react"},
    ])
    rows.append([
        {"text": action_labels.get(mode, "➡️ Next"), "callback_data": "postpick:next", "style": "success"},
    ])
    rows.append([
        {"text": "🎛️ Manage/Add Buttons", "callback_data": "admin:buttons", "style": "primary"},
        {"text": "❌ Cancel", "callback_data": "admin:home", "style": "danger"},
    ])

    selected_names = [b["label"] for b in buttons if int(b["id"]) in selected]
    summary = ", ".join(html_escape(n) for n in selected_names) if selected_names else "<i>None selected</i>"

    text = (
        f"<b>{mode_titles.get(mode, '📝 CHANNEL POST')} — CHOOSE BUTTONS</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Tap any button below to toggle it ON (✅) or OFF (⬜) for this post.\n\n"
        f"<b>Selected Buttons ({len(selected_names)}):</b>\n{summary}"
    )
    await edit_or_send(chat_id, message_id, text, {"inline_keyboard": rows})


async def show_admin_cryptapi_panel(chat_id, message_id=None):
    if str(chat_id) != str(ADMIN_USER_ID):
        return
    coins, networks = await fetch_cryptapi_catalog()
    wallets = get_cryptapi_wallets_map()
    enabled_cnt = sum(1 for t in coins if get_wallet_for_ticker(t))

    lines = [
        "🪙 <b>CRYPTAPI INTERNATIONAL CRYPTO SETTINGS</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🌐 Callback Base: <code>{html_escape(get_cryptapi_callback_base())}</code>",
        f"⛓️ Required Confirmations: <b>{html_escape(get_setting('cryptapi_confirmations', '1'))}</b>",
        f"📊 Catalog Loaded: <b>{len(coins)} coins across {len(networks)} networks</b>",
        f"✅ Enabled for Checkout: <b>{enabled_cnt} / {len(coins)}</b>",
        "",
        "<b>Configured Receiving Wallets:</b>",
    ]
    rows = []
    for key, label, _ in CRYPTO_WALLET_GROUPS:
        val = wallets.get(key, "")
        short_val = f"<code>{html_escape(val[:8])}...{html_escape(val[-6:])}</code>" if len(val) > 16 else (f"<code>{html_escape(val)}</code>" if val else "<i>Not set</i>")
        lines.append(f"{'✅' if val else '⬜'} <b>{key.upper()}</b>: {short_val}")
        rows.append([{"text": f"{'✏️' if val else '➕'} Set {key.upper()} Wallet", "callback_data": f"admin:cry_set:{key}", "style": "success" if val else "primary"}])

    rows.append([
        {"text": "🎯 Set Specific Coin Override", "callback_data": "admin:cry_set_custom", "style": "primary"},
        {"text": "🔄 Refresh CryptAPI Catalog", "callback_data": "admin:cry_refresh", "style": "success"},
    ])
    rows.append([{"text": "⬅️ Back to Settings", "callback_data": "admin:channels"}])
    lines.append("\n<i>Tip: Setting 1 EVM (0x...) address enables ETH, ERC20, BEP20, Polygon, Arbitrum, Optimism, Base, AVAX, etc. Setting 1 TRON (T...) address enables TRX & TRC20-USDT!</i>")

    await edit_or_send(chat_id, message_id, "\n".join(lines), {"inline_keyboard": rows})


async def show_admin_payhero_diag(chat_id, message_id=None):
    if str(chat_id) != str(ADMIN_USER_ID):
        return
    cur_ch = get_setting("payhero_channel_id", PAYHERO_CHANNEL_ID)
    diag = await fetch_payhero_diagnostics()
    ch_res = diag["channels"]
    wal_res = diag["wallet"]

    lines = [
        "🩺 <b>PAYHERO DIAGNOSTICS</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🔢 Configured Channel ID: <code>{html_escape(str(cur_ch))}</code>",
        f"🔗 Callback URL: <code>{html_escape(PUBLIC_URL.rstrip('/') + '/payhero/callback')}</code>",
        "",
    ]

    # Wallet balance
    w_status = wal_res.get("status")
    w_data = wal_res.get("data")
    if w_status == 200 and isinstance(w_data, dict):
        bal = w_data.get("balance") or w_data.get("data", {}).get("balance") if isinstance(w_data.get("data"), dict) else w_data.get("balance")
        lines.append(f"💰 Service Wallet: <b>HTTP 200</b> — Balance: <b>KES {html_escape(str(bal if bal is not None else w_data))}</b>")
    else:
        lines.append(f"❌ Service Wallet Check: <b>HTTP {w_status}</b> — <code>{html_escape(str(wal_res.get('error') or w_data)[:180])}</code>")

    # Active channels
    c_status = ch_res.get("status")
    c_data = ch_res.get("data")
    rows = []
    if c_status == 200:
        items = c_data if isinstance(c_data, list) else (c_data.get("channels") or c_data.get("data") or [] if isinstance(c_data, dict) else [])
        lines.append(f"✅ Auth OK! Active Payment Channels (<b>{len(items)}</b>):")
        found_match = False
        for ch in items[:10]:
            cid_val = ch.get("id") or ch.get("channel_id")
            cname = ch.get("name") or ch.get("channel_type") or ch.get("short_code") or "Channel"
            ctype = ch.get("channel_type") or ch.get("provider") or ""
            is_cur = str(cid_val) == str(cur_ch)
            if is_cur:
                found_match = True
            lines.append(f"  {'✅' if is_cur else '•'} ID <code>{cid_val}</code> — {html_escape(str(cname))} ({html_escape(str(ctype))})")
            if cid_val and not is_cur:
                rows.append([{"text": f"🔄 Switch to Channel ID {cid_val} ({cname})", "callback_data": f"admin:ph_use_ch:{cid_val}", "style": "success"}])
        if items and not found_match:
            lines.append("\n⚠️ <b>MISMATCH DETECTED:</b> Your current Channel ID does NOT match any active PayHero channel ID above! Tap a button below to switch it.")
    else:
        lines.append(f"❌ Channels Auth Check: <b>HTTP {c_status}</b> — <code>{html_escape(str(ch_res.get('error') or c_data)[:200])}</code>")
        if c_status == 401:
            lines.append("💡 HTTP 401 means <code>PAYHERO_AUTH_TOKEN</code> is invalid or expired. Copy the Basic Auth token from PayHero Dashboard → API Keys.")

    rows.append([{"text": "🔄 Re-run Diagnostics", "callback_data": "admin:payhero_diag", "style": "primary"}])
    rows.append([{"text": "⬅️ Back to Settings", "callback_data": "admin:channels"}])
    await edit_or_send(chat_id, message_id, "\n".join(lines), {"inline_keyboard": rows})


# --------------------------------------------------
# COMMAND & MESSAGE HANDLER
# --------------------------------------------------

async def handle_message(message):
    user = message.get("from", {})
    if not user or user.get("is_bot"):
        return
    user_id = user.get("id")
    chat_obj = message.get("chat", {})
    chat_id = chat_obj.get("id")
    chat_type = chat_obj.get("type", "private")

    photo_list = message.get("photo") or []
    photo_file_id = photo_list[-1]["file_id"] if photo_list else None
    doc_obj = message.get("document")
    doc_file_id = doc_obj["file_id"] if doc_obj else None
    text = (message.get("text") or message.get("caption") or "").strip()

    if not user_id or not chat_id:
        return

    referred_by = None
    if text.startswith("/start "):
        arg = text.split(None, 1)[1].strip()
        if arg.startswith("ref_") and arg[4:].isdigit():
            referred_by = int(arg[4:])

    register_user(user, referred_by=referred_by)
    db_user = get_user(user_id)
    if db_user and db_user["is_blocked"] and not is_admin(user_id):
        await send_message(chat_id, "🚫 Your account has been suspended.")
        return

    if message.get("location"):
        sess = get_session(user_id)
        if sess and sess["state"] == "WAIT_LOCATION_PERM":
            clear_session(user_id)
            lat = message["location"]["latitude"]
            lon = message["location"]["longitude"]
            detected_code = await reverse_geocode_country(lat, lon)
            await send_message(chat_id, "🔍 Checking location...", {"remove_keyboard": True})
            if detected_code and detected_code in COUNTRIES:
                c = COUNTRIES[detected_code]
                await send_message(
                    chat_id,
                    f"📍 Detected Country: {c['flag']} <b>{c['name']}</b> ({c['currency']})\n\nIs this your country?",
                    {
                        "inline_keyboard": [
                            [{"text": "✅ Yes, Confirm", "callback_data": f"country:confirm:{detected_code}", "style": "success"}],
                            [{"text": "🔄 Change Country", "callback_data": "country:manual", "style": "primary"}],
                        ]
                    },
                )
            else:
                await send_message(
                    chat_id,
                    "⚠️ Could not match your location to our country list. Please select manually:",
                    country_manual_keyboard(),
                )
            return

    if text.startswith("/"):
        cmd = text.split()[0].split("@")[0].lower()

        if cmd == "/start":
            clear_session(user_id)
            prefs = get_user_prefs(user_id)
            arg = text.split(None, 1)[1].strip() if len(text.split(None, 1)) > 1 else ""
            if arg.lower() == "vip":
                await show_vip_screen(chat_id, user_id)
            elif arg.startswith("btn_") and arg[4:].isdigit():
                await show_ticket_button_checkout(chat_id, user_id, int(arg[4:]))
            elif not prefs["confirmed"]:
                await show_country_welcome(chat_id)
            else:
                await show_main_menu(chat_id, user_id)
            return

        if cmd == "/cancel":
            clear_session(user_id)
            await send_message(chat_id, "🛑 Cancelled.", {"remove_keyboard": True})
            await show_main_menu(chat_id, user_id)
            return

        admin_cmds = {"/admin", "/done", "/reply", "/addmatch", "/setprice", "/grant", "/revoke", "/export", "/setsetting"}
        if cmd in admin_cmds:
            if not is_admin(user_id):
                await report_unauthorized_admin_attempt(user_id, f"CMD:{cmd}")
                await send_message(chat_id, "⛔ Access Denied. Admin Dashboard is restricted to the Admin only.")
                return
            if not is_admin_private_chat(user_id, chat_id, chat_type):
                await send_message(chat_id, "🔒 For security, admin commands only work in private DM with the bot.")
                return

            if cmd == "/admin":
                await send_message(chat_id, f"⚡ <b>{PROJECT_NAME} ADMIN</b> ({BRAND_TAG})", admin_keyboard(user_id))
                return

            if cmd == "/done":
                sess = get_session(user_id)
                if sess and sess["state"] == "WAIT_BTN_MEDIA":
                    btn_id = int(sess["payload"])
                    clear_session(user_id)
                    await show_admin_button_media(chat_id, btn_id)
                    return
                clear_session(user_id)
                await send_message(chat_id, "✅ Done.", admin_keyboard(user_id))
                return

            if cmd == "/reply":
                parts = text.split(None, 2)
                if len(parts) < 3 or not parts[1].isdigit():
                    await send_message(chat_id, "Usage: <code>/reply &lt;user_id&gt; &lt;message&gt;</code>")
                    return
                await send_message(int(parts[1]), f"💬 <b>ADMIN REPLY ({BRAND_TAG})</b>\n\n{html_escape(parts[2])}")
                await send_message(chat_id, "✅ Reply sent.")
                return

            if cmd == "/addmatch":
                raw = text.split(None, 1)[1] if len(text.split(None, 1)) > 1 else ""
                parts = [p.strip() for p in raw.split("|")]
                if len(parts) < 7 or parts[0].upper() not in ("FREE", "VIP"):
                    await send_message(
                        chat_id,
                        "Usage:\n<code>/addmatch FREE | Arsenal vs Chelsea | Over 2.5 Goals | Over 2.5 | 1.85 | 2026-11-15 | 19:45 UTC</code>",
                    )
                    return
                tier, mname, market, tip, odds, mdate, ktime = parts[:7]
                with connect_db() as db:
                    db.execute(
                        "INSERT INTO matches(tier,match_name,market,tip,odds,match_date,kickoff_time,publish_status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                        (tier.upper(), mname, market, tip, odds, mdate, ktime, "QUEUED", utcnow().isoformat()),
                    )
                log_audit(user_id, "ADD_MATCH", mname)
                await send_message(chat_id, "✅ Match queued. Open Admin → Fixtures & Matches to publish.")
                return

            if cmd == "/setprice":
                parts = text.split()
                if len(parts) != 3 or not parts[2].isdigit() or not get_plan(parts[1].lower()):
                    await send_message(chat_id, "Usage: <code>/setprice monthly 1000</code>")
                    return
                with connect_db() as db:
                    db.execute("UPDATE plans SET price=? WHERE name=?", (int(parts[2]), parts[1].lower()))
                log_audit(user_id, "SET_PRICE", f"{parts[1]}={parts[2]}")
                await send_message(chat_id, f"✅ Updated {parts[1]} to KES {parts[2]}.")
                return

            if cmd == "/grant":
                parts = text.split()
                if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
                    await send_message(chat_id, "Usage: <code>/grant &lt;user_id&gt; &lt;days&gt;</code>")
                    return
                await give_vip_access(int(parts[1]), f"{parts[2]}-day", days_override=int(parts[2]))
                log_audit(user_id, "GRANT_VIP", f"{parts[1]} for {parts[2]}d")
                await send_message(chat_id, "✅ VIP granted.")
                return

            if cmd == "/revoke":
                parts = text.split()
                if len(parts) != 2 or not parts[1].isdigit():
                    await send_message(chat_id, "Usage: <code>/revoke &lt;user_id&gt;</code>")
                    return
                uid = int(parts[1])
                vip_chat = get_setting("vip_channel_id", VIP_CHANNEL_ID)
                try:
                    await telegram("banChatMember", {"chat_id": vip_chat, "user_id": uid})
                    await telegram("unbanChatMember", {"chat_id": vip_chat, "user_id": uid, "only_if_banned": True})
                except Exception:
                    pass
                with connect_db() as db:
                    db.execute("UPDATE subscriptions SET status='EXPIRED' WHERE user_id=?", (uid,))
                log_audit(user_id, "REVOKE_VIP", str(uid))
                await send_message(chat_id, "🚫 VIP revoked.")
                return

            if cmd == "/export":
                await export_csv_backups(chat_id)
                return

            if cmd == "/setsetting":
                parts = text.split(None, 2)
                allowed = {
                    "main_channel_id", "vip_channel_id", "main_channel_url",
                    "payhero_channel_id", "mpesa_till_number", "airtel_money_number",
                    "referral_reward_days", "vip_odds_info", "admin_contact_url", "website_url",
                    "cryptapi_confirmations", "cryptapi_callback_base",
                }
                if len(parts) != 3 or parts[1] not in allowed:
                    await send_message(chat_id, f"Allowed keys: {', '.join(sorted(allowed))}")
                    return
                set_setting(parts[1], parts[2].strip())
                log_audit(user_id, "SET_SETTING", parts[1])
                await send_message(chat_id, f"✅ Saved <code>{parts[1]}</code>.")
                return

        await send_message(chat_id, "❓ Unknown command.", main_menu_keyboard(user_id))
        return

    # ---- Session states ----
    sess = get_session(user_id)
    if sess:
        state, payload = sess["state"], sess["payload"]

        if state == "WAIT_MANUAL_ANALYSIS":
            clear_session(user_id)
            if not FOOTBALL_API_KEY or not AI_API_KEY:
                await send_message(
                    chat_id,
                    f"🤖 <b>Manual Match Analysis: {html_escape(text)}</b>\n\n"
                    "ℹ️ Football Data & AI API keys (<code>FOOTBALL_API_KEY</code> / <code>AI_API_KEY</code>) are scheduled for <b>Stage 2 & 3</b>.\n"
                    "We never invent fixtures, statistics, or odds—once you supply those keys in Stage 2, live H2H, form, and risk analysis will run here.",
                    main_menu_keyboard(user_id),
                )
            return

        if state == "WAIT_MPESA_PHONE":
            phone = normalize_phone(text)
            if not phone:
                await send_message(chat_id, "⚠️ Enter a valid Kenyan Safaricom number (e.g. <code>0712345678</code>) or /cancel.")
                return
            clear_session(user_id)
            await start_mpesa_payment(user_id, payload, phone, notify_telegram=True)
            return

        if state == "WAIT_PAYMENT_PROOF":
            clear_session(user_id)
            await handle_payment_proof_submission(user_id, chat_id, payload, text, photo_file_id, doc_file_id=doc_file_id)
            return

        if state == "WAIT_SUPPORT_MSG":
            clear_session(user_id)
            kb = {"inline_keyboard": [[{"text": "💬 Reply", "callback_data": f"admin:reply:{user_id}", "style": "primary"}]]}
            if photo_file_id:
                await telegram("sendPhoto", {"chat_id": ADMIN_USER_ID, "photo": photo_file_id, "caption": f"📩 Support photo from <code>{user_id}</code>\n{html_escape(text)}"[:1020], "parse_mode": "HTML", "reply_markup": kb})
            elif doc_file_id:
                await telegram("sendDocument", {"chat_id": ADMIN_USER_ID, "document": doc_file_id, "caption": f"📩 Support file from <code>{user_id}</code>\n{html_escape(text)}"[:1020], "parse_mode": "HTML", "reply_markup": kb})
            else:
                await send_message(ADMIN_USER_ID, f"📩 <b>SUPPORT MESSAGE</b>\nFrom: <code>{user_id}</code>\n\n{html_escape(text)}", kb)
            await send_message(chat_id, f"✅ Sent to admin ({BRAND_TAG}).", main_menu_keyboard(user_id))
            return

        admin_states = {
            "WAIT_ADMIN_REPLY", "WAIT_CHANNEL_POST", "WAIT_VIP_CHANNEL_POST",
            "WAIT_BTN_MEDIA", "WAIT_BTN_ADD_TICKET", "WAIT_BTN_ADD_PRICE",
            "WAIT_BTN_ADD_URL", "WAIT_BTN_SETPRICE", "WAIT_BTN_SETDAYS",
            "WAIT_BTN_RENAME", "WAIT_BTN_SETDESC", "WAIT_BROADCAST", "WAIT_PLAN_PRICE",
            "WAIT_CRY_WALLET", "WAIT_CRY_CUSTOM_WALLET",
        }
        if state in admin_states:
            if not is_admin_private_chat(user_id, chat_id, chat_type):
                clear_session(user_id)
                await report_unauthorized_admin_attempt(user_id, f"SESSION:{state}")
                await send_message(chat_id, "⛔ Access Denied.", main_menu_keyboard(user_id))
                return

            if state == "WAIT_CRY_WALLET":
                clear_session(user_id)
                addr = "" if text.lower() in ("clear", "none", "remove") else text.strip()
                save_cryptapi_wallet(payload, addr)
                log_audit(user_id, "SET_CRYPTAPI_WALLET", f"{payload}={'cleared' if not addr else 'updated'}")
                await show_admin_cryptapi_panel(chat_id)
                return

            if state == "WAIT_CRY_CUSTOM_WALLET":
                parts = [p.strip() for p in text.split("|", 1)]
                if len(parts) != 2 or not parts[0]:
                    await send_message(chat_id, "⚠️ Format: <code>ticker | wallet_address</code>\nExample: <code>trc20/usdt | TYourTronAddressHere</code>")
                    return
                clear_session(user_id)
                addr = "" if parts[1].lower() in ("clear", "none", "remove") else parts[1].strip()
                save_cryptapi_wallet(parts[0].lower(), addr)
                log_audit(user_id, "SET_CRYPTAPI_CUSTOM", parts[0].lower())
                await show_admin_cryptapi_panel(chat_id)
                return

            if state == "WAIT_ADMIN_REPLY":
                clear_session(user_id)
                await send_message(int(payload), f"💬 <b>ADMIN REPLY ({BRAND_TAG})</b>\n\n{html_escape(text)}")
                await send_message(chat_id, "✅ Reply sent.", admin_keyboard(user_id))
                return

            if state == "WAIT_CHANNEL_POST":
                clear_session(user_id)
                try:
                    picker = json.loads(payload)
                except Exception:
                    picker = {"btn_ids": [], "footer": True, "react": True}
                await publish_custom_main_post(
                    text,
                    photo_file_id=photo_file_id,
                    selected_btn_ids=picker.get("btn_ids", []),
                    include_footer=picker.get("footer", True),
                    include_reactions=picker.get("react", True),
                )
                log_audit(user_id, "CHANNEL_POST", text[:80])
                await send_message(chat_id, "✅ Published to Main Channel with your selected buttons!", admin_keyboard(user_id))
                return

            if state == "WAIT_VIP_CHANNEL_POST":
                if not text and not photo_file_id:
                    await send_message(chat_id, "⚠️ Send text or a photo+caption to post to the VIP Channel (or /cancel).")
                    return
                await publish_vip_channel_post(text, photo_file_id=photo_file_id)
                log_audit(user_id, "VIP_CHANNEL_POST", text[:80] or "photo")
                await send_message(
                    chat_id,
                    "✅ Published to <b>VIP Channel</b>!\n\nSend another VIP post/image right now, or tap <b>Done</b>:",
                    {"inline_keyboard": [[{"text": "✅ Done — Admin Dashboard", "callback_data": "admin:home", "style": "success"}]]},
                )
                return

            if state == "WAIT_BTN_MEDIA":
                btn_id = int(payload)
                b = get_custom_button(btn_id)
                if not b:
                    clear_session(user_id)
                    await show_admin_buttons(chat_id)
                    return
                if photo_file_id:
                    fid, mtype = photo_file_id, "photo"
                elif doc_file_id:
                    fid, mtype = doc_file_id, "document"
                else:
                    await send_message(chat_id, "⚠️ Please send a <b>ticket image (photo or file)</b>, or tap Done / /done.")
                    return
                offer_key = f"btn:{btn_id}"
                with connect_db() as db:
                    db.execute(
                        "INSERT INTO ticket_media(offer_key,file_id,media_type,caption,created_at) VALUES(?,?,?,?,?)",
                        (offer_key, fid, mtype, text[:400], utcnow().isoformat()),
                    )
                cnt = count_offer_media(offer_key)
                log_audit(user_id, "UPLOAD_TICKET_MEDIA", f"btn:{btn_id} total={cnt}")
                await send_message(
                    chat_id,
                    f"✅ Saved ticket image <b>#{cnt}</b> for <b>{html_escape(b['label'])}</b>!\nSend another image if needed, or tap Done:",
                    {"inline_keyboard": [[{"text": "✅ Done Uploading", "callback_data": f"admin:btn_media:{btn_id}", "style": "success"}]]},
                )
                return

            if state == "WAIT_BTN_ADD_TICKET":
                parts = [p.strip() for p in text.split("|")]
                if len(parts) >= 2 and parts[0]:
                    label = parts[0][:64]
                    if not parts[1].isdigit():
                        await send_message(chat_id, "⚠️ Price must be a whole number in KES. Example:\n<code>💎 Diamond Ticket | 1500 | 0</code>")
                        return
                    price = int(parts[1])
                    days = int(parts[2]) if (len(parts) >= 3 and parts[2].isdigit()) else 1
                    clear_session(user_id)
                    with connect_db() as db:
                        new_id = insert_returning_id(
                            db,
                            "INSERT INTO custom_buttons(label,url,button_type,price,days,description,style,is_active) VALUES(?,?,?,?,?,?,?,1)",
                            (label, "", "TICKET", price, days, "", "primary"),
                        )
                    log_audit(user_id, "ADD_BTN_TICKET", f"{label} KES {price}")
                    await send_message(chat_id, f"✅ Added ticket button <b>{html_escape(label)}</b>!")
                    await show_admin_button_detail(chat_id, new_id)
                elif text:
                    set_session(user_id, "WAIT_BTN_ADD_PRICE", text[:64])
                    await send_message(chat_id, f"💰 Enter the <b>KES price</b> for <b>{html_escape(text[:64])}</b> (e.g. <code>500</code>, or <code>0</code> for Contact Admin only):")
                return

            if state == "WAIT_BTN_ADD_PRICE":
                if not text.isdigit():
                    await send_message(chat_id, "⚠️ Enter a valid whole number KES price (e.g. <code>500</code>) or /cancel.")
                    return
                price = int(text)
                label = payload[:64]
                clear_session(user_id)
                with connect_db() as db:
                    new_id = insert_returning_id(
                        db,
                        "INSERT INTO custom_buttons(label,url,button_type,price,days,description,style,is_active) VALUES(?,?,?,?,?,?,?,1)",
                        (label, "", "TICKET", price, 1, "", "primary"),
                    )
                log_audit(user_id, "ADD_BTN_TICKET", f"{label} KES {price}")
                await send_message(chat_id, f"✅ Created <b>{html_escape(label)}</b> — KES {price:,}!")
                await show_admin_button_detail(chat_id, new_id)
                return

            if state == "WAIT_BTN_ADD_URL":
                parts = [p.strip() for p in text.split("|", 1)]
                if len(parts) != 2 or not parts[0] or not parts[1].startswith(("http://", "https://")):
                    await send_message(chat_id, "⚠️ Format: <code>Button Label | https://example.com</code>")
                    return
                clear_session(user_id)
                with connect_db() as db:
                    new_id = insert_returning_id(
                        db,
                        "INSERT INTO custom_buttons(label,url,button_type,price,days,description,style,is_active) VALUES(?,?,?,?,?,?,?,1)",
                        (parts[0][:64], parts[1][:300], "URL", 0, 0, "", "primary"),
                    )
                log_audit(user_id, "ADD_BTN_URL", parts[0][:64])
                await send_message(chat_id, f"✅ Added link button <b>{html_escape(parts[0][:64])}</b>!")
                await show_admin_button_detail(chat_id, new_id)
                return

            if state == "WAIT_BTN_SETPRICE":
                if not text.isdigit():
                    await send_message(chat_id, "⚠️ Enter a whole number KES price (e.g. <code>500</code>) or /cancel.")
                    return
                btn_id = int(payload)
                clear_session(user_id)
                with connect_db() as db:
                    db.execute("UPDATE custom_buttons SET price=? WHERE id=?", (int(text), btn_id))
                log_audit(user_id, "EDIT_BTN_PRICE", f"btn:{btn_id}={text}")
                await show_admin_button_detail(chat_id, btn_id)
                return

            if state == "WAIT_BTN_SETDAYS":
                if not text.isdigit():
                    await send_message(chat_id, "⚠️ Enter a whole number of days (e.g. <code>1</code>, <code>7</code>, or <code>0</code> for ticket image delivery only).")
                    return
                btn_id = int(payload)
                clear_session(user_id)
                with connect_db() as db:
                    db.execute("UPDATE custom_buttons SET days=? WHERE id=?", (int(text), btn_id))
                log_audit(user_id, "EDIT_BTN_DAYS", f"btn:{btn_id}={text}d")
                await show_admin_button_detail(chat_id, btn_id)
                return

            if state == "WAIT_BTN_RENAME":
                if not text:
                    await send_message(chat_id, "⚠️ Send a valid button label or /cancel.")
                    return
                btn_id = int(payload)
                clear_session(user_id)
                with connect_db() as db:
                    db.execute("UPDATE custom_buttons SET label=? WHERE id=?", (text[:64], btn_id))
                log_audit(user_id, "RENAME_BTN", f"btn:{btn_id}={text[:64]}")
                await show_admin_button_detail(chat_id, btn_id)
                return

            if state == "WAIT_BTN_SETDESC":
                btn_id = int(payload)
                clear_session(user_id)
                with connect_db() as db:
                    db.execute("UPDATE custom_buttons SET description=? WHERE id=?", (text[:300], btn_id))
                log_audit(user_id, "DESC_BTN", f"btn:{btn_id}")
                await show_admin_button_detail(chat_id, btn_id)
                return

            if state == "WAIT_BROADCAST":
                clear_session(user_id)
                with connect_db() as db:
                    targets = db.execute("SELECT user_id FROM users WHERE is_blocked=0 AND notify_news=1").fetchall()
                sent = 0
                for t in targets:
                    try:
                        if photo_file_id:
                            await telegram("sendPhoto", {"chat_id": int(t["user_id"]), "photo": photo_file_id, "caption": f"📣 <b>{PROJECT_NAME}</b>\n\n{html_escape(text)}"[:1020], "parse_mode": "HTML"})
                        else:
                            await send_message(int(t["user_id"]), f"📣 <b>{PROJECT_NAME}</b>\n\n{html_escape(text)}")
                        sent += 1
                    except Exception:
                        pass
                await send_message(chat_id, f"✅ Broadcast sent to {sent} users.", admin_keyboard(user_id))
                return

            if state == "WAIT_PLAN_PRICE":
                clear_session(user_id)
                if not text.isdigit() or int(text) < 1:
                    await send_message(chat_id, "⚠️ Enter a whole number KES price.")
                    return
                with connect_db() as db:
                    db.execute("UPDATE plans SET price=? WHERE name=?", (int(text), payload))
                log_audit(user_id, "EDIT_PRICE", f"{payload}={text}")
                await send_message(chat_id, f"✅ Updated {payload} to KES {text}.", admin_keyboard(user_id))
                return

    await show_main_menu(chat_id, user_id)


# --------------------------------------------------
# CALLBACK HANDLER
# --------------------------------------------------

async def handle_callback(query):
    cid = query["id"]
    user = query["from"]
    if not user or user.get("is_bot"):
        await answer_callback(cid)
        return
    user_id = user["id"]
    data = query.get("data", "")
    msg_obj = query.get("message") or {}
    chat_obj = msg_obj.get("chat") or {}
    chat_id = chat_obj.get("id") or user_id
    chat_type = chat_obj.get("type", "private")
    message_id = msg_obj.get("message_id")

    if data.startswith("react:"):
        idx = int(data.split(":", 1)[1])
        if 0 <= idx < len(REACTION_EMOJIS) and message_id:
            c_msg_id = message_id
            emoji = REACTION_EMOJIS[idx]
            with connect_db() as db:
                exists = db.execute(
                    "SELECT 1 FROM post_reactions WHERE channel_msg_id=? AND user_id=? AND emoji=?",
                    (c_msg_id, user_id, emoji),
                ).fetchone()
                if exists:
                    db.execute("DELETE FROM post_reactions WHERE channel_msg_id=? AND user_id=? AND emoji=?", (c_msg_id, user_id, emoji))
                    feedback = f"Removed {emoji}"
                else:
                    db.execute("INSERT INTO post_reactions(channel_msg_id,user_id,emoji) VALUES(?,?,?)", (c_msg_id, user_id, emoji))
                    feedback = f"Reacted {emoji}"
            await answer_callback(cid, feedback)
            try:
                await ensure_bot_username()
                await telegram("editMessageReplyMarkup", {
                    "chat_id": msg_obj["chat"]["id"],
                    "message_id": c_msg_id,
                    "reply_markup": channel_post_keyboard(channel_msg_id=c_msg_id),
                })
            except Exception:
                pass
        else:
            await answer_callback(cid)
        return

    register_user(user)
    db_user = get_user(user_id)
    if db_user and db_user["is_blocked"] and not is_admin(user_id):
        await answer_callback(cid, "Suspended")
        return

    if data == "menu:home":
        clear_session(user_id)
        await answer_callback(cid)
        await show_main_menu(chat_id, user_id)
        return

    if data == "menu:today":
        await answer_callback(cid)
        with connect_db() as db:
            rows = db.execute("SELECT * FROM matches WHERE tier='FREE' AND publish_status='PUBLISHED' ORDER BY id DESC LIMIT 10").fetchall()
        txt = "\n".join(format_match_block(m, i + 1) for i, m in enumerate(rows)) if rows else "<i>No free predictions published yet. Check back soon!</i>"
        await send_message(chat_id, f"⚽ <b>TODAY'S PREDICTIONS ({PROJECT_NAME})</b>\n━━━━━━━━━━━━━━━━━━━━\n\n{txt}", {"inline_keyboard": [[{"text": "🏠 Main Menu", "callback_data": "menu:home", "style": "primary"}]]})
        return

    if data == "menu:ai_analysis":
        await answer_callback(cid)
        set_session(user_id, "WAIT_MANUAL_ANALYSIS")
        await send_message(chat_id, "🤖 <b>AI MATCH ANALYSIS</b>\n\nSend a fixture name (e.g. <code>Arsenal vs Chelsea</code>) or /cancel:")
        return

    if data == "menu:accum":
        await answer_callback(cid)
        await show_accumulator_builder(chat_id, user_id)
        return

    if data.startswith("accum:"):
        parts = data.split(":", 2)
        sub = parts[1]
        sess = get_session(user_id)
        draft = {"category": "3-5 Odds", "match_ids": []}
        if sess and sess["state"] == "ACCUM_BUILD":
            try:
                draft = json.loads(sess["payload"])
            except Exception:
                pass
        if sub == "cat" and len(parts) == 3:
            draft["category"] = parts[2]
            set_session(user_id, "ACCUM_BUILD", json.dumps(draft))
            await answer_callback(cid, parts[2])
            await show_accumulator_builder(chat_id, user_id)
        elif sub == "toggle" and len(parts) == 3:
            mid = int(parts[2])
            if mid in draft["match_ids"]:
                draft["match_ids"].remove(mid)
            else:
                draft["match_ids"].append(mid)
            set_session(user_id, "ACCUM_BUILD", json.dumps(draft))
            await answer_callback(cid)
            await show_accumulator_builder(chat_id, user_id)
        elif sub == "clear":
            draft["match_ids"] = []
            set_session(user_id, "ACCUM_BUILD", json.dumps(draft))
            await answer_callback(cid, "Cleared")
            await show_accumulator_builder(chat_id, user_id)
        elif sub == "save":
            if not draft["match_ids"]:
                await answer_callback(cid, "Select at least 1 match")
                return
            with connect_db() as db:
                ph = ",".join(["?"] * len(draft["match_ids"]))
                rows = db.execute(f"SELECT * FROM matches WHERE id IN ({ph})", tuple(draft["match_ids"])).fetchall()
                total_odds = 1.0
                sels = []
                for r in rows:
                    sels.append({"id": r["id"], "match": r["match_name"], "tip": r["tip"], "odds": r["odds"]})
                    try:
                        total_odds *= float(r["odds"])
                    except ValueError:
                        pass
                db.execute(
                    "INSERT INTO accumulators(category,selections_json,total_odds,risk_note,status,created_by,created_at) VALUES(?,?,?,?,?,?,?)",
                    (draft["category"], json.dumps(sels), f"{total_odds:.2f}", "Accumulators are not guaranteed.", "PENDING_ADMIN", user_id, utcnow().isoformat()),
                )
            clear_session(user_id)
            await answer_callback(cid, "Ticket submitted!")
            await send_message(chat_id, f"🎟️ <b>Accumulator Ticket Submitted!</b>\nTotal Odds: <b>{total_odds:.2f}x</b>\n<i>Sent for admin review/publication.</i>", main_menu_keyboard(user_id))
        return

    if data == "menu:stats":
        await answer_callback(cid)
        await show_stats_and_results(chat_id, user_id, only_settled=False)
        return

    if data == "menu:results":
        await answer_callback(cid)
        await show_stats_and_results(chat_id, user_id, only_settled=True)
        return

    if data == "menu:vip":
        await answer_callback(cid)
        await show_vip_screen(chat_id, user_id)
        return

    if data == "menu:subscription":
        await answer_callback(cid)
        await ensure_bot_username()
        prefs = get_user_prefs(user_id)
        with connect_db() as db:
            sub = db.execute("SELECT * FROM subscriptions WHERE user_id=?", (user_id,)).fetchone()
            orders = db.execute("SELECT * FROM orders WHERE user_id=? ORDER BY created_at DESC LIMIT 6", (user_id,)).fetchall()
            ref_count = db.execute("SELECT COUNT(*) AS n FROM users WHERE referred_by=? AND referral_rewarded=1", (user_id,)).fetchone()["n"]
        sub_txt = f"✅ Active ({sub['plan'].title()}) — Expires: {'Lifetime' if sub['is_lifetime'] else format_local_time(sub['expires_at'], prefs['tz'])}" if (sub and sub["status"] == "ACTIVE") else "❌ Inactive"
        ord_txt = "\n".join(f"• {html_escape(format_order_item_name(o['plan']))} — {o['local_amount'] or o['amount']} [{o['status']}]" for o in orders) or "No orders yet."
        reward_days = get_setting("referral_reward_days", "2")
        ref_link = f"https://t.me/{bot_username}?start=ref_{user_id}" if bot_username else ""
        ref_txt = (
            f"\n\n🎁 <b>Referral Program (+{reward_days}d VIP per paid invite):</b>\n"
            f"Rewarded Invites: <b>{ref_count}</b>\n"
            f"Your Link: <code>{ref_link}</code>"
            if ref_link else ""
        )
        await send_message(chat_id, f"💳 <b>MY SUBSCRIPTION</b>\n\nStatus: <b>{sub_txt}</b>\n\n<b>Recent Payments:</b>\n{ord_txt}{ref_txt}", {"inline_keyboard": [[{"text": "👑 VIP Membership", "callback_data": "menu:vip", "style": "danger"}], [{"text": "🏠 Main Menu", "callback_data": "menu:home"}]]})
        return

    if data == "menu:country":
        await answer_callback(cid)
        await show_country_welcome(chat_id)
        return

    if data == "country:detect_ask":
        await answer_callback(cid)
        set_session(user_id, "WAIT_LOCATION_PERM")
        await send_message(
            chat_id,
            "📍 <b>Optional Location Permission</b>\n\nTap <b>Share Approximate Location</b> below to detect your country, or tap <b>Select Manually</b> (location is never forced or stored).",
            {
                "keyboard": [
                    [{"text": "📍 Share My Location", "request_location": True}],
                    [{"text": "/cancel"}],
                ],
                "resize_keyboard": True,
                "one_time_keyboard": True,
            },
        )
        return

    if data == "country:manual":
        await answer_callback(cid)
        await send_message(chat_id, "🗺️ <b>Select your country manually:</b>", country_manual_keyboard())
        return

    if data.startswith("country:pick:"):
        code = data.split(":")[2]
        c = COUNTRIES.get(code)
        if not c:
            await answer_callback(cid, "Invalid country")
            return
        await answer_callback(cid)
        await send_message(
            chat_id,
            f"📍 Selected: {c['flag']} <b>{c['name']}</b> ({c['currency']})\n\nIs this your country?",
            {
                "inline_keyboard": [
                    [{"text": "✅ Yes, Confirm", "callback_data": f"country:confirm:{code}", "style": "success"}],
                    [{"text": "🔄 Change Country", "callback_data": "country:manual", "style": "primary"}],
                ]
            },
        )
        return

    if data.startswith("country:confirm:"):
        code = data.split(":")[2]
        c = COUNTRIES.get(code)
        if c:
            with connect_db() as db:
                db.execute(
                    "UPDATE users SET country=?, country_confirmed=1, currency=?, timezone_name=? WHERE user_id=?",
                    (code, c["currency"], c["tz"], user_id),
                )
            await answer_callback(cid, f"Confirmed: {c['name']}")
            await show_main_menu(chat_id, user_id)
        return

    if data == "currency:menu":
        await answer_callback(cid)
        await send_message(chat_id, "💱 <b>Choose display currency:</b>", currency_manual_keyboard())
        return

    if data.startswith("currency:set:"):
        curr = data.split(":")[2]
        if curr in CURRENCIES:
            with connect_db() as db:
                db.execute("UPDATE users SET currency=?, country_confirmed=1 WHERE user_id=?", (curr, user_id))
            await answer_callback(cid, f"Currency set to {curr}")
            await show_vip_screen(chat_id, user_id)
        return

    if data == "menu:contact":
        await answer_callback(cid)
        set_session(user_id, "WAIT_SUPPORT_MSG")
        await send_message(chat_id, f"💬 <b>CONTACT ADMIN ({BRAND_TAG})</b>\n\nSend your message (or photo/file) below, or /cancel:")
        return

    if data.startswith("openbtn:") and data.split(":", 1)[1].isdigit():
        await answer_callback(cid)
        await show_ticket_button_checkout(chat_id, user_id, int(data.split(":", 1)[1]))
        return

    if data.startswith("plan:"):
        plan_name = data.split(":", 1)[1]
        plan = get_plan(plan_name)
        if not plan:
            await answer_callback(cid, "Unavailable")
            return
        await answer_callback(cid)
        prefs = get_user_prefs(user_id)
        c = COUNTRIES.get(prefs["country"], COUNTRIES["US"])
        disp, _ = format_converted_price(plan["price"], prefs["currency"])
        till_no = get_setting("mpesa_till_number", DEFAULT_MPESA_TILL)
        airtel_no = get_setting("airtel_money_number", DEFAULT_AIRTEL_NUMBER)
        rows = []
        for m in c["methods"]:
            lbl, sty = METHOD_LABELS[m]
            if m == "mpesa_till":
                lbl = f"🏪 Manual M-Pesa Till ({till_no})"
            elif m == "airtel":
                lbl = f"🔴 Airtel Money ({airtel_no})"
            rows.append([{"text": lbl, "callback_data": f"pay:{plan_name}:{m}", "style": sty}])
        rows.append([{"text": "⬅️ Back", "callback_data": "menu:vip"}])
        await send_message(chat_id, f"💳 <b>{html_escape(plan['title'])}</b> — {disp}\n\n<b>Payment Guide ({c['name']}):</b>\n{c['how']}", {"inline_keyboard": rows})
        return

    if data.startswith("pay:"):
        _, plan_name, provider = data.split(":", 2)
        await answer_callback(cid)
        if provider == "mpesa":
            set_session(user_id, "WAIT_MPESA_PHONE", plan_name)
            await send_message(chat_id, "📱 Enter your Kenyan Safaricom number (<code>0712345678</code>) or /cancel:")
        elif provider == "crypto":
            await show_crypto_checkout_menu(chat_id, user_id, plan_name, view="home")
        else:
            await create_manual_order_and_prompt(user_id, plan_name, provider)
        return

    if data.startswith("buybtn:"):
        _, raw_id, method = data.split(":", 2)
        if not raw_id.isdigit():
            await answer_callback(cid, "Invalid ticket")
            return
        b = get_custom_button(int(raw_id))
        if not b or not b["is_active"]:
            await answer_callback(cid, "Unavailable")
            return
        await answer_callback(cid)
        offer_key = f"btn:{b['id']}"
        if method == "mpesa":
            set_session(user_id, "WAIT_MPESA_PHONE", offer_key)
            await send_message(
                chat_id,
                f"📲 <b>Pay KES {int(b['price']):,} for {html_escape(b['label'])}</b>\n\n"
                "Enter your Kenyan Safaricom number (e.g. <code>0712345678</code>) or /cancel:",
            )
        elif method == "crypto":
            await show_crypto_checkout_menu(chat_id, user_id, offer_key, view="home")
        else:
            await create_manual_order_and_prompt(user_id, offer_key, method)
        return

    if data.startswith("cryptolist|"):
        parts = data.split("|")
        if len(parts) == 4:
            _, offer_key, view, pg = parts
            await answer_callback(cid)
            await show_crypto_checkout_menu(chat_id, user_id, offer_key, view=view, page=int(pg) if pg.isdigit() else 0, message_id=message_id)
        return

    if data.startswith("cryptopay|"):
        parts = data.split("|", 2)
        if len(parts) == 3:
            _, offer_key, ticker = parts
            await answer_callback(cid, "Generating deposit address...")
            await start_cryptapi_payment(user_id, chat_id, offer_key, ticker)
        return

    if data.startswith("cryptocheck:"):
        ext_ref = data.split(":", 1)[1]
        with connect_db() as db:
            order = db.execute("SELECT * FROM orders WHERE external_ref=? AND user_id=?", (ext_ref, user_id)).fetchone()
        if not order:
            await answer_callback(cid, "Order not found")
            return
        if order["status"] == "PAID":
            await answer_callback(cid, "Already paid!")
            return
        await answer_callback(cid, "Checking blockchain...")
        try:
            res = await verify_cryptapi_order_via_logs(order)
            if res == "NO_TX":
                await send_message(
                    chat_id,
                    f"⏳ No incoming transaction detected yet for <code>{ext_ref}</code>.\n"
                    f"Send <code>{order['crypto_amount']}</code> to <code>{order['crypto_address_in']}</code> and check again in 30–60s.",
                )
            elif res == "PENDING_CONFIRMATION":
                await send_message(chat_id, f"⏳ Transaction detected for <code>{ext_ref}</code> — waiting for blockchain confirmation!")
        except Exception:
            await send_message(chat_id, "⚠️ Could not query CryptAPI logs right now. Try again shortly.")
        return

    if data.startswith("manualtill:"):
        ext_ref = data.split(":", 1)[1]
        with connect_db() as db:
            order = db.execute("SELECT * FROM orders WHERE external_ref=? AND user_id=?", (ext_ref, user_id)).fetchone()
        if not order:
            await answer_callback(cid, "Order not found")
            return
        await answer_callback(cid)
        await send_manual_till_fallback(user_id, ext_ref, format_order_item_name(order["plan"]), order["amount"])
        return

    if data.startswith("proof:"):
        ext_ref = data.split(":", 1)[1]
        with connect_db() as db:
            order = db.execute("SELECT * FROM orders WHERE external_ref=? AND user_id=?", (ext_ref, user_id)).fetchone()
        if not order:
            await answer_callback(cid, "Order not found")
            return
        await answer_callback(cid)
        set_session(user_id, "WAIT_PAYMENT_PROOF", ext_ref)
        await send_message(
            chat_id,
            f"📸 <b>SEND PAYMENT SCREENSHOT OR PDF</b>\n\n"
            f"Order Ref: <code>{ext_ref}</code>\n"
            "Send your payment screenshot (photo or PDF/file receipt) right here now (or send /cancel):",
        )
        return

    if data.startswith("verify:"):
        ext_ref = data.split(":", 1)[1]
        with connect_db() as db:
            order = db.execute("SELECT * FROM orders WHERE external_ref=? AND user_id=?", (ext_ref, user_id)).fetchone()
        if not order or not order["payhero_reference"]:
            await answer_callback(cid, "Processing...")
            return
        await answer_callback(cid, "Checking...")
        try:
            verified = await verify_payhero_transaction(order["payhero_reference"])
            res = await finalize_paid_order(order, verified=verified)
            if res == "PENDING":
                till_no = get_setting("mpesa_till_number", DEFAULT_MPESA_TILL)
                await send_message(
                    user_id,
                    f"⏳ Still pending on M-Pesa. Tap Verify again shortly, or pay manually via Till <code>{till_no}</code> and send your screenshot/PDF.",
                )
        except Exception:
            await send_message(user_id, "⚠️ Could not reach PayHero status right now. Try again in a moment or upload screenshot.")
        return

    # ---- STRICT ADMIN-ONLY CALLBACKS (POST PICKER & ADMIN DASHBOARD) ----
    if data.startswith("postpick:"):
        if not is_admin_private_chat(user_id, chat_id, chat_type):
            await answer_callback(cid, "⛔ Admin access only.")
            await report_unauthorized_admin_attempt(user_id, f"CB:{data}")
            return
        parts = data.split(":")
        sub = parts[1]
        sess = get_session(user_id)
        picker = {"mode": "custom", "btn_ids": [], "footer": True, "react": True}
        if sess and sess["state"] == "POST_PICKER":
            try:
                picker = json.loads(sess["payload"])
            except Exception:
                pass

        if sub == "toggle" and len(parts) == 3 and parts[2].isdigit():
            bid = int(parts[2])
            if bid in picker["btn_ids"]:
                picker["btn_ids"].remove(bid)
            else:
                picker["btn_ids"].append(bid)
            await answer_callback(cid)
            await show_post_button_picker(chat_id, user_id, picker, message_id=message_id)
        elif sub == "all":
            picker["btn_ids"] = [int(b["id"]) for b in get_all_custom_buttons(only_active=True)]
            await answer_callback(cid, "All selected")
            await show_post_button_picker(chat_id, user_id, picker, message_id=message_id)
        elif sub == "none":
            picker["btn_ids"] = []
            await answer_callback(cid, "Cleared")
            await show_post_button_picker(chat_id, user_id, picker, message_id=message_id)
        elif sub == "footer":
            picker["footer"] = not picker.get("footer", True)
            await answer_callback(cid)
            await show_post_button_picker(chat_id, user_id, picker, message_id=message_id)
        elif sub == "react":
            picker["react"] = not picker.get("react", True)
            await answer_callback(cid)
            await show_post_button_picker(chat_id, user_id, picker, message_id=message_id)
        elif sub == "next":
            mode = picker.get("mode", "custom")
            if mode == "custom":
                await answer_callback(cid)
                set_session(user_id, "WAIT_CHANNEL_POST", json.dumps(picker))
                await send_message(
                    chat_id,
                    f"📝 <b>STEP 2: SEND POST CONTENT</b>\n\n"
                    f"Selected buttons: <b>{len(picker.get('btn_ids', []))}</b>\n"
                    "Now send the text message or photo+caption to publish to the Main Channel (or /cancel):",
                )
            elif mode == "vip_promo":
                clear_session(user_id)
                await answer_callback(cid, "Publishing...")
                await publish_vip_post(
                    selected_btn_ids=picker.get("btn_ids", []),
                    include_footer=picker.get("footer", True),
                    include_reactions=picker.get("react", True),
                )
                log_audit(user_id, "VIP_PROMO_POST", f"btns={picker.get('btn_ids', [])}")
                await send_message(chat_id, "✅ VIP Promo published to Main Channel!", admin_keyboard(user_id))
            elif mode == "free_matches":
                clear_session(user_id)
                await answer_callback(cid, "Publishing...")
                res = await publish_match_batch(
                    "FREE",
                    selected_btn_ids=picker.get("btn_ids", []),
                    include_footer=picker.get("footer", True),
                    include_reactions=picker.get("react", True),
                )
                if res:
                    log_audit(user_id, "PUB_FREE_MATCHES", f"btns={picker.get('btn_ids', [])}")
                    await send_message(chat_id, "✅ Published queued FREE matches to Main Channel!", admin_keyboard(user_id))
                else:
                    await send_message(chat_id, "ℹ️ No queued FREE matches found.", admin_keyboard(user_id))
        return

    if data.startswith("admin:"):
        if not is_admin_private_chat(user_id, chat_id, chat_type):
            await answer_callback(cid, "⛔ Admin access only.")
            await report_unauthorized_admin_attempt(user_id, f"CB:{data}")
            return
        parts = data.split(":")
        action = parts[1]

        if action == "home":
            clear_session(user_id)
            await answer_callback(cid)
            await send_message(chat_id, f"⚡ <b>{PROJECT_NAME} ADMIN</b>", admin_keyboard(user_id))
        elif action == "stats":
            await answer_callback(cid)
            await show_admin_stats(chat_id, user_id)
        elif action == "matches":
            await answer_callback(cid)
            await show_admin_matches(chat_id, user_id)
        elif action == "pub_free":
            with connect_db() as db:
                qf = db.execute("SELECT COUNT(*) AS n FROM matches WHERE tier='FREE' AND publish_status='QUEUED'").fetchone()["n"]
            if qf == 0:
                await answer_callback(cid, "No queued FREE matches")
                return
            await answer_callback(cid)
            await show_post_button_picker(chat_id, user_id, {"mode": "free_matches", "btn_ids": [], "footer": True, "react": True})
        elif action == "pub_vip":
            await answer_callback(cid)
            res = await publish_match_batch("VIP", selected_btn_ids=[], include_footer=True, include_reactions=False)
            await send_message(chat_id, "✅ Published queued VIP matches!" if res else "ℹ️ No queued VIP matches.", admin_keyboard(user_id))
        elif action == "res" and len(parts) ==

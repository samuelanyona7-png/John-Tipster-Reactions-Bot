
import os
import io
import re
import csv
import json
import time
import base64
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
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware

try:
    import psycopg2
    import psycopg2.extras
    import psycopg2.pool
except ImportError:
    psycopg2 = None

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

LOCAL_COUNTRIES = {"KE", "TZ", "UG", "ZM"}

bot_username = None
http_client: httpx.AsyncClient | None = None
_pg_pool = None
_settings_cache = {}
_settings_loaded = False
_fx_cache = {"KES": 1.0}
_fx_cache_ts = 0.0
_cryptapi_catalog_cache = {"fetched_at": None, "coins": {}, "networks": {}}

COUNTRIES = {
    "KE": {
        "flag": "🇰🇪", "name": "Kenya", "currency": "KES", "tz": "Africa/Nairobi", "scope": "LOCAL",
        "methods": ["mpesa", "mpesa_till", "airtel"],
        "how": f"1️⃣ <b>M-Pesa STK Push (PayHero)</b> for instant checkout.\n2️⃣ Or pay via <b>Buy Goods Till: <code>{DEFAULT_MPESA_TILL}</code></b>.\n3️⃣ Or <b>Airtel Money: <code>{DEFAULT_AIRTEL_NUMBER}</code></b>.",
    },
    "TZ": {
        "flag": "🇹🇿", "name": "Tanzania", "currency": "TZS", "tz": "Africa/Dar_es_Salaam", "scope": "LOCAL",
        "methods": ["airtel", "mpesa_tz", "tigo"],
        "how": f"1️⃣ Send via <b>Airtel Money: <code>{DEFAULT_AIRTEL_NUMBER}</code></b> (or contact {BRAND_TAG} for M-Pesa TZ / Mixx).\n2️⃣ Send payment screenshot or PDF receipt here.",
    },
    "UG": {
        "flag": "🇺🇬", "name": "Uganda", "currency": "UGX", "tz": "Africa/Kampala", "scope": "LOCAL",
        "methods": ["airtel", "mtn"],
        "how": f"1️⃣ Send via <b>Airtel Money: <code>{DEFAULT_AIRTEL_NUMBER}</code></b> (or contact {BRAND_TAG} for MTN MoMo).\n2️⃣ Send payment screenshot or PDF receipt here.",
    },
    "ZM": {
        "flag": "🇿🇲", "name": "Zambia", "currency": "ZMW", "tz": "Africa/Lusaka", "scope": "LOCAL",
        "methods": ["airtel", "mtn"],
        "how": f"1️⃣ Send via <b>Airtel Money: <code>{DEFAULT_AIRTEL_NUMBER}</code></b> (or contact {BRAND_TAG} for MTN MoMo).\n2️⃣ Send payment screenshot or PDF receipt here.",
    },
    "INTL": {
        "flag": "🌍", "name": "International (Global)", "currency": "USD", "tz": "UTC", "scope": "INTL",
        "methods": ["crypto", "intl_transfer"],
        "how": f"1️⃣ Pay instantly via <b>Crypto (CryptAPI — 100+ Coins)</b> for automatic activation.\n2️⃣ Or use <b>Card / PayPal / Bank Transfer</b> via {BRAND_TAG}.",
    },
    "US": {"flag": "🇺🇸", "name": "United States", "currency": "USD", "tz": "America/New_York", "scope": "INTL", "methods": ["crypto", "intl_transfer"], "how": f"1️⃣ Pay automatically via <b>Crypto (CryptAPI)</b>.\n2️⃣ Or Card / PayPal via {BRAND_TAG}."},
    "GB": {"flag": "🇬🇧", "name": "United Kingdom", "currency": "GBP", "tz": "Europe/London", "scope": "INTL", "methods": ["crypto", "intl_transfer"], "how": f"1️⃣ Pay automatically via <b>Crypto (CryptAPI)</b>.\n2️⃣ Or UK Bank / PayPal via {BRAND_TAG}."},
    "EU": {"flag": "🇪🇺", "name": "Europe (Eurozone)", "currency": "EUR", "tz": "Europe/Berlin", "scope": "INTL", "methods": ["crypto", "intl_transfer"], "how": f"1️⃣ Pay automatically via <b>Crypto (CryptAPI)</b>.\n2️⃣ Or SEPA / PayPal via {BRAND_TAG}."},
    "NG": {"flag": "🇳🇬", "name": "Nigeria", "currency": "NGN", "tz": "Africa/Lagos", "scope": "INTL", "methods": ["crypto", "ng_bank"], "how": f"1️⃣ Pay automatically via <b>Crypto (CryptAPI)</b>.\n2️⃣ Or Nigeria Bank / OPay / PalmPay via {BRAND_TAG}."},
    "GH": {"flag": "🇬🇭", "name": "Ghana", "currency": "GHS", "tz": "Africa/Accra", "scope": "INTL", "methods": ["crypto", "intl_transfer"], "how": f"1️⃣ Pay automatically via <b>Crypto (CryptAPI)</b>.\n2️⃣ Or international transfer via {BRAND_TAG}."},
    "ZA": {"flag": "🇿🇦", "name": "South Africa", "currency": "ZAR", "tz": "Africa/Johannesburg", "scope": "INTL", "methods": ["crypto", "za_eft"], "how": f"1️⃣ Pay automatically via <b>Crypto (CryptAPI)</b>.\n2️⃣ Or Instant EFT via {BRAND_TAG}."},
    "IN": {"flag": "🇮🇳", "name": "India", "currency": "INR", "tz": "Asia/Kolkata", "scope": "INTL", "methods": ["crypto", "intl_transfer"], "how": f"1️⃣ Pay automatically via <b>Crypto (CryptAPI)</b>.\n2️⃣ Or UPI / International Transfer via {BRAND_TAG}."},
    "CA": {"flag": "🇨🇦", "name": "Canada", "currency": "CAD", "tz": "America/Toronto", "scope": "INTL", "methods": ["crypto", "intl_transfer"], "how": f"1️⃣ Pay automatically via <b>Crypto (CryptAPI)</b>.\n2️⃣ Or Interac / PayPal via {BRAND_TAG}."},
    "AU": {"flag": "🇦🇺", "name": "Australia", "currency": "AUD", "tz": "Australia/Sydney", "scope": "INTL", "methods": ["crypto", "intl_transfer"], "how": f"1️⃣ Pay automatically via <b>Crypto (CryptAPI)</b>.\n2️⃣ Or PayID / PayPal via {BRAND_TAG}."},
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
    "ng_bank": ("🇳🇬 Bank / OPay / PalmPay", "primary"),
    "za_eft": ("🇿🇦 Instant EFT / Capitec", "primary"),
    "intl_transfer": ("💲 Card / PayPal / Bank Transfer", "primary"),
}

ACCUM_CATEGORIES = ["3-5 Odds", "5-10 Odds", "10-15 Odds", "15-20 Odds", "20+ Odds"]

CRYPTO_WALLET_GROUPS = [
    ("evm", "🔷 EVM Chains (ETH, ERC20, BEP20, Polygon, Arbitrum, Optimism, Base, AVAX)", "CRYPTAPI_EVM_ADDRESS"),
    ("tron", "🔴 TRON & TRC20 (TRX, USDT-TRC20, USDC-TRC20)", "CRYPTAPI_TRON_ADDRESS"),
    ("sol", "🟣 Solana & SPL Tokens (SOL, USDT-SOL, USDC-SOL)", "CRYPTAPI_SOL_ADDRESS"),
    ("ton", "💎 TON & Jettons (TON, USDT-TON)", "CRYPTAPI_TON_ADDRESS"),
    ("btc", "🟠 Bitcoin (BTC)", "CRYPTAPI_BTC_ADDRESS"),
    ("ltc", "⚪ Litecoin (LTC)", "CRYPTAPI_LTC_ADDRESS"),
    ("bch", "🟢 Bitcoin Cash (BCH)", "CRYPTAPI_BCH_ADDRESS"),
    ("doge", "🟡 Dogecoin (DOGE)", "CRYPTAPI_DOGE_ADDRESS"),
    ("zec", "🛡️ Zcash (ZEC)", "CRYPTAPI_ZEC_ADDRESS"),
]

CRYPTO_NETWORK_LABELS = {
    "coins": "🪙 Native Coins", "trc20": "🔴 Tron (TRC-20)", "bep20": "🟡 BNB Smart Chain (BEP-20)",
    "erc20": "🔷 Ethereum (ERC-20)", "polygon": "🟣 Polygon", "sol": "🧬 Solana", "ton": "💎 TON",
    "arbitrum": "🔵 Arbitrum One", "optimism": "🔺 Optimism", "base": "🟦 Base",
    "avax-c": "❄️ Avalanche-C", "linea": "📏 Linea", "monad": "🟪 Monad", "berachain": "🐻 Berachain",
}

POPULAR_CRYPTO_TICKERS = [
    "trc20/usdt", "bep20/usdt", "sol/usdt", "ton/usdt", "polygon/usdt", "erc20/usdt",
    "trc20/usdc", "bep20/usdc", "sol/usdc", "base/usdc", "btc", "eth", "ltc", "trx", "sol/sol", "ton/ton", "doge", "bch",
]


def check_settings():
    settings = {
        "TELEGRAM_BOT_TOKEN": BOT_TOKEN, "ADMIN_USER_ID": ADMIN_USER_ID,
        "CHANNEL_ID": CHANNEL_ID, "VIP_CHANNEL_ID": VIP_CHANNEL_ID,
        "WEBHOOK_SECRET": WEBHOOK_SECRET, "PAYHERO_CHANNEL_ID": PAYHERO_CHANNEL_ID,
        "PUBLIC_URL / RENDER_EXTERNAL_URL": PUBLIC_URL,
    }
    if not PAYHERO_AUTH_TOKEN and not (PAYHERO_API_USERNAME and PAYHERO_API_PASSWORD):
        settings["PAYHERO_AUTH_TOKEN (or PAYHERO_API_USERNAME + PAYHERO_API_PASSWORD)"] = ""
    missing = [k for k, v in settings.items() if not v]
    if missing:
        raise RuntimeError("Missing environment variables: " + ", ".join(missing))
    if not ADMIN_USER_ID.isdigit() or not PAYHERO_CHANNEL_ID.isdigit():
        raise RuntimeError("ADMIN_USER_ID and PAYHERO_CHANNEL_ID must be numeric")
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
        http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=8.0),
            limits=httpx.Limits(max_connections=60, max_keepalive_connections=20),
        )
    return http_client


def is_admin(user_id):
    return bool(ADMIN_USER_ID) and ADMIN_USER_ID.isdigit() and str(user_id) == str(ADMIN_USER_ID)


def is_admin_private_chat(user_id, chat_id, chat_type="private"):
    return is_admin(user_id) and chat_type == "private" and str(chat_id) == str(ADMIN_USER_ID)


# --------------------------------------------------
# POOLED DATABASE LAYER + SETTINGS CACHE
# --------------------------------------------------

def get_pg_pool():
    global _pg_pool
    if _pg_pool is None and USE_POSTGRES:
        _pg_pool = psycopg2.pool.ThreadedConnectionPool(2, 16, DATABASE_URL, connect_timeout=8)
    return _pg_pool


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
        pool = get_pg_pool()
        raw = pool.getconn()
        try:
            if raw.closed:
                pool.putconn(raw, close=True)
                raw = pool.getconn()
        except Exception:
            raw = psycopg2.connect(DATABASE_URL, connect_timeout=8)
        db = DBConn(raw)
        try:
            yield db
            raw.commit()
        except Exception:
            try:
                raw.rollback()
            except Exception:
                pass
            raise
        finally:
            try:
                pool.putconn(raw)
            except Exception:
                raw.close()
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


def reload_settings_cache():
    global _settings_cache, _settings_loaded
    with connect_db() as db:
        rows = db.execute("SELECT key, value FROM app_settings").fetchall()
    _settings_cache = {r["key"]: r["value"] for r in rows}
    _settings_loaded = True


def init_db():
    pk_auto = "SERIAL PRIMARY KEY" if USE_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"
    with connect_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY, username TEXT, first_name TEXT,
                country TEXT NOT NULL DEFAULT '', country_confirmed INTEGER NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'USD', timezone_name TEXT NOT NULL DEFAULT 'UTC',
                referred_by BIGINT, referral_rewarded INTEGER NOT NULL DEFAULT 0,
                is_blocked INTEGER NOT NULL DEFAULT 0, notify_free INTEGER NOT NULL DEFAULT 1,
                notify_vip INTEGER NOT NULL DEFAULT 1, notify_news INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS plans (
                name TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '', price INTEGER NOT NULL,
                days INTEGER NOT NULL, benefits TEXT NOT NULL DEFAULT '', is_lifetime INTEGER NOT NULL DEFAULT 0
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS intl_plans (
                name TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '', price_usd DOUBLE PRECISION NOT NULL,
                days INTEGER NOT NULL, benefits TEXT NOT NULL DEFAULT '', is_lifetime INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1
            )
        """)
        db.execute("CREATE TABLE IF NOT EXISTS sessions (user_id BIGINT PRIMARY KEY, state TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}')")
        if USE_POSTGRES:
            has_old = db.execute("SELECT 1 FROM information_schema.columns WHERE table_name='sessions' AND column_name='plan'").fetchone()
        else:
            has_old = "plan" in [r["name"] for r in db.execute("PRAGMA table_info(sessions)").fetchall()]
        if has_old:
            db.execute("DROP TABLE sessions")
            db.execute("CREATE TABLE sessions (user_id BIGINT PRIMARY KEY, state TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}')")

        db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                external_ref TEXT PRIMARY KEY, user_id BIGINT NOT NULL, plan TEXT NOT NULL,
                amount INTEGER NOT NULL, amount_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
                market_scope TEXT NOT NULL DEFAULT 'LOCAL',
                currency TEXT NOT NULL DEFAULT 'KES', local_amount TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL, provider TEXT NOT NULL DEFAULT 'mpesa', status TEXT NOT NULL,
                payhero_reference TEXT, checkout_id TEXT, mpesa_receipt TEXT,
                proof_file_id TEXT, proof_type TEXT NOT NULL DEFAULT 'photo',
                crypto_ticker TEXT, crypto_address_in TEXT, crypto_amount TEXT,
                crypto_txid TEXT, crypto_uuid TEXT, callback_nonce TEXT, created_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id BIGINT PRIMARY KEY, plan TEXT NOT NULL, expires_at TEXT NOT NULL,
                status TEXT NOT NULL, is_lifetime INTEGER NOT NULL DEFAULT 0, reminded INTEGER NOT NULL DEFAULT 0
            )
        """)
        db.execute(f"""
            CREATE TABLE IF NOT EXISTS matches (
                id {pk_auto}, tier TEXT NOT NULL, match_name TEXT NOT NULL, market TEXT NOT NULL DEFAULT '1X2',
                tip TEXT NOT NULL, odds TEXT NOT NULL, match_date TEXT NOT NULL, kickoff_time TEXT NOT NULL,
                publish_status TEXT NOT NULL DEFAULT 'QUEUED', result TEXT NOT NULL DEFAULT 'PENDING',
                scheduled_for TEXT, channel_msg_id BIGINT, created_at TEXT NOT NULL
            )
        """)
        db.execute(f"""
            CREATE TABLE IF NOT EXISTS accumulators (
                id {pk_auto}, category TEXT NOT NULL, selections_json TEXT NOT NULL, total_odds TEXT NOT NULL,
                risk_note TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'PENDING_ADMIN',
                created_by BIGINT NOT NULL, channel_msg_id BIGINT, created_at TEXT NOT NULL
            )
        """)
        db.execute("CREATE TABLE IF NOT EXISTS post_reactions (channel_msg_id BIGINT NOT NULL, user_id BIGINT NOT NULL, emoji TEXT NOT NULL, PRIMARY KEY (channel_msg_id, user_id, emoji))")
        db.execute("CREATE TABLE IF NOT EXISTS exchange_rates (currency TEXT PRIMARY KEY, rate_from_kes DOUBLE PRECISION NOT NULL, updated_at TEXT NOT NULL)")
        db.execute(f"""
            CREATE TABLE IF NOT EXISTS custom_buttons (
                id {pk_auto}, label TEXT NOT NULL, url TEXT NOT NULL DEFAULT '', button_type TEXT NOT NULL DEFAULT 'TICKET',
                price INTEGER NOT NULL DEFAULT 0, intl_price_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
                days INTEGER NOT NULL DEFAULT 1, description TEXT NOT NULL DEFAULT '',
                style TEXT NOT NULL DEFAULT 'primary', is_active INTEGER NOT NULL DEFAULT 1
            )
        """)
        db.execute(f"""
            CREATE TABLE IF NOT EXISTS ticket_media (
                id {pk_auto}, offer_key TEXT NOT NULL, file_id TEXT NOT NULL,
                media_type TEXT NOT NULL DEFAULT 'photo', caption TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS channel_posts (
                channel_msg_id BIGINT PRIMARY KEY, button_ids TEXT NOT NULL DEFAULT '[]',
                include_reactions INTEGER NOT NULL DEFAULT 1, include_footer INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
            )
        """)
        db.execute("CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute(f"CREATE TABLE IF NOT EXISTS audit_logs (id {pk_auto}, actor_id BIGINT NOT NULL, action TEXT NOT NULL, details TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)")

        for tbl, col, defn in [
            ("users", "country", "TEXT NOT NULL DEFAULT ''"), ("users", "country_confirmed", "INTEGER NOT NULL DEFAULT 0"),
            ("users", "currency", "TEXT NOT NULL DEFAULT 'USD'"), ("users", "timezone_name", "TEXT NOT NULL DEFAULT 'UTC'"),
            ("plans", "title", "TEXT NOT NULL DEFAULT ''"), ("plans", "benefits", "TEXT NOT NULL DEFAULT ''"), ("plans", "is_lifetime", "INTEGER NOT NULL DEFAULT 0"),
            ("orders", "amount_usd", "DOUBLE PRECISION NOT NULL DEFAULT 0"), ("orders", "market_scope", "TEXT NOT NULL DEFAULT 'LOCAL'"),
            ("orders", "provider", "TEXT NOT NULL DEFAULT 'mpesa'"), ("orders", "mpesa_receipt", "TEXT"),
            ("orders", "proof_file_id", "TEXT"), ("orders", "proof_type", "TEXT NOT NULL DEFAULT 'photo'"),
            ("orders", "currency", "TEXT NOT NULL DEFAULT 'KES'"), ("orders", "local_amount", "TEXT NOT NULL DEFAULT ''"),
            ("orders", "crypto_ticker", "TEXT"), ("orders", "crypto_address_in", "TEXT"), ("orders", "crypto_amount", "TEXT"),
            ("orders", "crypto_txid", "TEXT"), ("orders", "crypto_uuid", "TEXT"), ("orders", "callback_nonce", "TEXT"),
            ("subscriptions", "is_lifetime", "INTEGER NOT NULL DEFAULT 0"), ("subscriptions", "reminded", "INTEGER NOT NULL DEFAULT 0"),
            ("matches", "market", "TEXT NOT NULL DEFAULT '1X2'"),
            ("custom_buttons", "button_type", "TEXT NOT NULL DEFAULT 'URL'"), ("custom_buttons", "price", "INTEGER NOT NULL DEFAULT 0"),
            ("custom_buttons", "intl_price_usd", "DOUBLE PRECISION NOT NULL DEFAULT 0"), ("custom_buttons", "days", "INTEGER NOT NULL DEFAULT 1"),
            ("custom_buttons", "description", "TEXT NOT NULL DEFAULT ''"), ("custom_buttons", "style", "TEXT NOT NULL DEFAULT 'primary'"),
            ("custom_buttons", "is_active", "INTEGER NOT NULL DEFAULT 1"),
        ]:
            ensure_column(db, tbl, col, defn)

        for row in [
            ("daily", "Daily VIP", 100, 1, "24 Hours VIP access • Daily single & combo picks", 0),
            ("weekly", "Weekly VIP", 300, 7, "7 Days VIP access • Daily slips & analysis alerts", 0),
            ("monthly", "Monthly VIP", 1000, 30, "30 Days VIP access • Full ticket access • Best value", 0),
            ("premium", "Premium VIP", 2500, 90, "90 Days VIP access • All accumulator slips • Priority support", 0),
            ("lifetime", "Lifetime VIP", 10000, 36500, "Permanent VIP access • Never expires • All future updates", 1),
        ]:
            db.execute("INSERT INTO plans(name,title,price,days,benefits,is_lifetime) VALUES(?,?,?,?,?,?) ON CONFLICT(name) DO NOTHING", row)

        for row in [
            ("intl_daily", "Global Daily VIP", 5.0, 1, "24 Hours Global VIP access • High-confidence picks", 0, 1),
            ("intl_weekly", "Global Weekly VIP", 15.0, 7, "7 Days Global VIP access • Daily multi-bet tickets", 0, 1),
            ("intl_monthly", "Global Monthly VIP", 40.0, 30, "30 Days Global VIP access • Full access • Best value", 0, 1),
            ("intl_premium", "Global Premium VIP", 99.0, 90, "90 Days Global VIP access • Priority support", 0, 1),
            ("intl_lifetime", "Global Lifetime VIP", 299.0, 36500, "Permanent Global VIP access • Never expires", 1, 1),
        ]:
            db.execute("INSERT INTO intl_plans(name,title,price_usd,days,benefits,is_lifetime,is_active) VALUES(?,?,?,?,?,?,?) ON CONFLICT(name) DO NOTHING", row)

        defaults = {
            "main_channel_id": CHANNEL_ID, "vip_channel_id": VIP_CHANNEL_ID,
            "main_channel_url": MAIN_CHANNEL_URL or (f"https://t.me/{CHANNEL_ID.lstrip('@')}" if CHANNEL_ID.startswith("@") else ""),
            "payhero_channel_id": PAYHERO_CHANNEL_ID, "env_payhero_channel_id": PAYHERO_CHANNEL_ID,
            "mpesa_till_number": DEFAULT_MPESA_TILL, "airtel_money_number": DEFAULT_AIRTEL_NUMBER,
            "auto_confirm_screenshot": "0", "referral_reward_days": "2",
            "vip_odds_info": "🔥 Daily VIP Matches: Curated Football Picks & Multi-Bet Tickets",
            "admin_contact_url": f"https://t.me/{BRAND_TAG.lstrip('@')}",
            "website_url": WEBSITE_URL_ENV or DEFAULT_WEBSITE_URL,
            "cryptapi_wallets": "{}", "cryptapi_confirmations": CRYPTAPI_CONFIRMATIONS or "1",
            "cryptapi_callback_base": CRYPTAPI_CALLBACK_BASE_URL,
            "intl_payment_note": f"Pay automatically via Crypto (CryptAPI) or message {BRAND_TAG} for PayPal / Card / Bank Transfer.",
        }
        for k, v in defaults.items():
            db.execute("INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO NOTHING", (k, v))

        prev_env_ph = db.execute("SELECT value FROM app_settings WHERE key='env_payhero_channel_id'").fetchone()
        if PAYHERO_CHANNEL_ID and (not prev_env_ph or prev_env_ph["value"] != PAYHERO_CHANNEL_ID):
            db.execute("INSERT INTO app_settings(key,value) VALUES('payhero_channel_id',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (PAYHERO_CHANNEL_ID,))
            db.execute("INSERT INTO app_settings(key,value) VALUES('env_payhero_channel_id',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (PAYHERO_CHANNEL_ID,))

        seeded = db.execute("SELECT value FROM app_settings WHERE key='seeded_ticket_buttons'").fetchone()
        if not seeded:
            for lbl, url, btype, price, iprice, days, desc, style in [
                ("🔵 Get Blue Ticket", "", "TICKET", 500, 10.0, 1, "Blue VIP Ticket — Curated high-confidence picks", "primary"),
                ("🎫 Get Daily Ticket", "", "TICKET", 200, 5.0, 1, "Daily Ticket — Today's top selections", "success"),
                ("🥇 Golden Ticket", "", "TICKET", 1000, 20.0, 3, "Golden VIP Ticket — Premium multi-match slip", "danger"),
                ("🇲🇰 Get Macedonia Ticket", "", "TICKET", 1500, 25.0, 1, "Macedonia Special Ticket — Exclusive fixture slip", "primary"),
                ("🇪🇺 Euro Ticket", "", "TICKET", 800, 15.0, 1, "European Leagues Ticket — Top Euro picks", "success"),
            ]:
                db.execute("INSERT INTO custom_buttons(label,url,button_type,price,intl_price_usd,days,description,style,is_active) VALUES(?,?,?,?,?,?,?,?,1)", (lbl, url, btype, price, iprice, days, desc, style))
            db.execute("INSERT INTO app_settings(key,value) VALUES('seeded_ticket_buttons','1') ON CONFLICT(key) DO UPDATE SET value='1'")

        db.execute("INSERT INTO exchange_rates(currency,rate_from_kes,updated_at) VALUES('KES',1.0,?) ON CONFLICT(currency) DO NOTHING", (utcnow().isoformat(),))
    reload_settings_cache()


def log_audit(actor_id, action, details=""):
    try:
        with connect_db() as db:
            db.execute("INSERT INTO audit_logs(actor_id,action,details,created_at) VALUES(?,?,?,?)", (int(actor_id), action, str(details)[:500], utcnow().isoformat()))
    except Exception:
        pass


def get_setting(key, fallback=""):
    if not _settings_loaded:
        reload_settings_cache()
    val = _settings_cache.get(key, fallback)
    return val if (val is not None and val != "") else fallback


def set_setting(key, value):
    with connect_db() as db:
        db.execute("INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    _settings_cache[key] = str(value)


def get_website_url():
    url = get_setting("website_url", DEFAULT_WEBSITE_URL).strip()
    return ("https://" + url) if (url and not url.startswith(("http://", "https://"))) else url


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
        existing = db.execute("SELECT user_id, username, first_name FROM users WHERE user_id=?", (user_id,)).fetchone()
        if existing:
            if existing["username"] != username or existing["first_name"] != first_name:
                db.execute("UPDATE users SET username=?, first_name=? WHERE user_id=?", (username, first_name, user_id))
        else:
            db.execute("INSERT INTO users(user_id,username,first_name,country,country_confirmed,currency,timezone_name,referred_by,created_at) VALUES(?,?,?,'',0,'KES','UTC',?,?)", (user_id, username, first_name, referred_by, utcnow().isoformat()))


def set_session(user_id, state, payload=""):
    with connect_db() as db:
        db.execute("INSERT INTO sessions(user_id,state,payload) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET state=excluded.state, payload=excluded.payload", (user_id, state, payload))


def get_session(user_id):
    with connect_db() as db:
        return db.execute("SELECT * FROM sessions WHERE user_id=?", (user_id,)).fetchone()


def clear_session(user_id):
    with connect_db() as db:
        db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))


def has_active_vip(user_id):
    with connect_db() as db:
        sub = db.execute("SELECT status, is_lifetime, expires_at FROM subscriptions WHERE user_id=?", (user_id,)).fetchone()
    if not sub or sub["status"] != "ACTIVE":
        return False
    return True if sub["is_lifetime"] else (parse_utc(sub["expires_at"]) > utcnow())


# --------------------------------------------------
# OFFERS (ISOLATED LOCAL VS INTERNATIONAL) & TICKET MEDIA
# --------------------------------------------------

def get_all_custom_buttons(only_active=True):
    with connect_db() as db:
        return db.execute("SELECT * FROM custom_buttons WHERE is_active=1 ORDER BY id" if only_active else "SELECT * FROM custom_buttons ORDER BY id").fetchall()


def get_custom_button(btn_id):
    with connect_db() as db:
        return db.execute("SELECT * FROM custom_buttons WHERE id=?", (int(btn_id),)).fetchone()


def get_plan(plan_name):
    with connect_db() as db:
        return db.execute("SELECT * FROM plans WHERE name=?", (plan_name,)).fetchone()


def get_intl_plan(plan_name):
    with connect_db() as db:
        return db.execute("SELECT * FROM intl_plans WHERE name=?", (plan_name,)).fetchone()


def get_all_intl_plans(only_active=True):
    with connect_db() as db:
        return db.execute("SELECT * FROM intl_plans WHERE is_active=1 ORDER BY price_usd" if only_active else "SELECT * FROM intl_plans ORDER BY price_usd").fetchall()


def media_key_for_offer(offer_key):
    s = str(offer_key)
    return ("btn:" + s.split(":", 1)[1]) if s.startswith("ibtn:") else s


def get_offer_media(offer_key):
    with connect_db() as db:
        return db.execute("SELECT * FROM ticket_media WHERE offer_key=? ORDER BY id", (media_key_for_offer(offer_key),)).fetchall()


def count_offer_media(offer_key):
    with connect_db() as db:
        return int(db.execute("SELECT COUNT(*) AS n FROM ticket_media WHERE offer_key=?", (media_key_for_offer(offer_key),)).fetchone()["n"])


def resolve_offer(key):
    k = str(key or "")
    if k.startswith("btn:") or k.startswith("ibtn:"):
        is_intl = k.startswith("ibtn:")
        raw_id = k.split(":", 1)[1]
        if not raw_id.isdigit():
            return None
        b = get_custom_button(int(raw_id))
        if not b or not b["is_active"] or b["button_type"] != "TICKET":
            return None
        price_kes = int(b["price"])
        price_usd = float(b["intl_price_usd"] or 0)
        if is_intl and price_usd <= 0:
            return None
        if not is_intl and price_kes <= 0:
            return None
        return {
            "kind": "button", "scope": "INTL" if is_intl else "LOCAL", "key": k, "btn_id": int(b["id"]),
            "title": b["label"], "price": price_kes, "price_usd": price_usd,
            "days": int(b["days"]), "is_lifetime": 0, "description": b["description"] or "",
        }
    if k.startswith("intl:"):
        pname = k.split(":", 1)[1]
        ip = get_intl_plan(pname)
        if not ip or not ip["is_active"]:
            return None
        return {
            "kind": "intl_plan", "scope": "INTL", "key": k, "btn_id": None,
            "title": ip["title"] or pname.title(), "price": 0, "price_usd": float(ip["price_usd"]),
            "days": int(ip["days"]), "is_lifetime": int(ip["is_lifetime"]), "description": ip["benefits"] or "",
        }
    p = get_plan(k)
    if not p:
        return None
    return {
        "kind": "plan", "scope": "LOCAL", "key": p["name"], "btn_id": None,
        "title": p["title"] or f"{p['name'].title()} VIP", "price": int(p["price"]), "price_usd": 0.0,
        "days": int(p["days"]), "is_lifetime": int(p["is_lifetime"]), "description": p["benefits"] or "",
    }


def format_order_item_name(plan_key):
    k = str(plan_key or "")
    if k.startswith("btn:") or k.startswith("ibtn:"):
        raw_id = k.split(":", 1)[1]
        if raw_id.isdigit():
            b = get_custom_button(int(raw_id))
            if b:
                return f"{b['label']} ({'Intl' if k.startswith('ibtn:') else 'Local'})"
        return f"Ticket #{raw_id}"
    if k.startswith("intl:"):
        ip = get_intl_plan(k.split(":", 1)[1])
        return ip["title"] if ip else k
    p = get_plan(k)
    return p["title"] if (p and p["title"]) else k.title()


# --------------------------------------------------
# LIVE EXCHANGE RATES & COUNTRY HELPERS
# --------------------------------------------------

def _load_fx_into_mem():
    global _fx_cache, _fx_cache_ts
    with connect_db() as db:
        rows = db.execute("SELECT currency, rate_from_kes FROM exchange_rates").fetchall()
    _fx_cache = {"KES": 1.0}
    for r in rows:
        _fx_cache[r["currency"]] = float(r["rate_from_kes"])
    _fx_cache_ts = time.time()


async def refresh_exchange_rates(force=False):
    global _fx_cache_ts
    if not force:
        with connect_db() as db:
            usd_row = db.execute("SELECT updated_at FROM exchange_rates WHERE currency='USD'").fetchone()
        if usd_row and (utcnow() - parse_utc(usd_row["updated_at"])).total_seconds() / 3600.0 < FX_CACHE_HOURS:
            _load_fx_into_mem()
            return True
    url = f"https://v6.exchangerate-api.com/v6/{EXCHANGE_RATE_API_KEY}/latest/KES" if EXCHANGE_RATE_API_KEY else "https://open.er-api.com/v6/latest/KES"
    try:
        client = await get_http()
        resp = await client.get(url, timeout=12)
        rates = (resp.json().get("conversion_rates") or resp.json().get("rates") or {})
        if not rates:
            return False
        now_iso = utcnow().isoformat()
        with connect_db() as db:
            for code in CURRENCIES:
                val = 1.0 if code == "KES" else rates.get(code)
                if val and float(val) > 0:
                    db.execute("INSERT INTO exchange_rates(currency,rate_from_kes,updated_at) VALUES(?,?,?) ON CONFLICT(currency) DO UPDATE SET rate_from_kes=excluded.rate_from_kes, updated_at=excluded.updated_at", (code, float(val), now_iso))
        _load_fx_into_mem()
        return True
    except Exception as e:
        log.warning("FX refresh skipped: %s", e)
        return False


def get_cached_rate(currency_code):
    code = (currency_code or "KES").upper()
    if code == "KES":
        return 1.0
    if time.time() - _fx_cache_ts > 300:
        _load_fx_into_mem()
    return _fx_cache.get(code)


def format_converted_price(kes_amount, display_currency):
    code = (display_currency or "KES").upper()
    rate = get_cached_rate(code)
    if code == "KES" or rate is None:
        return f"KES {int(kes_amount):,}", False
    meta = CURRENCIES.get(code, {"symbol": f"{code} ", "decimals": 2})
    conv = float(kes_amount) * rate
    fmt = f"{meta['symbol']}{int(round(conv)):,}" if meta["decimals"] == 0 else f"{meta['symbol']}{conv:,.2f}"
    return f"{fmt} {code} (KES {int(kes_amount):,})", True


def format_usd_price(usd_amount, display_currency="USD"):
    usd = float(usd_amount or 0)
    code = (display_currency or "USD").upper()
    if code == "USD":
        return f"${usd:,.2f} USD", False
    r_usd = get_cached_rate("USD")
    r_target = get_cached_rate(code)
    if not r_usd or not r_target:
        return f"${usd:,.2f} USD", False
    conv = usd * (r_target / r_usd)
    meta = CURRENCIES.get(code, {"symbol": f"{code} ", "decimals": 2})
    fmt = f"{meta['symbol']}{int(round(conv)):,}" if meta["decimals"] == 0 else f"{meta['symbol']}{conv:,.2f}"
    return f"${usd:,.2f} USD (~{fmt} {code})", True


def get_user_prefs(user_id):
    u = get_user(user_id)
    if not u:
        return {"country": "", "confirmed": False, "currency": "KES", "tz": "UTC", "is_intl": False}
    code = u["country"] if u["country"] in COUNTRIES else ""
    curr = u["currency"] if u["currency"] in CURRENCIES else "KES"
    is_intl = bool(code and code not in LOCAL_COUNTRIES) or (not code and curr not in ("KES", "TZS", "UGX", "ZMW"))
    return {"country": code, "confirmed": bool(u["country_confirmed"]), "currency": curr, "tz": u["timezone_name"] or "UTC", "is_intl": is_intl}


async def reverse_geocode_country(lat, lon):
    client = await get_http()
    resp = await client.get("https://nominatim.openstreetmap.org/reverse", params={"format": "jsonv2", "lat": lat, "lon": lon, "zoom": 3}, headers={"User-Agent": "PrimeTipsEmpireBot/1.0"}, timeout=10)
    if resp.status_code != 200:
        return None
    iso2 = ((resp.json().get("address") or {}).get("country_code") or "").upper()
    if iso2 in EUROZONE_ISO:
        return "EU"
    return iso2 if iso2 in COUNTRIES else "INTL"


# --------------------------------------------------
# TELEGRAM API
# --------------------------------------------------

async def telegram(method, data):
    client = await get_http()
    response = await client.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", json=data)
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
    client = await get_http()
    resp = await client.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument", data={"chat_id": str(chat_id), "caption": caption, "parse_mode": "HTML"}, files={"document": (filename, content_bytes, "text/csv")})
    res = resp.json()
    if not res.get("ok"):
        raise RuntimeError(f"sendDocument failed: {res.get('description', res)}")
    return res.get("result")


async def telegram_send_photo_bytes(chat_id, filename, photo_bytes, caption="", keyboard=None):
    client = await get_http()
    data = {"chat_id": str(chat_id), "caption": caption[:1020], "parse_mode": "HTML"}
    if keyboard:
        data["reply_markup"] = json.dumps(keyboard)
    resp = await client.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto", data=data, files={"photo": (filename, photo_bytes, "image/png")})
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
        data = {"chat_id": chat_id, "text": chunk, "parse_mode": "HTML", "disable_web_page_preview": True}
        if keyboard and i == len(chunks) - 1:
            data["reply_markup"] = keyboard
        last_res = await telegram("sendMessage", data)
    return last_res


async def edit_or_send(chat_id, message_id, text, keyboard=None):
    if message_id:
        try:
            payload = {"chat_id": chat_id, "message_id": message_id, "text": text[:4090], "parse_mode": "HTML", "disable_web_page_preview": True}
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
        pass


async def report_unauthorized_admin_attempt(user_id, context_label):
    log_audit(user_id, "UNAUTHORIZED_ADMIN_ATTEMPT", context_label)
    try:
        u = get_user(user_id)
        uname = f"@{u['username']}" if (u and u["username"]) else str(user_id)
        await send_message(ADMIN_USER_ID, f"🚨 <b>UNAUTHORIZED ADMIN ACCESS BLOCKED</b>\n👤 {html_escape(uname)} (<code>{user_id}</code>)\n🔎 <code>{html_escape(context_label)}</code>")
    except Exception:
        pass


# --------------------------------------------------
# KEYBOARDS
# --------------------------------------------------

def main_menu_keyboard(user_id=None):
    ch_url = get_setting("main_channel_url") or get_website_url()
    portal_url = PUBLIC_URL.rstrip("/") + "/app"
    portal_btn = {"text": "🌐 Open Empire Web Portal", "web_app": {"url": portal_url}, "style": "primary"} if portal_url.startswith("https://") else {"text": "🌐 Open Empire Web Portal", "url": portal_url, "style": "primary"}
    rows = [
        [{"text": "⚽ Today's Predictions", "callback_data": "menu:today", "style": "success"}, {"text": "🤖 AI Match Analysis", "callback_data": "menu:ai_analysis", "style": "primary"}],
        [{"text": "🎯 Accumulator Builder", "callback_data": "menu:accum", "style": "primary"}, {"text": "📊 Football Statistics", "callback_data": "menu:stats", "style": "success"}],
        [{"text": "🏆 Results & Accuracy", "callback_data": "menu:results", "style": "primary"}, {"text": "👑 VIP Membership", "callback_data": "menu:vip", "style": "danger"}],
        [{"text": "🌍 International VIP & Crypto", "callback_data": "menu:vip_intl", "style": "primary"}, {"text": "💳 My Subscription", "callback_data": "menu:subscription", "style": "success"}],
        [{"text": "🗺️ Country & Currency", "callback_data": "menu:country", "style": "primary"}, {"text": "📢 Join Channel", "url": ch_url, "style": "primary"}],
        [{"text": "💬 Contact Admin", "callback_data": "menu:contact", "style": "success"}, portal_btn],
    ]
    if user_id and is_admin(user_id):
        rows.append([{"text": "⚡ ADMIN DASHBOARD", "callback_data": "admin:home", "style": "danger"}])
    return {"inline_keyboard": rows}


def country_welcome_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "🌍 Detect My Country", "callback_data": "country:detect_ask", "style": "primary"}],
            [{"text": "🗺️ Select Country Manually", "callback_data": "country:manual", "style": "success"}],
            [{"text": "🌍 International Global ($ USD / Crypto)", "callback_data": "country:confirm:INTL", "style": "danger"}],
            [{"text": "⏭️ Continue to Free Features", "callback_data": "menu:home"}],
        ]
    }


def country_manual_keyboard():
    items = list(COUNTRIES.items())
    rows = []
    for i in range(0, len(items), 2):
        rows.append([{"text": f"{info['flag']} {info['name']} ({info['currency']})", "callback_data": f"country:pick:{code}", "style": "primary"} for code, info in items[i:i + 2]])
    rows.append([{"text": "💱 Change Currency Only", "callback_data": "currency:menu", "style": "success"}])
    rows.append([{"text": "🏠 Main Menu", "callback_data": "menu:home"}])
    return {"inline_keyboard": rows}


def currency_manual_keyboard():
    items = list(CURRENCIES.keys())
    rows = [[{"text": f"💱 {c}", "callback_data": f"currency:set:{c}", "style": "primary"} for c in items[i:i + 3]] for i in range(0, len(items), 3)]
    rows.append([{"text": "⬅️ Back", "callback_data": "menu:country"}])
    return {"inline_keyboard": rows}


def local_plans_keyboard(display_currency="KES"):
    with connect_db() as db:
        plans = db.execute("SELECT * FROM plans ORDER BY price").fetchall()
    style_map = {"daily": "success", "weekly": "primary", "monthly": "primary", "premium": "danger", "lifetime": "danger"}
    rows = []
    for p in plans:
        duration = "Lifetime" if p["is_lifetime"] else f"{p['days']}d"
        price_str, _ = format_converted_price(p["price"], display_currency)
        rows.append([{"text": f"👑 {p['title'] or p['name'].title()} ({duration}) — {price_str}", "callback_data": f"plan:{p['name']}", "style": style_map.get(p["name"], "primary")}])
    rows.append([{"text": "🌍 Switch to International Plans ($ USD)", "callback_data": "menu:vip_intl", "style": "primary"}])
    rows.append([{"text": "🏠 Main Menu", "callback_data": "menu:home"}])
    return {"inline_keyboard": rows}


def intl_plans_keyboard(display_currency="USD"):
    plans = get_all_intl_plans(only_active=True)
    rows = []
    for p in plans:
        duration = "Lifetime" if p["is_lifetime"] else f"{p['days']}d"
        price_str, _ = format_usd_price(p["price_usd"], display_currency)
        rows.append([{"text": f"🌍 {p['title']} ({duration}) — {price_str}", "callback_data": f"iplan:{p['name']}", "style": "success"}])
    rows.append([{"text": "🇰🇪 Switch to East Africa Local Plans", "callback_data": "menu:vip_local", "style": "primary"}])
    rows.append([{"text": "🏠 Main Menu", "callback_data": "menu:home"}])
    return {"inline_keyboard": rows}


def get_reaction_counts(channel_msg_id):
    counts = {e: 0 for e in REACTION_EMOJIS}
    if not channel_msg_id:
        return counts
    with connect_db() as db:
        rows = db.execute("SELECT emoji, COUNT(*) AS cnt FROM post_reactions WHERE channel_msg_id=? GROUP BY emoji", (channel_msg_id,)).fetchall()
    for r in rows:
        if r["emoji"] in counts:
            counts[r["emoji"]] = int(r["cnt"])
    return counts


def save_channel_post_meta(channel_msg_id, selected_btn_ids, include_reactions, include_footer):
    with connect_db() as db:
        db.execute(
            "INSERT INTO channel_posts(channel_msg_id,button_ids,include_reactions,include_footer,created_at) VALUES(?,?,?,?,?) ON CONFLICT(channel_msg_id) DO UPDATE SET button_ids=excluded.button_ids, include_reactions=excluded.include_reactions, include_footer=excluded.include_footer",
            (int(channel_msg_id), json.dumps([int(x) for x in (selected_btn_ids or [])]), 1 if include_reactions else 0, 1 if include_footer else 0, utcnow().isoformat()),
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
        rows.append([{"text": f"{e} {counts[e]}", "callback_data": f"react:{idx}"} for idx, e in enumerate(REACTION_EMOJIS[:3])])
        rows.append([{"text": f"{e} {counts[e]}", "callback_data": f"react:{idx + 3}"} for idx, e in enumerate(REACTION_EMOJIS[3:])])

    if selected_btn_ids:
        with connect_db() as db:
            ph = ",".join(["?"] * len(selected_btn_ids))
            btns = db.execute(f"SELECT * FROM custom_buttons WHERE id IN ({ph}) AND is_active=1 ORDER BY id", tuple(int(x) for x in selected_btn_ids)).fetchall()
        btn_map = {int(b["id"]): b for b in btns}
        for bid in selected_btn_ids:
            b = btn_map.get(int(bid))
            if not b:
                continue
            style = b["style"] if b["style"] in BUTTON_STYLES else "primary"
            target_url = b["url"] if (b["button_type"] == "URL" and b["url"]) else (f"https://t.me/{bot_username}?start=btn_{b['id']}" if bot_username else contact_url)
            rows.append([{"text": b["label"], "url": target_url, "style": style}])

    if include_footer:
        rows.append([{"text": "👑 JOIN VIP NOW", "url": join_url, "style": "danger"}, {"text": "💬 Contact Admin", "url": contact_url, "style": "success"}])
        if website_url:
            rows.append([{"text": f"🌐 {re.sub(r'^https?://', '', website_url).rstrip('/')}", "url": website_url, "style": "primary"}])
    return {"inline_keyboard": rows} if rows else None


def admin_keyboard(user_id):
    if not is_admin(user_id):
        return {"inline_keyboard": []}
    return {"inline_keyboard": [
        [{"text": "📊 Overview & Stats", "callback_data": "admin:stats", "style": "primary"}, {"text": "⚽ Fixtures & Matches", "callback_data": "admin:matches", "style": "success"}],
        [{"text": "🎯 Accumulator Tickets", "callback_data": "admin:accums", "style": "primary"}, {"text": "🧾 Transactions", "callback_data": "admin:transactions", "style": "success"}],
        [{"text": "👥 Users & VIPs", "callback_data": "admin:users", "style": "primary"}, {"text": "🎛️ Channel Buttons & Tickets", "callback_data": "admin:buttons", "style": "primary"}],
        [{"text": "🇰🇪 Local VIP Plans (KES)", "callback_data": "admin:packages", "style": "success"}, {"text": "🌍 International Plans & Prices ($)", "callback_data": "admin:intl_packages", "style": "danger"}],
        [{"text": "📝 Post to Main Channel", "callback_data": "admin:post_channel", "style": "success"}, {"text": "💎 Post to VIP Channel", "callback_data": "admin:post_vip_channel", "style": "danger"}],
        [{"text": "📢 VIP Promo Post", "callback_data": "admin:post", "style": "primary"}, {"text": "📣 Broadcast", "callback_data": "admin:broadcast", "style": "danger"}],
        [{"text": "⚙️ Channels & Payment Settings", "callback_data": "admin:channels", "style": "primary"}, {"text": "📥 Export CSV", "callback_data": "admin:export", "style": "success"}],
        [{"text": "🛡️ Audit Logs", "callback_data": "admin:audit", "style": "primary"}, {"text": "🏠 Main Menu", "callback_data": "menu:home"}],
    ]}


# --------------------------------------------------
# PAYHERO KENYA API v2
# --------------------------------------------------

def payhero_headers():
    if PAYHERO_API_USERNAME and PAYHERO_API_PASSWORD and not PAYHERO_AUTH_TOKEN:
        raw_creds = f"{PAYHERO_API_USERNAME}:{PAYHERO_API_PASSWORD}".encode("utf-8")
        return {"Authorization": "Basic " + base64.b64encode(raw_creds).decode("utf-8"), "Content-Type": "application/json"}
    token = re.sub(r"^(basic|bearer)\s+", "", PAYHERO_AUTH_TOKEN.strip().strip('"').strip("'").strip(), flags=re.IGNORECASE).strip()
    auth = f"Basic {base64.b64encode(token.encode('utf-8')).decode('utf-8')}" if ":" in token else f"Basic {token}"
    return {"Authorization": auth, "Content-Type": "application/json"}


def normalize_phone(phone):
    phone = re.sub(r"[\s+\-()]", "", phone)
    if phone.startswith("2540") and len(phone) == 13:
        phone = "0" + phone[4:]
    elif phone.startswith("254") and len(phone) == 12:
        phone = "0" + phone[3:]
    elif (phone.startswith("7") or phone.startswith("1")) and len(phone) == 9:
        phone = "0" + phone
    return phone if re.fullmatch(r"0[17]\d{8}", phone) else None


def recent_stk_cooldown_remaining(user_id):
    with connect_db() as db:
        last = db.execute("SELECT created_at FROM orders WHERE user_id=? AND provider='mpesa' ORDER BY created_at DESC LIMIT 1", (user_id,)).fetchone()
    return max(0, int(STK_COOLDOWN_SECONDS - (utcnow() - parse_utc(last["created_at"])).total_seconds())) if last else 0


async def send_manual_till_fallback(user_id, external_ref, item_title, amount, reason_note=""):
    till_no = get_setting("mpesa_till_number", DEFAULT_MPESA_TILL)
    with connect_db() as db:
        db.execute("UPDATE orders SET provider='mpesa_till', status='PENDING_PROOF' WHERE external_ref=?", (external_ref,))
    set_session(user_id, "WAIT_PAYMENT_PROOF", external_ref)
    prefix = f"⚠️ <i>{html_escape(reason_note)}</i>\n\n" if reason_note else ""
    await send_message(
        user_id,
        f"{prefix}🏪 <b>PAY VIA M-PESA TILL NUMBER {html_escape(till_no)}</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        f"🎫 Item: <b>{html_escape(item_title)}</b>\n💰 Amount: <b>KES {int(amount):,}</b>\n"
        f"🔢 Till Number: <code>{html_escape(till_no)}</code>\n🔗 Ref: <code>{external_ref}</code>\n\n"
        f"1️⃣ <b>M-Pesa → Lipa na M-Pesa → Buy Goods and Services</b> → Till <code>{html_escape(till_no)}</code>\n"
        f"2️⃣ <b>Send your payment screenshot (photo or PDF/file) right here</b> for confirmation & automatic delivery!",
        {"inline_keyboard": [
            [{"text": "📸 Send Payment Screenshot / PDF", "callback_data": f"proof:{external_ref}", "style": "success"}],
            [{"text": f"💬 Contact {BRAND_TAG}", "url": get_setting("admin_contact_url"), "style": "primary"}],
            [{"text": "🏠 Main Menu", "callback_data": "menu:home"}],
        ]},
    )


async def create_manual_order_and_prompt(user_id, offer_key, method_key):
    offer = resolve_offer(offer_key)
    if not offer:
        await send_message(user_id, "⚠️ Selected package/ticket is currently unavailable.")
        return
    prefs = get_user_prefs(user_id)
    is_intl = offer["scope"] == "INTL"
    if is_intl:
        disp_str, _ = format_usd_price(offer["price_usd"], prefs["currency"] if prefs["is_intl"] else "USD")
        amt_kes, amt_usd, curr = 0, float(offer["price_usd"]), "USD"
    else:
        disp_str, _ = format_converted_price(offer["price"], prefs["currency"])
        amt_kes, amt_usd, curr = int(offer["price"]), 0.0, prefs["currency"]

    external_ref = "MAN" + secrets.token_hex(6).upper()
    with connect_db() as db:
        db.execute(
            "INSERT INTO orders(external_ref,user_id,plan,amount,amount_usd,market_scope,currency,local_amount,phone,provider,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (external_ref, user_id, offer["key"], amt_kes, amt_usd, offer["scope"], curr, disp_str, method_key, method_key, "PENDING_PROOF", utcnow().isoformat()),
        )
    set_session(user_id, "WAIT_PAYMENT_PROOF", external_ref)

    if method_key == "mpesa_till":
        await send_manual_till_fallback(user_id, external_ref, offer["title"], amt_kes)
        return
    if method_key == "airtel":
        airtel_no = get_setting("airtel_money_number", DEFAULT_AIRTEL_NUMBER)
        await send_message(
            user_id,
            f"🔴 <b>AIRTEL MONEY PAYMENT</b>\n━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 Item: <b>{html_escape(offer['title'])}</b>\n💰 Amount: <b>{html_escape(disp_str)}</b>\n"
            f"📞 Airtel Money: <code>{html_escape(airtel_no)}</code>\n🔗 Ref: <code>{external_ref}</code>\n\n"
            "📸 <b>Send your payment screenshot (photo or PDF/file) right here</b> after paying!",
            {"inline_keyboard": [
                [{"text": "📸 Send Payment Screenshot / PDF", "callback_data": f"proof:{external_ref}", "style": "success"}],
                [{"text": f"💬 Contact {BRAND_TAG}", "url": get_setting("admin_contact_url"), "style": "primary"}],
                [{"text": "🏠 Main Menu", "callback_data": "menu:home"}],
            ]},
        )
        return

    intl_note = get_setting("intl_payment_note")
    await send_message(
        user_id,
        f"🌍 <b>INTERNATIONAL PAYMENT ({html_escape(method_key.upper())})</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        f"🎫 Item: <b>{html_escape(offer['title'])}</b>\n💰 Price: <b>{html_escape(disp_str)}</b>\n🔗 Ref: <code>{external_ref}</code>\n\n"
        f"📝 {html_escape(intl_note)}\n\n📸 Send your payment screenshot / PDF receipt right here after paying!",
        {"inline_keyboard": [
            [{"text": f"💬 Contact {BRAND_TAG}", "url": get_setting("admin_contact_url"), "style": "primary"}],
            [{"text": "📸 Send Payment Screenshot / PDF", "callback_data": f"proof:{external_ref}", "style": "success"}],
            [{"text": "🏠 Main Menu", "callback_data": "menu:home"}],
        ]},
    )


async def fetch_payhero_diagnostics():
    client = await get_http()
    headers = payhero_headers()

    async def get_ep(path, params):
        try:
            r = await client.get(PAYHERO_URL + path, headers=headers, params=params, timeout=15)
            return {"status": r.status_code, "data": r.json() if "application/json" in r.headers.get("content-type", "") else r.text[:300]}
        except Exception as e:
            return {"status": 0, "error": str(e)}

    ch_res, wal_res = await asyncio.gather(
        get_ep("/payment_channels", {"is_active": "true"}),
        get_ep("/wallets", {"wallet_type": "service_wallet"}),
    )
    return {"channels": ch_res, "wallet": wal_res}


async def start_mpesa_payment(user_id, offer_key, phone, notify_telegram=True):
    offer = resolve_offer(offer_key)
    if not offer or offer["scope"] != "LOCAL":
        if notify_telegram:
            await send_message(user_id, "⚠️ Selected package/ticket is currently unavailable for M-Pesa.")
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
    ph_channel = int(raw_ch if str(raw_ch).isdigit() else PAYHERO_CHANNEL_ID)
    item_title = offer["title"]
    db_user = get_user(user_id)
    cust_name = (db_user["first_name"] if db_user and db_user["first_name"] else "Prime Tips Member")[:40]

    with connect_db() as db:
        db.execute("INSERT INTO orders(external_ref,user_id,plan,amount,amount_usd,market_scope,currency,local_amount,phone,provider,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (external_ref, user_id, offer["key"], amount, 0.0, "LOCAL", "KES", f"KES {amount:,}", phone, "mpesa", "PENDING", utcnow().isoformat()))

    payload = {"amount": amount, "phone_number": phone, "channel_id": ph_channel, "provider": "m-pesa", "external_reference": external_ref, "customer_name": cust_name, "callback_url": PUBLIC_URL.rstrip("/") + "/payhero/callback"}
    if PAYHERO_CREDENTIAL_ID:
        payload["credential_id"] = PAYHERO_CREDENTIAL_ID

    try:
        client = await get_http()
        response = await client.post(PAYHERO_URL + "/payments", headers=payhero_headers(), json=payload)
        try:
            result = response.json()
        except Exception:
            result = {"raw": response.text[:350]}

        if not ((response.status_code in (200, 201)) and (bool(result.get("success")) or bool(result.get("reference")))):
            err_msg = result.get("error_message") or result.get("message") or result.get("detail") or str(result)[:220]
            log.error("PayHero STK failed (HTTP %s, ch=%s): %s", response.status_code, ph_channel, err_msg)
            asyncio.create_task(send_message(ADMIN_USER_ID, f"⚠️ <b>PayHero STK Failed (HTTP {response.status_code})</b>\nChannel: <code>{ph_channel}</code> | Error: <code>{html_escape(str(err_msg))}</code>\n💡 Run <b>🩺 Test PayHero</b> in Admin → Settings."))
            if notify_telegram:
                await send_manual_till_fallback(user_id, external_ref, item_title, amount, reason_note=f"Instant STK Push could not be reached — use Manual Till {get_setting('mpesa_till_number', DEFAULT_MPESA_TILL)} below!")
            return {"ok": False, "fallback": "till", "external_ref": external_ref, "error": str(err_msg)}

        reference = result.get("reference")
        checkout_id = result.get("CheckoutRequestID") or result.get("checkout_request_id")
        if not reference:
            if notify_telegram:
                await send_manual_till_fallback(user_id, external_ref, item_title, amount, reason_note=f"Instant STK Push unavailable — use Manual Till {get_setting('mpesa_till_number', DEFAULT_MPESA_TILL)} below!")
            return {"ok": False, "fallback": "till", "external_ref": external_ref}

        with connect_db() as db:
            db.execute("UPDATE orders SET payhero_reference=?, checkout_id=? WHERE external_ref=?", (reference, checkout_id, external_ref))

        till_no = get_setting("mpesa_till_number", DEFAULT_MPESA_TILL)
        if notify_telegram:
            await send_message(
                user_id,
                f"📲 <b>M-PESA STK PUSH SENT!</b>\n\n🎫 Item: <b>{html_escape(item_title)}</b>\n💳 Amount: <b>KES {amount:,}</b>\n📞 Phone: <code>{html_escape(phone)}</code>\n🔗 Ref: <code>{external_ref}</code>\n\n🔐 Enter your M-Pesa PIN on your phone!",
                {"inline_keyboard": [
                    [{"text": "🔄 Verify Payment Status", "callback_data": f"verify:{external_ref}", "style": "success"}],
                    [{"text": f"🏪 Use Till {till_no} Instead", "callback_data": f"manualtill:{external_ref}", "style": "primary"}],
                    [{"text": "📸 Upload Screenshot / PDF", "callback_data": f"proof:{external_ref}", "style": "primary"}],
                    [{"text": "🏠 Main Menu", "callback_data": "menu:home"}],
                ]},
            )
        return {"ok": True, "external_ref": external_ref, "status": "PENDING"}
    except Exception:
        if notify_telegram:
            await send_manual_till_fallback(user_id, external_ref, item_title, amount, reason_note=f"Instant STK Push timed out — use Manual Till {get_setting('mpesa_till_number', DEFAULT_MPESA_TILL)} below!")
        return {"ok": False, "fallback": "till", "external_ref": external_ref}


async def verify_payhero_transaction(reference):
    client = await get_http()
    response = await client.get(PAYHERO_URL + "/transaction-status", headers=payhero_headers(), params={"reference": reference})
    response.raise_for_status()
    return response.json()


# --------------------------------------------------
# CRYPTAPI INTERNATIONAL CRYPTO PAYMENTS (100+ COINS)
# --------------------------------------------------

def get_cryptapi_wallets_map():
    merged = {}
    for k, env_name in [("evm", "CRYPTAPI_EVM_ADDRESS"), ("tron", "CRYPTAPI_TRON_ADDRESS"), ("sol", "CRYPTAPI_SOL_ADDRESS"), ("ton", "CRYPTAPI_TON_ADDRESS"), ("btc", "CRYPTAPI_BTC_ADDRESS"), ("ltc", "CRYPTAPI_LTC_ADDRESS"), ("bch", "CRYPTAPI_BCH_ADDRESS"), ("doge", "CRYPTAPI_DOGE_ADDRESS"), ("zec", "CRYPTAPI_ZEC_ADDRESS")]:
        v = env_str(env_name)
        if v:
            merged[k] = v
    if CRYPTAPI_WALLETS_JSON:
        try:
            for k, v in json.loads(CRYPTAPI_WALLETS_JSON).items():
                if v:
                    merged[str(k).lower().strip()] = str(v).strip()
        except Exception:
            pass
    try:
        for k, v in json.loads(get_setting("cryptapi_wallets", "{}")).items():
            if v:
                merged[str(k).lower().strip()] = str(v).strip()
    except Exception:
        pass
    return merged


def save_cryptapi_wallet(key, address):
    key = key.lower().strip()
    try:
        data = json.loads(get_setting("cryptapi_wallets", "{}"))
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
    if wallets.get(t):
        return wallets[t]
    net = t.split("/", 1)[0] if "/" in t else "coins"
    if t in ("btc", "bch", "ltc", "doge", "zec"):
        return wallets.get(t, "")
    if t == "trx" or net == "trc20":
        return wallets.get("tron", "")
    if t == "sol" or net == "sol":
        return wallets.get("sol", "")
    if t == "ton" or net == "ton":
        return wallets.get("ton", "")
    if t == "eth" or net in ("erc20", "bep20", "bsc", "polygon", "arbitrum", "optimism", "base", "avax-c", "avax", "linea", "monad", "berachain", "bera"):
        return wallets.get("evm", "")
    return wallets.get(net, "")


def _parse_cryptapi_info_payload(raw):
    coins, networks = {}, {}

    def add_coin(guess, info, net_key):
        t = (info.get("ticker") or guess or "").lower().strip()
        if not t:
            return
        mn = info.get("minimum_transaction_coin")
        if mn is None:
            mn = info.get("minimum_transaction")
        entry = {"ticker": t, "name": str(info.get("coin") or t.upper()), "network": net_key, "min_coin": float(mn) if mn not in (None, "") else 0.0}
        coins[t] = entry
        networks.setdefault(net_key, []).append(entry)

    for tk, tv in (raw or {}).items():
        if tk in ("fee_tiers", "status", "minimum_fee") or not isinstance(tv, dict):
            continue
        if "coin" in tv or "minimum_transaction_coin" in tv or "ticker" in tv:
            add_coin(tk, tv, "coins")
        else:
            for sk, sv in tv.items():
                if isinstance(sv, dict) and ("coin" in sv or "minimum_transaction_coin" in sv or "ticker" in sv):
                    add_coin(f"{tk}/{sk}", sv, tk)
    for nk in networks:
        networks[nk].sort(key=lambda x: x["name"])
    return coins, networks


async def fetch_cryptapi_catalog(force=False):
    now = utcnow()
    if not force and _cryptapi_catalog_cache["fetched_at"] and _cryptapi_catalog_cache["coins"]:
        if (now - _cryptapi_catalog_cache["fetched_at"]).total_seconds() / 60.0 < CRYPTAPI_CACHE_MINUTES:
            return _cryptapi_catalog_cache["coins"], _cryptapi_catalog_cache["networks"]
    try:
        client = await get_http()
        resp = await client.get(f"{CRYPTAPI_URL}/info/", params={"prices": "1"}, timeout=15)
        if resp.status_code == 200:
            coins, networks = _parse_cryptapi_info_payload(resp.json())
            if coins:
                _cryptapi_catalog_cache.update({"fetched_at": now, "coins": coins, "networks": networks})
                return coins, networks
    except Exception as e:
        log.warning("CryptAPI /info/ skipped: %s", e)

    if _cryptapi_catalog_cache["coins"]:
        return _cryptapi_catalog_cache["coins"], _cryptapi_catalog_cache["networks"]
    coins, networks = {}, {}
    for t, name, net, mn in [
        ("btc", "Bitcoin", "coins", 0.00008), ("eth", "Ethereum", "coins", 0.0015), ("ltc", "Litecoin", "coins", 0.002),
        ("doge", "Dogecoin", "coins", 10.0), ("trx", "TRON", "coins", 10.0), ("trc20/usdt", "USDT (TRC20)", "trc20", 1.0),
        ("bep20/usdt", "USDT (BEP20)", "bep20", 1.0), ("erc20/usdt", "USDT (ERC20)", "erc20", 5.0), ("sol/usdt", "USDT (Solana)", "sol", 1.0),
        ("ton/usdt", "USDT (TON)", "ton", 1.0), ("sol/sol", "Solana", "sol", 0.01), ("ton/ton", "Toncoin", "ton", 0.2),
    ]:
        e = {"ticker": t, "name": name, "network": net, "min_coin": mn}
        coins[t] = e
        networks.setdefault(net, []).append(e)
    return coins, networks


def get_cryptapi_callback_base():
    return (get_setting("cryptapi_callback_base", "").strip() or CRYPTAPI_CALLBACK_BASE_URL or PUBLIC_URL).rstrip("/")


def build_cryptapi_callback_url(external_ref, nonce):
    return f"{get_cryptapi_callback_base()}/cryptapi/callback?{urlencode({'order_ref': external_ref, 'nonce': nonce})}"


async def cryptapi_convert_usd_to_coin(ticker, usd_amount):
    client = await get_http()
    resp = await client.get(f"{CRYPTAPI_URL}/{ticker}/convert/", params={"value": f"{usd_amount:.2f}", "from": "USD"}, timeout=15)
    data = resp.json()
    if resp.status_code != 200 or data.get("status") == "error":
        raise RuntimeError(data.get("error") or f"Convert failed ({resp.status_code})")
    return float(data["value_coin"])


async def cryptapi_create_address(ticker, address_out, callback_url, confirmations=1):
    client = await get_http()
    resp = await client.get(f"{CRYPTAPI_URL}/{ticker}/create/", params={"callback": callback_url, "address": address_out, "pending": "1", "confirmations": str(confirmations), "post": "1"}, timeout=18)
    data = resp.json() if "application/json" in resp.headers.get("content-type", "") else {"error": resp.text[:300]}
    if resp.status_code != 200 or data.get("status") == "error":
        raise RuntimeError(data.get("error") or data.get("detail") or str(data))
    return data


async def cryptapi_get_qrcode(ticker, address_in, value_coin):
    client = await get_http()
    resp = await client.get(f"{CRYPTAPI_URL}/{ticker}/qrcode/", params={"address": address_in, "value": str(value_coin), "size": "512"}, timeout=15)
    data = resp.json()
    if resp.status_code != 200 or not data.get("qr_code"):
        raise RuntimeError("QR generation failed")
    return base64.b64decode(data["qr_code"])


async def cryptapi_get_logs(ticker, callback_url):
    client = await get_http()
    resp = await client.get(f"{CRYPTAPI_URL}/{ticker}/logs/", params={"callback": callback_url}, timeout=15)
    return resp.json() if resp.status_code == 200 else None


async def show_crypto_checkout_menu(chat_id, user_id, offer_key, view="home", page=0, message_id=None):
    coins, networks = await fetch_cryptapi_catalog()
    enabled = {t: info for t, info in coins.items() if get_wallet_for_ticker(t)}
    if not enabled:
        await edit_or_send(chat_id, message_id, "⚠️ <b>Crypto payments are not configured yet.</b>\n" + ("Admin: Go to <b>Admin → Channels & Payment Settings → 🪙 CryptAPI Crypto Wallets</b>." if is_admin(user_id) else f"Please contact {BRAND_TAG}."), {"inline_keyboard": [[{"text": f"💬 Contact {BRAND_TAG}", "url": get_setting("admin_contact_url"), "style": "primary"}], [{"text": "🏠 Main Menu", "callback_data": "menu:home"}]]})
        return

    offer = resolve_offer(offer_key)
    if not offer or offer["scope"] != "INTL":
        await send_message(chat_id, "⚠️ Crypto checkout is only for International plans/prices.")
        return

    usd_amt = float(offer["price_usd"])
    rows = []
    back_cb = f"iplan:{offer_key.split(':', 1)[1]}" if offer_key.startswith("intl:") else f"openibtn:{offer_key.split(':', 1)[1]}"

    if view == "home":
        pop = [t for t in POPULAR_CRYPTO_TICKERS if t in enabled]
        for i in range(0, len(pop), 2):
            rows.append([{"text": f"🪙 {enabled[t]['name']}", "callback_data": f"cryptopay|{offer_key}|{t}", "style": "success"} for t in pop[i:i + 2]])
        rows.append([{"text": "💵 All Stablecoins", "callback_data": f"cryptolist|{offer_key}|stable|0", "style": "primary"}, {"text": "🌐 All Networks", "callback_data": f"cryptolist|{offer_key}|nets|0", "style": "primary"}])
        rows.append([{"text": "⬅️ Back", "callback_data": back_cb}])
        await edit_or_send(chat_id, message_id, f"🪙 <b>INTERNATIONAL CRYPTO CHECKOUT</b>\n━━━━━━━━━━━━━━━━━━━━\n🎫 Item: <b>{html_escape(offer['title'])}</b>\n💵 Price: <b>${usd_amt:,.2f} USD</b>\n✅ Enabled Assets: <b>{len(enabled)}</b>\n\nChoose a coin below:", {"inline_keyboard": rows})
        return

    if view == "nets":
        active_nets = [(nk, sum(1 for it in nlist if it["ticker"] in enabled)) for nk, nlist in networks.items()]
        active_nets = [(nk, c) for nk, c in active_nets if c > 0]
        for i in range(0, len(active_nets), 2):
            rows.append([{"text": f"{CRYPTO_NETWORK_LABELS.get(nk, nk.upper())} ({c})", "callback_data": f"cryptolist|{offer_key}|net:{nk}|0", "style": "primary"} for nk, c in active_nets[i:i + 2]])
        rows.append([{"text": "⬅️ Popular Coins", "callback_data": f"cryptolist|{offer_key}|home|0"}])
        await edit_or_send(chat_id, message_id, f"🌐 <b>ALL CRYPTAPI NETWORKS</b>\nItem: <b>{html_escape(offer['title'])}</b> (${usd_amt:,.2f} USD)", {"inline_keyboard": rows})
        return

    if view == "stable":
        items = sorted([c for t, c in enabled.items() if any(s in t for s in ("usdt", "usdc", "dai", "busd", "pyusd", "eurc", "usd1", "fdusd"))], key=lambda x: x["name"])
        title = "💵 <b>ALL STABLECOINS</b>"
    elif view.startswith("net:"):
        nk = view.split(":", 1)[1]
        items = [c for c in networks.get(nk, []) if c["ticker"] in enabled]
        title = f"🌐 <b>{html_escape(CRYPTO_NETWORK_LABELS.get(nk, nk.upper()))}</b>"
    else:
        items = sorted(enabled.values(), key=lambda x: x["name"])
        title = "🪙 <b>ALL COINS</b>"

    per_page = 12
    total_pages = max(1, (len(items) + per_page - 1) // per_page)
    page = max(0, min(int(page), total_pages - 1))
    for i in range(0, len(items[page * per_page:(page + 1) * per_page]), 2):
        chunk = items[page * per_page:(page + 1) * per_page][i:i + 2]
        rows.append([{"text": c["name"], "callback_data": f"cryptopay|{offer_key}|{c['ticker']}", "style": "success"} for c in chunk])
    nav = []
    if page > 0:
        nav.append({"text": "⬅️ Prev", "callback_data": f"cryptolist|{offer_key}|{view}|{page - 1}"})
    if page < total_pages - 1:
        nav.append({"text": "Next ➡️", "callback_data": f"cryptolist|{offer_key}|{view}|{page + 1}"})
    if nav:
        rows.append(nav)
    rows.append([{"text": "⬅️ Crypto Menu", "callback_data": f"cryptolist|{offer_key}|home|0"}])
    await edit_or_send(chat_id, message_id, f"{title} ({page + 1}/{total_pages})\nItem: <b>{html_escape(offer['title'])}</b> (${usd_amt:,.2f} USD)", {"inline_keyboard": rows})


async def start_cryptapi_payment(user_id, chat_id, offer_key, ticker):
    offer = resolve_offer(offer_key)
    if not offer or offer["scope"] != "INTL":
        await send_message(chat_id, "⚠️ International item unavailable.")
        return
    ticker = ticker.lower().strip()
    address_out = get_wallet_for_ticker(ticker)
    if not address_out:
        await send_message(chat_id, "⚠️ No wallet configured for this coin.")
        return
    coins, _ = await fetch_cryptapi_catalog()
    coin_meta = coins.get(ticker, {"name": ticker.upper(), "min_coin": 0.0})
    usd_amount = float(offer["price_usd"])
    try:
        value_coin = await cryptapi_convert_usd_to_coin(ticker, usd_amount)
    except Exception as e:
        await send_message(chat_id, f"⚠️ Price conversion failed: <code>{html_escape(str(e))}</code>")
        return

    min_coin = float(coin_meta.get("min_coin") or 0)
    if min_coin > 0 and value_coin < min_coin:
        await send_message(
            chat_id,
            f"⚠️ <b>Below network minimum for {html_escape(coin_meta['name'])}</b>\nOrder: <code>{value_coin:.8f}</code> | Min: <code>{min_coin}</code>\n💡 Try <b>USDT (TRC20/BEP20/Polygon/Solana/TON)</b> or <b>LTC / TRX / DOGE</b>.",
            {"inline_keyboard": [[{"text": "🔄 Choose Another Coin", "callback_data": f"cryptolist|{offer_key}|home|0", "style": "primary"}]]},
        )
        return

    external_ref = "CRY" + secrets.token_hex(6).upper()
    nonce = secrets.token_hex(12)
    cb_url = build_cryptapi_callback_url(external_ref, nonce)
    confs = int(get_setting("cryptapi_confirmations", "1") or "1")
    try:
        created = await cryptapi_create_address(ticker, address_out, cb_url, confirmations=confs)
    except Exception as e:
        err_s = str(e)
        if "verify your domain" in err_s.lower():
            asyncio.create_task(send_message(ADMIN_USER_ID, f"🚨 <b>CryptAPI Domain Verification Required</b>\nVerify <code>{html_escape(get_cryptapi_callback_base())}</code> at https://cryptapi.io/verify_domain/"))
        await send_message(chat_id, f"⚠️ Could not create deposit address: <code>{html_escape(err_s[:180])}</code>")
        return

    address_in = created["address_in"]
    coin_str = f"{value_coin:.8f}".rstrip("0").rstrip(".")
    if "." not in coin_str:
        coin_str += ".0"

    with connect_db() as db:
        db.execute(
            "INSERT INTO orders(external_ref,user_id,plan,amount,amount_usd,market_scope,currency,local_amount,phone,provider,status,crypto_ticker,crypto_address_in,crypto_amount,callback_nonce,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (external_ref, user_id, offer["key"], 0, usd_amount, "INTL", "USD", f"${usd_amount:.2f} ({coin_str} {coin_meta['name']})", ticker, "crypto", "PENDING", ticker, address_in, coin_str, nonce, utcnow().isoformat()),
        )

    caption = (
        f"🪙 <b>PAY WITH {html_escape(coin_meta['name'].upper())}</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        f"🎫 Item: <b>{html_escape(offer['title'])}</b>\n💵 Value: <b>${usd_amount:.2f} USD</b>\n"
        f"💰 Send EXACT: <code>{coin_str}</code> <b>{html_escape(coin_meta['name'])}</b>\n📥 Address:\n<code>{html_escape(address_in)}</code>\n\n"
        f"🔗 Ref: <code>{external_ref}</code> | Min: <code>{created.get('minimum_transaction_coin') or min_coin}</code>\n⚡ <i>Auto-activates after {confs} confirmation(s).</i>"
    )
    kb = {"inline_keyboard": [
        [{"text": "🔄 Check Crypto Payment", "callback_data": f"cryptocheck:{external_ref}", "style": "success"}],
        [{"text": "🔄 Switch Coin", "callback_data": f"cryptolist|{offer_key}|home|0", "style": "primary"}],
        [{"text": "🏠 Main Menu", "callback_data": "menu:home"}],
    ]}
    try:
        qr_bytes = await cryptapi_get_qrcode(ticker, address_in, coin_str)
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
            await send_message(int(order["user_id"]), f"⏳ <b>CRYPTO PAYMENT DETECTED!</b>\nRef: <code>{external_ref}</code>\nTxID: <code>{html_escape(txid_in or 'Pending')}</code>\n⛓️ Waiting for confirmation...")
        except Exception:
            pass
        return "PENDING_CONFIRMATION"

    if expected_coin > 0 and paid_coin > 0 and paid_coin < (expected_coin * 0.97):
        with connect_db() as db:
            db.execute("UPDATE orders SET status='PENDING_REVIEW', crypto_txid=?, crypto_uuid=?, mpesa_receipt=? WHERE external_ref=? AND status!='PAID'", (txid_in, tx_uuid, txid_in, external_ref))
        asyncio.create_task(send_message(ADMIN_USER_ID, f"⚠️ <b>UNDERPAID CRYPTO</b> Ref: <code>{external_ref}</code> Exp: {expected_coin} Got: {paid_coin}", {"inline_keyboard": [[{"text": "✅ Approve Anyway", "callback_data": f"admin:approve:{external_ref}", "style": "success"}, {"text": "❌ Reject", "callback_data": f"admin:reject:{external_ref}", "style": "danger"}]]}))
        return "UNDERPAID"

    with connect_db() as db:
        db.execute("UPDATE orders SET crypto_txid=?, crypto_uuid=?, mpesa_receipt=? WHERE external_ref=?", (txid_in, tx_uuid, txid_in or f"CRYPTO-{external_ref}", external_ref))
        fresh = db.execute("SELECT * FROM orders WHERE external_ref=?", (external_ref,)).fetchone()
    await finalize_paid_order(fresh, callback_data={"MpesaReceiptNumber": txid_in or f"CRYPTO-{external_ref}"}, manual_admin=True)
    return "PAID"


async def verify_cryptapi_order_via_logs(order):
    if not order["crypto_ticker"] or not order["callback_nonce"]:
        return "NOT_CRYPTO"
    logs_data = await cryptapi_get_logs(order["crypto_ticker"], build_cryptapi_callback_url(order["external_ref"], order["callback_nonce"]))
    if not logs_data or logs_data.get("status") == "error":
        return "NO_TX"
    callbacks = logs_data.get("callbacks") or []
    if not callbacks:
        return "NO_TX"
    confirmed = [c for c in callbacks if str(c.get("pending", "1")) in ("0", "false", "False")]
    return await process_cryptapi_event(order, confirmed[-1] if confirmed else callbacks[-1])


# --------------------------------------------------
# VIP ACCESS, DELIVERY & ORDER FINALIZATION
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
                await telegram("sendDocument", {"chat_id": user_id, "document": m["file_id"], "caption": cap[:1020], "parse_mode": "HTML"})
            else:
                await telegram("sendPhoto", {"chat_id": user_id, "photo": m["file_id"], "caption": cap[:1020], "parse_mode": "HTML"})
            sent += 1
        except Exception:
            pass
    return sent


async def give_vip_access(user_id, plan_name, days_override=None, receipt=None, is_lifetime_override=False, title_override=None):
    if days_override is not None:
        days, display_title, is_lifetime = int(days_override), title_override or f"{days_override}-Day VIP", 1 if is_lifetime_override else 0
    else:
        offer = resolve_offer(plan_name)
        if not offer:
            return
        days, display_title, is_lifetime = int(offer["days"]), title_override or offer["title"], int(offer["is_lifetime"])

    contact_url = get_setting("admin_contact_url", f"https://t.me/{BRAND_TAG.lstrip('@')}")
    receipt_line = f"🧾 Receipt: <code>{html_escape(receipt)}</code>\n" if receipt else ""
    delivered_media = await send_offer_ticket_media(user_id, plan_name, display_title, receipt=receipt)

    if days <= 0 and not is_lifetime:
        note = f"🖼️ Delivered <b>{delivered_media}</b> ticket image(s) above!" if delivered_media > 0 else f"✅ Payment confirmed! Admin ({BRAND_TAG}) notified."
        await send_message(user_id, f"🎉 <b>PAYMENT CONFIRMED!</b>\n\n👑 <b>{PROJECT_NAME}</b>\n🎫 Ticket: <b>{html_escape(display_title)}</b>\n{receipt_line}\n{note}", {"inline_keyboard": [[{"text": f"💬 Contact {BRAND_TAG}", "url": contact_url, "style": "success"}], [{"text": "🏠 Main Menu", "callback_data": "menu:home", "style": "primary"}]]})
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
        db.execute("INSERT INTO subscriptions(user_id,plan,expires_at,status,is_lifetime,reminded) VALUES(?,?,?,'ACTIVE',?,0) ON CONFLICT(user_id) DO UPDATE SET plan=excluded.plan, expires_at=excluded.expires_at, status='ACTIVE', is_lifetime=excluded.is_lifetime, reminded=0", (user_id, plan_name, expiry.isoformat(), is_lifetime))

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
            f"🎉 <b>VIP ACCESS ACTIVATED!</b>\n\n👑 <b>{PROJECT_NAME} ({BRAND_TAG})</b>\n💎 Item: <b>{html_escape(display_title)}</b>\n{receipt_line}{media_line}📅 Expires: <b>{exp_text}</b>\n\n👇 Tap below to enter VIP:",
            {"inline_keyboard": [[{"text": "👑 ENTER VIP CHANNEL", "url": invite["invite_link"], "style": "danger"}], [{"text": "🏠 Main Menu", "callback_data": "menu:home", "style": "primary"}]]},
        )
    except Exception:
        await send_message(ADMIN_USER_ID, f"⚠️ VIP activated for <code>{user_id}</code>, invite creation failed.")


async def reward_referrer_if_eligible(payer_user_id):
    payer = get_user(payer_user_id)
    if not payer or not payer["referred_by"] or payer["referral_rewarded"]:
        return
    referrer_id = int(payer["referred_by"])
    if referrer_id == int(payer_user_id) or not get_user(referrer_id):
        return
    with connect_db() as db:
        if db.execute("UPDATE users SET referral_rewarded=1 WHERE user_id=? AND referral_rewarded=0", (int(payer_user_id),)).rowcount == 0:
            return
    reward_days = int(get_setting("referral_reward_days", "2") or "2")
    if reward_days > 0:
        try:
            await give_vip_access(referrer_id, f"referral-{reward_days}d", days_override=reward_days, title_override=f"🎁 Referral Bonus (+{reward_days} Days VIP)")
            await send_message(referrer_id, f"🎁 <b>REFERRAL REWARD!</b> You received <b>+{reward_days} Day(s) of VIP Access</b>!")
        except Exception:
            pass


def _extract_payhero_status(verified):
    if not isinstance(verified, dict):
        return ""
    nested = verified.get("data") if isinstance(verified.get("data"), dict) else {}
    return str(verified.get("status") or verified.get("transaction_status") or verified.get("Status") or nested.get("status") or nested.get("transaction_status") or "").upper().strip()


async def finalize_paid_order(order, verified=None, callback_data=None, manual_admin=False):
    external_ref = order["external_ref"]
    if not manual_admin:
        status = _extract_payhero_status(verified)
        cb_status = str((callback_data or {}).get("Status") or (callback_data or {}).get("status") or "").upper()
        cb_rc = (callback_data or {}).get("ResultCode")
        if status in ("FAILED", "CANCELLED", "CANCELED", "EXPIRED") or (cb_rc not in (None, 0, "0") and cb_status in ("FAILED", "CANCELLED", "CANCELED", "")):
            with connect_db() as db:
                if db.execute("UPDATE orders SET status='FAILED' WHERE external_ref=? AND status='PENDING'", (external_ref,)).rowcount > 0:
                    till_no = get_setting("mpesa_till_number", DEFAULT_MPESA_TILL)
                    asyncio.create_task(send_message(int(order["user_id"]), f"❌ <b>STK Push failed or was cancelled.</b>\nPay manually via Till <code>{html_escape(till_no)}</code> and send your screenshot:", {"inline_keyboard": [[{"text": f"🏪 Pay via Till {till_no}", "callback_data": f"manualtill:{external_ref}", "style": "success"}], [{"text": "🏠 Main Menu", "callback_data": "menu:home"}]]}))
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
    receipt = (callback_data or {}).get("MpesaReceiptNumber") or (callback_data or {}).get("MPESA_Reference") or (verified or {}).get("provider_reference") or (verified or {}).get("transaction_reference") or v_nested.get("provider_reference") or order["mpesa_receipt"]
    with connect_db() as db:
        updated = db.execute("UPDATE orders SET status='PAID', mpesa_receipt=COALESCE(?, mpesa_receipt) WHERE external_ref=? AND status IN ('PENDING','PENDING_PROOF','PENDING_REVIEW')", (receipt, external_ref)).rowcount
    if updated == 0:
        return "ALREADY_PAID"

    offer = resolve_offer(order["plan"])
    item_title = offer["title"] if offer else format_order_item_name(order["plan"])
    if offer and offer["kind"] == "button":
        await give_vip_access(int(order["user_id"]), order["plan"], days_override=offer["days"], receipt=receipt, title_override=item_title)
    else:
        await give_vip_access(int(order["user_id"]), order["plan"], receipt=receipt, title_override=item_title)
    await reward_referrer_if_eligible(int(order["user_id"]))

    amt_display = order["local_amount"] or (f"${order['amount_usd']:.2f} USD" if order["market_scope"] == "INTL" else f"KES {order['amount']}")
    await send_message(ADMIN_USER_ID, f"✅ <b>{PROJECT_NAME} PAYMENT CONFIRMED</b>\n\n🆔 User: <code>{order['user_id']}</code>\n🌍 Scope: <b>{order['market_scope']}</b> | 💳 <code>{html_escape(order['provider'].upper())}</code>\n🎫 Item: <b>{html_escape(item_title)}</b> — {html_escape(amt_display)}\n🧾 Receipt: <code>{html_escape(receipt or 'N/A')}</code>")
    return "PAID"


async def process_payhero_callback(payload):
    data = payload.get("response") if isinstance(payload.get("response"), dict) else payload
    ext_ref = data.get("ExternalReference") or data.get("external_reference") or payload.get("external_reference")
    checkout_id = data.get("CheckoutRequestID") or data.get("checkout_request_id") or payload.get("CheckoutRequestID")
    ph_ref = data.get("reference") or payload.get("reference")
    order = None
    with connect_db() as db:
        if ext_ref:
            order = db.execute("SELECT * FROM orders WHERE external_ref=?", (ext_ref,)).fetchone()
        if not order and checkout_id:
            order = db.execute("SELECT * FROM orders WHERE checkout_id=?", (checkout_id,)).fetchone()
        if not order and ph_ref:
            order = db.execute("SELECT * FROM orders WHERE payhero_reference=?", (ph_ref,)).fetchone()
    if not order or order["status"] == "PAID":
        return
    if not order["payhero_reference"]:
        if str(data.get("Status") or "").upper() == "FAILED" or data.get("ResultCode") not in (None, 0, "0"):
            await finalize_paid_order(order, verified={"status": "FAILED"}, callback_data=data)
        return
    try:
        verified = await verify_payhero_transaction(order["payhero_reference"])
    except Exception:
        if str(data.get("Status") or "").upper() == "FAILED" or data.get("ResultCode") not in (None, 0, "0"):
            await finalize_paid_order(order, verified={"status": "FAILED"}, callback_data=data)
        return
    if _extract_payhero_status(verified) in ("QUEUED", "PENDING", "") and (str(data.get("Status") or "").upper() == "SUCCESS" or str(data.get("ResultCode")) == "0"):
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
        await send_message(chat_id, "⚠️ Order not found.", main_menu_keyboard(user_id))
        return
    if order["status"] == "PAID":
        await send_message(chat_id, "✅ This order is already confirmed!", main_menu_keyboard(user_id))
        return
    media_fid = photo_file_id or doc_file_id
    media_kind = "photo" if photo_file_id else ("document" if doc_file_id else "text")
    if not media_fid and not text:
        await send_message(chat_id, "⚠️ Please send your payment screenshot, PDF/file receipt, or transaction code.")
        return

    with connect_db() as db:
        db.execute("UPDATE orders SET status='PENDING_REVIEW', proof_file_id=COALESCE(?, proof_file_id), proof_type=?, mpesa_receipt=? WHERE external_ref=?", (media_fid, media_kind, text[:120] if text else media_kind.upper(), external_ref))
        order = db.execute("SELECT * FROM orders WHERE external_ref=?", (external_ref,)).fetchone()

    item_title = format_order_item_name(order["plan"])
    amt_display = order["local_amount"] or f"KES {order['amount']:,}"
    u = get_user(user_id)
    uname = f"@{u['username']}" if (u and u["username"]) else (u["first_name"] if u else str(user_id))
    auto_deliver = get_setting("auto_confirm_screenshot", "0") == "1" and bool(media_fid)

    cap = f"📸 <b>PAYMENT PROOF ({order['market_scope']} / {media_kind.upper()})</b>\n\n👤 {html_escape(uname)} (<code>{user_id}</code>)\n🎫 <b>{html_escape(item_title)}</b>\n💰 <b>{html_escape(amt_display)}</b>\n💳 <code>{html_escape(order['provider'].upper())}</code> | 🔗 <code>{external_ref}</code>" + (f"\n📝 <code>{html_escape(text[:200])}</code>" if text else "")
    kb = None if auto_deliver else {"inline_keyboard": [[{"text": "✅ Confirm & Deliver", "callback_data": f"admin:approve:{external_ref}", "style": "success"}, {"text": "❌ Reject", "callback_data": f"admin:reject:{external_ref}", "style": "danger"}], [{"text": "💬 Reply", "callback_data": f"admin:reply:{user_id}", "style": "primary"}]]}

    if photo_file_id:
        await telegram("sendPhoto", {"chat_id": ADMIN_USER_ID, "photo": photo_file_id, "caption": cap[:1020], "parse_mode": "HTML", **({"reply_markup": kb} if kb else {})})
    elif doc_file_id:
        await telegram("sendDocument", {"chat_id": ADMIN_USER_ID, "document": doc_file_id, "caption": cap[:1020], "parse_mode": "HTML", **({"reply_markup": kb} if kb else {})})
    else:
        await send_message(ADMIN_USER_ID, cap, kb)

    if auto_deliver:
        await finalize_paid_order(order, manual_admin=True)
    else:
        await send_message(chat_id, f"✅ <b>PAYMENT PROOF RECEIVED!</b>\n🎫 <b>{html_escape(item_title)}</b>\n🔗 Ref: <code>{external_ref}</code>\n\n⏳ Confirming your payment now — delivery is automatic once confirmed!", {"inline_keyboard": [[{"text": "🏠 Main Menu", "callback_data": "menu:home", "style": "primary"}]]})


# --------------------------------------------------
# MATCHES, ACCUMULATORS & CHANNEL PUBLISHING
# --------------------------------------------------

def format_match_block(m, index=None):
    badges = {"PENDING": "⏳ PENDING", "WON": "✅ WON", "LOST": "❌ LOST", "VOID": "⚪ VOID"}
    prefix = f"<b>#{index}</b> " if index else ""
    return f"{prefix}⚽ <b>{html_escape(m['match_name'])}</b>\n🎯 {html_escape(m['market'])}: <b>{html_escape(m['tip'])}</b>  |  📈 Odds: <b>{html_escape(m['odds'])}</b>\n🗓 {html_escape(m['match_date'])} ⏰ {html_escape(m['kickoff_time'])}  •  {badges.get(m['result'], m['result'])}\n"


def render_batch_text(tier, rows):
    vip_odds_info = get_setting("vip_odds_info")
    header = f"💎 <b>{PROJECT_NAME} — VIP PICKS</b> ({BRAND_TAG})\n━━━━━━━━━━━━━━━━━━━━\n\n" if tier == "VIP" else f"⚽ <b>{PROJECT_NAME} — TODAY'S PREDICTIONS</b> ({BRAND_TAG})\n━━━━━━━━━━━━━━━━━━━━\n\n"
    body = "\n".join(format_match_block(m, i + 1) for i, m in enumerate(rows))
    footer = f"\n━━━━━━━━━━━━━━━━━━━━\n🎯 {html_escape(vip_odds_info)}\n⚠️ <i>Football predictions involve risk.</i>\n🔥 <b>{PROJECT_NAME} ({BRAND_TAG})</b>"
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
        await telegram("editMessageText", {"chat_id": target_chat, "message_id": msg_id, "parse_mode": "HTML", "text": full_text[:4090], "reply_markup": channel_post_keyboard(channel_msg_id=msg_id, include_reactions=(tier == "FREE")), "disable_web_page_preview": True})
    except Exception:
        pass


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
    msg = await telegram("sendMessage", {"chat_id": target_chat, "parse_mode": "HTML", "text": full_text[:4090], "reply_markup": channel_post_keyboard(selected_btn_ids=sel_btns, include_footer=include_footer, include_reactions=False), "disable_web_page_preview": True})
    msg_id = msg["message_id"]
    save_channel_post_meta(msg_id, sel_btns, show_reacts, include_footer)
    with connect_db() as db:
        for r in rows:
            db.execute("UPDATE matches SET publish_status='PUBLISHED', channel_msg_id=? WHERE id=?", (msg_id, r["id"]))
    if show_reacts:
        try:
            await telegram("editMessageReplyMarkup", {"chat_id": target_chat, "message_id": msg_id, "reply_markup": channel_post_keyboard(channel_msg_id=msg_id)})
        except Exception:
            pass
    return msg


async def publish_vip_post(selected_btn_ids=None, include_footer=True, include_reactions=True):
    await ensure_bot_username()
    main_chat = get_setting("main_channel_id", CHANNEL_ID)
    sel_btns = selected_btn_ids or []
    msg = await telegram("sendMessage", {
        "chat_id": main_chat, "parse_mode": "HTML",
        "text": f"👑 <b>{PROJECT_NAME}</b> ({BRAND_TAG})\n\n🎯 {html_escape(get_setting('vip_odds_info'))}\n\n⚡ Football analysis, VIP picks & accumulator tickets.\n🌍 Local M-Pesa/Airtel & International USD/Crypto supported.\n\n👇 <b>TAP BELOW TO JOIN VIP OR GET A TICKET</b>",
        "reply_markup": channel_post_keyboard(selected_btn_ids=sel_btns, include_footer=include_footer, include_reactions=False),
    })
    msg_id = msg["message_id"]
    save_channel_post_meta(msg_id, sel_btns, include_reactions, include_footer)
    if include_reactions:
        try:
            await telegram("editMessageReplyMarkup", {"chat_id": main_chat, "message_id": msg_id, "reply_markup": channel_post_keyboard(channel_msg_id=msg_id)})
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
        msg = await telegram("sendPhoto", {"chat_id": main_chat, "photo": photo_file_id, "caption": formatted[:1020], "parse_mode": "HTML", **({"reply_markup": kb} if kb else {})})
    else:
        msg = await telegram("sendMessage", {"chat_id": main_chat, "parse_mode": "HTML", "text": formatted[:4090], "disable_web_page_preview": True, **({"reply_markup": kb} if kb else {})})
    msg_id = msg["message_id"]
    save_channel_post_meta(msg_id, sel_btns, include_reactions, include_footer)
    if include_reactions:
        try:
            await telegram("editMessageReplyMarkup", {"chat_id": main_chat, "message_id": msg_id, "reply_markup": channel_post_keyboard(channel_msg_id=msg_id)})
        except Exception:
            pass
    return msg


async def publish_vip_channel_post(text_body="", photo_file_id=None):
    vip_chat = get_setting("vip_channel_id", VIP_CHANNEL_ID)
    formatted = f"💎 <b>{PROJECT_NAME} — VIP EXCLUSIVE</b> ({BRAND_TAG})\n\n{html_escape(text_body)}" if text_body else f"💎 <b>{PROJECT_NAME} — VIP EXCLUSIVE</b> ({BRAND_TAG})"
    if photo_file_id:
        return await telegram("sendPhoto", {"chat_id": vip_chat, "photo": photo_file_id, "caption": formatted[:1020], "parse_mode": "HTML"})
    return await telegram("sendMessage", {"chat_id": vip_chat, "text": formatted[:4090], "parse_mode": "HTML", "disable_web_page_preview": True})


async def publish_accumulator_to_channel(accum_id, target="MAIN"):
    await ensure_bot_username()
    with connect_db() as db:
        a = db.execute("SELECT * FROM accumulators WHERE id=?", (int(accum_id),)).fetchone()
    if not a:
        return None
    sels = json.loads(a["selections_json"]) if a["selections_json"] else []
    legs_text = "\n".join(f"<b>{i + 1}.</b> ⚽ <b>{html_escape(s.get('match', ''))}</b>\n    🎯 Tip: <b>{html_escape(s.get('tip', ''))}</b> | 📈 Odds: <b>{html_escape(str(s.get('odds', '')))}</b>" for i, s in enumerate(sels))
    is_vip = (target.upper() == "VIP")
    target_chat = get_setting("vip_channel_id", VIP_CHANNEL_ID) if is_vip else get_setting("main_channel_id", CHANNEL_ID)
    msg_text = f"🎯 <b>{PROJECT_NAME} — {'VIP ' if is_vip else ''}ACCUMULATOR #{a['id']}</b>\n📊 {html_escape(a['category'])} | 📈 <b>{html_escape(str(a['total_odds']))}x</b>\n━━━━━━━━━━━━━━━━━━━━\n\n{legs_text}\n\n━━━━━━━━━━━━━━━━━━━━\n🔥 <b>{PROJECT_NAME} ({BRAND_TAG})</b>"
    msg = await telegram("sendMessage", {"chat_id": target_chat, "parse_mode": "HTML", "text": msg_text[:4090], "reply_markup": channel_post_keyboard(selected_btn_ids=[], include_footer=not is_vip, include_reactions=False), "disable_web_page_preview": True})
    msg_id = msg["message_id"]
    save_channel_post_meta(msg_id, [], not is_vip, not is_vip)
    if not is_vip:
        try:
            await telegram("editMessageReplyMarkup", {"chat_id": target_chat, "message_id": msg_id, "reply_markup": channel_post_keyboard(channel_msg_id=msg_id)})
        except Exception:
            pass
    with connect_db() as db:
        db.execute("UPDATE accumulators SET status=?, channel_msg_id=? WHERE id=?", ("PUBLISHED_VIP" if is_vip else "PUBLISHED_MAIN", msg_id, int(accum_id)))
    return msg


# --------------------------------------------------
# USER VIEWS (ISOLATED LOCAL VS INTERNATIONAL)
# --------------------------------------------------

async def show_country_welcome(chat_id):
    await send_message(chat_id, "🌍 <b>Welcome to Prime Tips Empire!</b>\n\nChoose your country/region to show the right plans, currency & payment methods:", country_welcome_keyboard())


async def show_main_menu(chat_id, user_id):
    prefs = get_user_prefs(user_id)
    c_info = COUNTRIES.get(prefs["country"])
    country_line = f"{c_info['flag']} {c_info['name']} ({prefs['currency']})" if (prefs["confirmed"] and c_info) else f"Not set ({prefs['currency']})"
    vip_badge = "💎 <b>VIP ACTIVE</b>" if has_active_vip(user_id) else "🆓 <b>FREE MEMBER</b>"
    await send_message(
        chat_id,
        f"👑 <b>{PROJECT_NAME}</b> • {BRAND_TAG}\n━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Status: {vip_badge} | {country_line}\n"
        f"🎯 {html_escape(get_setting('vip_odds_info'))}\n\n"
        "⚽ <i>International Football Predictions & Analytics Platform</i>",
        main_menu_keyboard(user_id),
    )


async def show_ticket_button_checkout(chat_id, user_id, btn_id, force_scope=None):
    b = get_custom_button(btn_id)
    if not b or not b["is_active"]:
        await send_message(chat_id, "⚠️ This ticket/button is no longer active.", main_menu_keyboard(user_id))
        return
    contact_url = get_setting("admin_contact_url", f"https://t.me/{BRAND_TAG.lstrip('@')}")
    if b["button_type"] == "URL" and b["url"]:
        await send_message(chat_id, f"🔗 <b>{html_escape(b['label'])}</b>", {"inline_keyboard": [[{"text": b["label"], "url": b["url"], "style": "primary"}], [{"text": "🏠 Main Menu", "callback_data": "menu:home"}]]})
        return

    prefs = get_user_prefs(user_id)
    use_intl = (force_scope == "INTL") if force_scope else prefs["is_intl"]
    days = int(b["days"])
    img_cnt = count_offer_media(f"btn:{b['id']}")
    days_line = f"⏳ Access: <b>{days} Day{'s' if days != 1 else ''} VIP</b>\n" if days > 0 else "🎟️ Type: <b>Instant Ticket Image Delivery</b>\n"
    img_line = f"🖼️ Auto-Delivery: <b>{img_cnt} Ticket Image(s)</b>\n" if img_cnt > 0 else ""
    desc_line = f"📝 {html_escape(b['description'])}\n" if (b["description"] or "").strip() else ""

    if use_intl:
        intl_usd = float(b["intl_price_usd"] or 0)
        kb_rows = []
        if intl_usd > 0:
            price_line, _ = format_usd_price(intl_usd, prefs["currency"] if prefs["is_intl"] else "USD")
            kb_rows.append([{"text": "🪙 Pay with Crypto (CryptAPI — 100+ Coins)", "callback_data": f"buyibtn:{b['id']}:crypto", "style": "success"}])
            kb_rows.append([{"text": "💳 International Card / PayPal / Transfer", "callback_data": f"buyibtn:{b['id']}:intl_transfer", "style": "primary"}])
            kb_rows.append([{"text": f"💬 Contact {BRAND_TAG}", "url": contact_url, "style": "primary"}])
        else:
            price_line = "Contact Admin for International Price"
            kb_rows.append([{"text": f"💬 Contact {BRAND_TAG} for Price", "url": contact_url, "style": "success"}])
        kb_rows.append([{"text": "🇰🇪 Switch to Local East Africa Price (KES)", "callback_data": f"openbtn:{b['id']}"}])
        kb_rows.append([{"text": "🏠 Main Menu", "callback_data": "menu:home"}])
        await send_message(
            chat_id,
            f"🌍 <b>{html_escape(b['label'])} — INTERNATIONAL CHECKOUT</b>\n━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 Price: <b>{html_escape(price_line)}</b>\n{days_line}{img_line}{desc_line}\n"
            "🪙 <i>Crypto activates automatically after blockchain confirmation.</i>",
            {"inline_keyboard": kb_rows},
        )
        return

    # Local East Africa checkout (isolated from international)
    price = int(b["price"])
    till_no = get_setting("mpesa_till_number", DEFAULT_MPESA_TILL)
    airtel_no = get_setting("airtel_money_number", DEFAULT_AIRTEL_NUMBER)
    price_line = f"KES {price:,}"
    if price > 0 and prefs["currency"] in ("TZS", "UGX", "ZMW"):
        conv, ok = format_converted_price(price, prefs["currency"])
        if ok:
            price_line = conv

    kb_rows = []
    if price > 0:
        kb_rows.append([{"text": f"📲 Kenya: M-Pesa STK Push (KES {price:,})", "callback_data": f"buybtn:{b['id']}:mpesa", "style": "success"}])
        kb_rows.append([{"text": f"🏪 Kenya: Manual M-Pesa Till {till_no}", "callback_data": f"buybtn:{b['id']}:mpesa_till", "style": "primary"}])
        kb_rows.append([{"text": f"🔴 TZ / UG / ZM: Airtel Money ({airtel_no})", "callback_data": f"buybtn:{b['id']}:airtel", "style": "danger"}])
    else:
        kb_rows.append([{"text": f"💬 Contact {BRAND_TAG}", "url": contact_url, "style": "success"}])
    kb_rows.append([{"text": "🌍 Outside East Africa? Pay International ($ / Crypto)", "callback_data": f"openibtn:{b['id']}", "style": "primary"}])
    kb_rows.append([{"text": "🏠 Main Menu", "callback_data": "menu:home"}])

    await send_message(
        chat_id,
        f"🎫 <b>{html_escape(b['label'])} — LOCAL CHECKOUT</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Price: <b>{html_escape(price_line if price > 0 else 'Contact Admin')}</b>\n{days_line}{img_line}{desc_line}\n"
        f"🇰🇪 <b>Kenya:</b> M-Pesa STK Push or Till <code>{html_escape(till_no)}</code>\n"
        f"🇹🇿🇺🇬🇿🇲 <b>TZ/UG/ZM:</b> Airtel Money <code>{html_escape(airtel_no)}</code>",
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
    selected = [m for m in avail if m["id"] in draft["match_ids"]]
    total_odds = 1.0
    for m in selected:
        try:
            total_odds *= float(m["odds"])
        except ValueError:
            pass
    rows = [
        [{"text": ("✅ " if draft["category"] == c else "") + c.replace(" Odds", ""), "callback_data": f"accum:cat:{c}"} for c in ACCUM_CATEGORIES[:3]],
        [{"text": ("✅ " if draft["category"] == c else "") + c.replace(" Odds", ""), "callback_data": f"accum:cat:{c}"} for c in ACCUM_CATEGORIES[3:]],
    ]
    for m in avail:
        picked = m["id"] in draft["match_ids"]
        rows.append([{"text": f"{'✅' if picked else '➕'} {m['match_name']} | {m['tip']} ({m['odds']})", "callback_data": f"accum:toggle:{m['id']}", "style": "success" if picked else "primary"}])
    if selected:
        rows.append([{"text": "🎟️ Submit Ticket", "callback_data": "accum:save", "style": "danger"}, {"text": "🗑 Clear", "callback_data": "accum:clear"}])
    rows.append([{"text": "🏠 Main Menu", "callback_data": "menu:home"}])
    sel_txt = "\n".join(f"• <b>{html_escape(m['match_name'])}</b> — {html_escape(m['tip'])} (<code>{m['odds']}</code>)" for m in selected) or "<i>Tap matches below to add legs.</i>"
    await send_message(chat_id, f"🎯 <b>ACCUMULATOR BUILDER</b>\nCategory: <b>{html_escape(draft['category'])}</b> | Odds: <b>{total_odds:.2f}x</b>\n\n{sel_txt}", {"inline_keyboard": rows})


async def show_vip_screen(chat_id, user_id, force_scope=None):
    prefs = get_user_prefs(user_id)
    if not prefs["confirmed"] and not force_scope:
        await send_message(chat_id, "🌍 Please select your country/region first so we show the right VIP plans & payment options:", country_welcome_keyboard())
        return
    use_intl = (force_scope == "INTL") if force_scope else prefs["is_intl"]

    if use_intl:
        plans = get_all_intl_plans(only_active=True)
        disp_curr = prefs["currency"] if prefs["is_intl"] else "USD"
        details = "\n".join(f"🌍 <b>{html_escape(p['title'])}</b> — <b>{format_usd_price(p['price_usd'], disp_curr)[0]}</b>\n<i>   {html_escape(p['benefits'])}</i>" for p in plans)
        await send_message(
            chat_id,
            f"🌍 <b>{PROJECT_NAME} — INTERNATIONAL VIP PLANS</b>\n"
            f"<i>Global USD Pricing • Isolated Crypto (100+ Coins) & International Payments</i>\n\n{details}\n\n👇 Select an international plan:",
            intl_plans_keyboard(disp_curr),
        )
        return

    c = COUNTRIES.get(prefs["country"], COUNTRIES["KE"])
    with connect_db() as db:
        plans = db.execute("SELECT * FROM plans ORDER BY price").fetchall()
    details = "\n".join(f"👑 <b>{html_escape(p['title'])}</b> — <b>{format_converted_price(p['price'], prefs['currency'])[0]}</b>\n<i>   {html_escape(p['benefits'])}</i>" for p in plans)
    await send_message(
        chat_id,
        f"👑 <b>{PROJECT_NAME} — LOCAL VIP MEMBERSHIP</b>\n"
        f"{c['flag']} Region: <b>{c['name']}</b> • Currency: <b>{prefs['currency']}</b>\n\n{details}\n\n👇 Select a package:",
        local_plans_keyboard(prefs["currency"]),
    )


async def show_stats_and_results(chat_id, user_id, only_settled=False):
    with connect_db() as db:
        total = db.execute("SELECT COUNT(*) AS n FROM matches").fetchone()["n"]
        won = db.execute("SELECT COUNT(*) AS n FROM matches WHERE result='WON'").fetchone()["n"]
        lost = db.execute("SELECT COUNT(*) AS n FROM matches WHERE result='LOST'").fetchone()["n"]
        void = db.execute("SELECT COUNT(*) AS n FROM matches WHERE result='VOID'").fetchone()["n"]
        pending = db.execute("SELECT COUNT(*) AS n FROM matches WHERE result='PENDING'").fetchone()["n"]
        rows = db.execute("SELECT * FROM matches WHERE result!='PENDING' ORDER BY id DESC LIMIT 12" if only_settled else "SELECT * FROM matches WHERE publish_status='PUBLISHED' ORDER BY id DESC LIMIT 12").fetchall()
    decided = won + lost
    win_pct = f"{(won / decided * 100):.1f}%" if decided > 0 else "N/A"
    body = "\n".join(format_match_block(m, i + 1) for i, m in enumerate(rows)) if rows else "<i>No published records yet.</i>"
    title = "🏆 <b>RESULTS & ACCURACY</b>" if only_settled else "📊 <b>FOOTBALL STATISTICS</b>"
    await send_message(chat_id, f"{title}\n📈 Win Rate: <b>{win_pct}</b> | ✅ {won} · ❌ {lost} · ⚪ {void} · ⏳ {pending} (Total: {total})\n━━━━━━━━━━━━━━━━━━━━\n\n{body}", {"inline_keyboard": [[{"text": "🏠 Main Menu", "callback_data": "menu:home", "style": "primary"}]]})


# --------------------------------------------------
# ADMIN PANELS (INCLUDING INTERNATIONAL-ONLY PRICING MANAGER)
# --------------------------------------------------

async def show_admin_intl_packages(chat_id, message_id=None):
    if str(chat_id) != str(ADMIN_USER_ID):
        return
    plans = get_all_intl_plans(only_active=False)
    buttons = [b for b in get_all_custom_buttons(only_active=False) if b["button_type"] == "TICKET"]
    rows = []
    lines = ["🌍 <b>INTERNATIONAL-ONLY PLANS & PRICES ($ USD)</b>", "━━━━━━━━━━━━━━━━━━━━", "<i>Completely isolated from East Africa KES plans & payments.</i>\n", "<b>VIP International Plans:</b>"]
    for p in plans:
        dur = "Lifetime" if p["is_lifetime"] else f"{p['days']}d"
        lines.append(f"{'🟢' if p['is_active'] else '⚫'} <b>{html_escape(p['title'])}</b> ({dur}) — <b>${float(p['price_usd']):,.2f} USD</b>")
        rows.append([
            {"text": f"💵 {p['title']} (${float(p['price_usd']):,.2f})", "callback_data": f"admin:edit_intl_price:{p['name']}", "style": "success"},
            {"text": "🗑️", "callback_data": f"admin:del_intl_plan:{p['name']}", "style": "danger"},
        ])
    lines.append("\n<b>Ticket Buttons — International USD Prices:</b>")
    for b in buttons:
        ip = float(b["intl_price_usd"] or 0)
        lines.append(f"• {html_escape(b['label'])} — <b>{'$' + f'{ip:,.2f} USD' if ip > 0 else 'Not set (Contact Admin)'}</b>")
        rows.append([{"text": f"🌍 {b['label']} (${ip:,.2f})", "callback_data": f"admin:btn_setintlprice:{b['id']}", "style": "primary"}])

    rows.append([{"text": "➕ Add International VIP Plan", "callback_data": "admin:add_intl_plan", "style": "success"}])
    rows.append([{"text": "🪙 CryptAPI Crypto Wallets", "callback_data": "admin:cryptapi", "style": "primary"}, {"text": "📝 Intl Payment Note", "callback_data": "admin:edit_intl_note", "style": "primary"}])
    rows.append([{"text": "⬅️ Back to Admin", "callback_data": "admin:home"}])
    await edit_or_send(chat_id, message_id, "\n".join(lines), {"inline_keyboard": rows})


async def show_admin_buttons(chat_id, message_id=None):
    if str(chat_id) != str(ADMIN_USER_ID):
        return
    buttons = get_all_custom_buttons(only_active=False)
    lines, rows = [], []
    for b in buttons:
        dot = "🟢" if b["is_active"] else "⚫"
        info = "🔗 Link" if b["button_type"] == "URL" else f"KES {int(b['price']):,} | ${float(b['intl_price_usd'] or 0):,.2f} | 🖼️{count_offer_media(f'btn:{b[\"id\"]}')}"
        lines.append(f"{dot} <b>{html_escape(b['label'])}</b> — {info}")
        rows.append([{"text": f"⚙️ {b['label']} ({info})", "callback_data": f"admin:btn_view:{b['id']}", "style": b["style"] if b["style"] in BUTTON_STYLES else "primary"}])
    rows.append([{"text": "➕ Add Ticket Button", "callback_data": "admin:btn_add_ticket", "style": "success"}, {"text": "🔗 Add Link Button", "callback_data": "admin:btn_add_url", "style": "primary"}])
    rows.append([{"text": "⬅️ Back to Admin", "callback_data": "admin:home"}])
    await edit_or_send(chat_id, message_id, "🎛️ <b>CHANNEL BUTTONS & TICKETS</b>\n━━━━━━━━━━━━━━━━━━━━\n\n" + ("\n".join(lines) if lines else "<i>No buttons yet.</i>"), {"inline_keyboard": rows})


async def show_admin_button_detail(chat_id, btn_id, message_id=None):
    if str(chat_id) != str(ADMIN_USER_ID):
        return
    b = get_custom_button(btn_id)
    if not b:
        await show_admin_buttons(chat_id, message_id)
        return
    if b["button_type"] == "URL":
        rows = [[{"text": "✏️ Rename Label", "callback_data": f"admin:btn_rename:{b['id']}", "style": "primary"}], [{"text": f"🎨 Color: {b['style'].title()}", "callback_data": f"admin:btn_style:{b['id']}"}], [{"text": "🗑️ Remove", "callback_data": f"admin:btn_del:{b['id']}", "style": "danger"}], [{"text": "⬅️ All Buttons", "callback_data": "admin:buttons"}]]
        await edit_or_send(chat_id, message_id, f"🔗 <b>{html_escape(b['label'])}</b>\nURL: <code>{html_escape(b['url'])}</code>", {"inline_keyboard": rows})
        return
    img_count = count_offer_media(f"btn:{b['id']}")
    ip = float(b["intl_price_usd"] or 0)
    txt = (
        f"🎫 <b>{html_escape(b['label'])}</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        f"🇰🇪 Local Price: <b>KES {int(b['price']):,}</b>\n"
        f"🌍 International Price: <b>${ip:,.2f} USD</b>\n"
        f"📅 VIP Days: <b>{b['days']}d</b> | 🖼️ Ticket Images: <b>{img_count}</b>\n"
        f"📝 Description: <i>{html_escape(b['description'] or 'None')}</i>"
    )
    rows = [
        [{"text": f"🖼️ Ticket Images ({img_count})", "callback_data": f"admin:btn_media:{b['id']}", "style": "success"}],
        [{"text": f"🇰🇪 Local Price (KES {int(b['price']):,})", "callback_data": f"admin:btn_setprice:{b['id']}", "style": "success"}, {"text": f"🌍 Intl Price (${ip:,.2f})", "callback_data": f"admin:btn_setintlprice:{b['id']}", "style": "danger"}],
        [{"text": f"📅 VIP Days ({b['days']}d)", "callback_data": f"admin:btn_setdays:{b['id']}", "style": "primary"}, {"text": "✏️ Rename", "callback_data": f"admin:btn_rename:{b['id']}", "style": "primary"}],
        [{"text": "📝 Description", "callback_data": f"admin:btn_setdesc:{b['id']}"}, {"text": f"🎨 Color: {b['style'].title()}", "callback_data": f"admin:btn_style:{b['id']}"}],
        [{"text": "🗑️ Remove Button", "callback_data": f"admin:btn_del:{b['id']}", "style": "danger"}, {"text": "⬅️ All Buttons", "callback_data": "admin:buttons"}],
    ]
    await edit_or_send(chat_id, message_id, txt, {"inline_keyboard": rows})


async def show_admin_button_media(chat_id, btn_id, message_id=None):
    if str(chat_id) != str(ADMIN_USER_ID):
        return
    b = get_custom_button(btn_id)
    if not b:
        await show_admin_buttons(chat_id, message_id)
        return
    media_rows = get_offer_media(f"btn:{btn_id}")
    rows = [[{"text": "➕ Upload Ticket Image(s)", "callback_data": f"admin:btn_media_add:{btn_id}", "style": "success"}]]
    if media_rows:
        rows.append([{"text": f"👁️ Preview ({len(media_rows)})", "callback_data": f"admin:btn_media_prev:{btn_id}", "style": "primary"}, {"text": "🗑️ Clear All", "callback_data": f"admin:btn_media_clr:{btn_id}", "style": "danger"}])
    rows.append([{"text": "⬅️ Back to Button", "callback_data": f"admin:btn_view:{btn_id}"}])
    await edit_or_send(chat_id, message_id, f"🖼️ <b>TICKET IMAGES — {html_escape(b['label'])}</b>\nSaved: <b>{len(media_rows)}</b>\n<i>Auto-delivered to both Local & International buyers on payment confirmation!</i>", {"inline_keyboard": rows})


async def show_post_button_picker(chat_id, user_id, picker, message_id=None):
    if not is_admin(user_id) or str(chat_id) != str(ADMIN_USER_ID):
        return
    set_session(user_id, "POST_PICKER", json.dumps(picker))
    buttons = get_all_custom_buttons(only_active=True)
    selected = set(int(x) for x in picker.get("btn_ids", []))
    mode = picker.get("mode", "custom")
    action_labels = {"custom": "➡️ Next: Write Post / Send Photo", "vip_promo": "🚀 Publish VIP Promo", "free_matches": "🚀 Publish FREE Matches"}
    rows = [[{"text": f"{'✅' if int(b['id']) in selected else '⬜'} {b['label']}", "callback_data": f"postpick:toggle:{b['id']}", "style": "success" if int(b['id']) in selected else "primary"}] for b in buttons]
    rows.append([{"text": "✅ Select All", "callback_data": "postpick:all", "style": "success"}, {"text": "⬜ Clear", "callback_data": "postpick:none", "style": "primary"}])
    rows.append([{"text": f"👑 Footer: {'ON' if picker.get('footer', True) else 'OFF'}", "callback_data": "postpick:footer"}, {"text": f"🔥 Reacts: {'ON' if picker.get('react', True) else 'OFF'}", "callback_data": "postpick:react"}])
    rows.append([{"text": action_labels.get(mode, "➡️ Next"), "callback_data": "postpick:next", "style": "success"}])
    rows.append([{"text": "🎛️ Manage Buttons", "callback_data": "admin:buttons"}, {"text": "❌ Cancel", "callback_data": "admin:home", "style": "danger"}])
    await edit_or_send(chat_id, message_id, f"📝 <b>CHOOSE CHANNEL BUTTONS</b>\nSelected: <b>{len(selected)}</b>", {"inline_keyboard": rows})


async def show_admin_cryptapi_panel(chat_id, message_id=None):
    if str(chat_id) != str(ADMIN_USER_ID):
        return
    coins, networks = await fetch_cryptapi_catalog()
    wallets = get_cryptapi_wallets_map()
    enabled_cnt = sum(1 for t in coins if get_wallet_for_ticker(t))
    lines = ["🪙 <b>CRYPTAPI CRYPTO WALLETS</b>", f"🌐 Callback: <code>{html_escape(get_cryptapi_callback_base())}</code>", f"✅ Enabled Assets: <b>{enabled_cnt} / {len(coins)}</b> across {len(networks)} networks\n"]
    rows = []
    for key, _, _ in CRYPTO_WALLET_GROUPS:
        val = wallets.get(key, "")
        short_val = f"<code>{html_escape(val[:8])}...{html_escape(val[-6:])}</code>" if len(val) > 16 else (f"<code>{html_escape(val)}</code>" if val else "<i>Not set</i>")
        lines.append(f"{'✅' if val else '⬜'} <b>{key.upper()}</b>: {short_val}")
        rows.append([{"text": f"{'✏️' if val else '➕'} Set {key.upper()} Wallet", "callback_data": f"admin:cry_set:{key}", "style": "success" if val else "primary"}])
    rows.append([{"text": "🎯 Specific Coin Override", "callback_data": "admin:cry_set_custom", "style": "primary"}, {"text": "🔄 Refresh Catalog", "callback_data": "admin:cry_refresh", "style": "success"}])
    rows.append([{"text": "⬅️ Back", "callback_data": "admin:channels"}])
    await edit_or_send(chat_id, message_id, "\n".join(lines), {"inline_keyboard": rows})


async def show_admin_payhero_diag(chat_id, message_id=None):
    if str(chat_id) != str(ADMIN_USER_ID):
        return
    cur_ch = get_setting("payhero_channel_id", PAYHERO_CHANNEL_ID)
    diag = await fetch_payhero_diagnostics()
    ch_res, wal_res = diag["channels"], diag["wallet"]
    lines = ["🩺 <b>PAYHERO DIAGNOSTICS</b>", f"🔢 Channel ID: <code>{html_escape(str(cur_ch))}</code>", f"🔗 Callback: <code>{html_escape(PUBLIC_URL.rstrip('/') + '/payhero/callback')}</code>\n"]
    w_status, w_data = wal_res.get("status"), wal_res.get("data")
    if w_status == 200 and isinstance(w_data, dict):
        bal = w_data.get("balance") or (w_data.get("data") or {}).get("balance")
        lines.append(f"💰 Service Wallet: <b>HTTP 200</b> — Balance: <b>KES {html_escape(str(bal if bal is not None else w_data))}</b>")
    else:
        lines.append(f"❌ Service Wallet: <b>HTTP {w_status}</b> — <code>{html_escape(str(wal_res.get('error') or w_data)[:160])}</code>")

    c_status, c_data = ch_res.get("status"), ch_res.get("data")
    rows = []
    if c_status == 200:
        items = c_data if isinstance(c_data, list) else (c_data.get("channels") or c_data.get("data") or [] if isinstance(c_data, dict) else [])
        lines.append(f"✅ Active Channels (<b>{len(items)}</b>):")
        for ch in items[:10]:
            cid_val = ch.get("id") or ch.get("channel_id")
            cname = ch.get("name") or ch.get("channel_type") or "Channel"
            is_cur = str(cid_val) == str(cur_ch)
            lines.append(f"  {'✅' if is_cur else '•'} ID <code>{cid_val}</code> — {html_escape(str(cname))}")
            if cid_val and not is_cur:
                rows.append([{"text": f"🔄 Use Channel {cid_val} ({cname})", "callback_data": f"admin:ph_use_ch:{cid_val}", "style": "success"}])
    else:
        lines.append(f"❌ Channels Auth: <b>HTTP {c_status}</b> — <code>{html_escape(str(ch_res.get('error') or c_data)[:160])}</code>")
    rows.append([{"text": "🔄 Re-run Diagnostics", "callback_data": "admin:payhero_diag", "style": "primary"}, {"text": "⬅️ Settings", "callback_data": "admin:channels"}])
    await edit_or_send(chat_id, message_id, "\n".join(lines), {"inline_keyboard": rows})


# --------------------------------------------------
# MESSAGE & CALLBACK HANDLERS
# --------------------------------------------------

async def handle_message(message):
    user = message.get("from", {})
    if not user or user.get("is_bot"):
        return
    user_id = user.get("id")
    chat_obj = message.get("chat", {})
    chat_id, chat_type = chat_obj.get("id"), chat_obj.get("type", "private")
    if not user_id or not chat_id:
        return

    photo_list = message.get("photo") or []
    photo_file_id = photo_list[-1]["file_id"] if photo_list else None
    doc_file_id = (message.get("document") or {}).get("file_id")
    text = (message.get("text") or message.get("caption") or "").strip()

    referred_by = int(text.split(None, 1)[1][4:]) if (text.startswith("/start ref_") and text.split(None, 1)[1][4:].isdigit()) else None
    register_user(user, referred_by=referred_by)
    db_user = get_user(user_id)
    if db_user and db_user["is_blocked"] and not is_admin(user_id):
        await send_message(chat_id, "🚫 Your account has been suspended.")
        return

    if message.get("location"):
        sess = get_session(user_id)
        if sess and sess["state"] == "WAIT_LOCATION_PERM":
            clear_session(user_id)
            detected = await reverse_geocode_country(message["location"]["latitude"], message["location"]["longitude"])
            await send_message(chat_id, "🔍 Detecting country...", {"remove_keyboard": True})
            c = COUNTRIES.get(detected)
            if c:
                await send_message(chat_id, f"📍 Detected: {c['flag']} <b>{c['name']}</b> ({c['currency']})\nConfirm?", {"inline_keyboard": [[{"text": "✅ Yes, Confirm", "callback_data": f"country:confirm:{detected}", "style": "success"}], [{"text": "🔄 Change", "callback_data": "country:manual", "style": "primary"}]]})
            else:
                await send_message(chat_id, "Select country manually:", country_manual_keyboard())
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

        if cmd in {"/admin", "/done", "/reply", "/addmatch", "/setprice", "/grant", "/revoke", "/export", "/setsetting"}:
            if not is_admin(user_id):
                await report_unauthorized_admin_attempt(user_id, f"CMD:{cmd}")
                await send_message(chat_id, "⛔ Access Denied.")
                return
            if not is_admin_private_chat(user_id, chat_id, chat_type):
                await send_message(chat_id, "🔒 Admin commands only work in private DM.")
                return
            if cmd == "/admin":
                await send_message(chat_id, f"⚡ <b>{PROJECT_NAME} ADMIN</b> ({BRAND_TAG})", admin_keyboard(user_id))
            elif cmd == "/done":
                sess = get_session(user_id)
                if sess and sess["state"] == "WAIT_BTN_MEDIA":
                    bid = int(sess["payload"])
                    clear_session(user_id)
                    await show_admin_button_media(chat_id, bid)
                else:
                    clear_session(user_id)
                    await send_message(chat_id, "✅ Done.", admin_keyboard(user_id))
            elif cmd == "/reply":
                parts = text.split(None, 2)
                if len(parts) < 3 or not parts[1].isdigit():
                    await send_message(chat_id, "Usage: <code>/reply &lt;user_id&gt; &lt;msg&gt;</code>")
                    return
                await send_message(int(parts[1]), f"💬 <b>ADMIN ({BRAND_TAG})</b>\n\n{html_escape(parts[2])}")
                await send_message(chat_id, "✅ Sent.")
            elif cmd == "/addmatch":
                raw = text.split(None, 1)[1] if len(text.split(None, 1)) > 1 else ""
                parts = [p.strip() for p in raw.split("|")]
                if len(parts) < 7 or parts[0].upper() not in ("FREE", "VIP"):
                    await send_message(chat_id, "Usage: <code>/addmatch FREE | Team A vs Team B | Market | Tip | 1.85 | 2026-11-16 | 19:45 UTC</code>")
                    return
                with connect_db() as db:
                    db.execute("INSERT INTO matches(tier,match_name,market,tip,odds,match_date,kickoff_time,publish_status,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (parts[0].upper(), parts[1], parts[2], parts[3], parts[4], parts[5], parts[6], "QUEUED", utcnow().isoformat()))
                await send_message(chat_id, "✅ Match queued.")
            elif cmd == "/setprice":
                parts = text.split()
                if len(parts) != 3 or not parts[2].isdigit() or not get_plan(parts[1].lower()):
                    await send_message(chat_id, "Usage: <code>/setprice monthly 1000</code>")
                    return
                with connect_db() as db:
                    db.execute("UPDATE plans SET price=? WHERE name=?", (int(parts[2]), parts[1].lower()))
                await send_message(chat_id, f"✅ Updated {parts[1]} to KES {parts[2]}.")
            elif cmd == "/grant":
                parts = text.split()
                if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
                    await give_vip_access(int(parts[1]), f"{parts[2]}-day", days_override=int(parts[2]))
                    await send_message(chat_id, "✅ VIP granted.")
            elif cmd == "/revoke":
                parts = text.split()
                if len(parts) == 2 and parts[1].isdigit():
                    uid = int(parts[1])
                    try:
                        await telegram("banChatMember", {"chat_id": get_setting("vip_channel_id", VIP_CHANNEL_ID), "user_id": uid})
                        await telegram("unbanChatMember", {"chat_id": get_setting("vip_channel_id", VIP_CHANNEL_ID), "user_id": uid, "only_if_banned": True})
                    except Exception:
                        pass
                    with connect_db() as db:
                        db.execute("UPDATE subscriptions SET status='EXPIRED' WHERE user_id=?", (uid,))
                    await send_message(chat_id, "🚫 VIP revoked.")
            elif cmd == "/export":
                await export_csv_backups(chat_id)
            elif cmd == "/setsetting":
                parts = text.split(None, 2)
                allowed = {"main_channel_id", "vip_channel_id", "main_channel_url", "payhero_channel_id", "mpesa_till_number", "airtel_money_number", "referral_reward_days", "vip_odds_info", "admin_contact_url", "website_url", "cryptapi_confirmations", "cryptapi_callback_base", "intl_payment_note"}
                if len(parts) != 3 or parts[1] not in allowed:
                    await send_message(chat_id, f"Allowed: {', '.join(sorted(allowed))}")
                    return
                set_setting(parts[1], parts[2].strip())
                await send_message(chat_id, f"✅ Saved <code>{parts[1]}</code>.")
            return
        await send_message(chat_id, "❓ Unknown command.", main_menu_keyboard(user_id))
        return

    sess = get_session(user_id)
    if sess:
        state, payload = sess["state"], sess["payload"]
        if state == "WAIT_MANUAL_ANALYSIS":
            clear_session(user_id)
            await send_message(chat_id, f"🤖 <b>Manual Match Analysis: {html_escape(text)}</b>\n\nℹ️ Live Football/AI APIs are scheduled for Stage 2.", main_menu_keyboard(user_id))
            return
        if state == "WAIT_MPESA_PHONE":
            phone = normalize_phone(text)
            if not phone:
                await send_message(chat_id, "⚠️ Enter a valid Kenyan number (<code>0712345678</code>) or /cancel.")
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
                await telegram("sendPhoto", {"chat_id": ADMIN_USER_ID, "photo": photo_file_id, "caption": f"📩 Support from <code>{user_id}</code>\n{html_escape(text)}"[:1020], "parse_mode": "HTML", "reply_markup": kb})
            elif doc_file_id:
                await telegram("sendDocument", {"chat_id": ADMIN_USER_ID, "document": doc_file_id, "caption": f"📩 Support file from <code>{user_id}</code>\n{html_escape(text)}"[:1020], "parse_mode": "HTML", "reply_markup": kb})
            else:
                await send_message(ADMIN_USER_ID, f"📩 <b>SUPPORT</b> From <code>{user_id}</code>\n\n{html_escape(text)}", kb)
            await send_message(chat_id, f"✅ Sent to admin ({BRAND_TAG}).", main_menu_keyboard(user_id))
            return

        admin_states = {
            "WAIT_ADMIN_REPLY", "WAIT_CHANNEL_POST", "WAIT_VIP_CHANNEL_POST", "WAIT_BTN_MEDIA",
            "WAIT_BTN_ADD_TICKET", "WAIT_BTN_ADD_PRICE", "WAIT_BTN_ADD_URL", "WAIT_BTN_SETPRICE",
            "WAIT_BTN_SETINTLPRICE", "WAIT_BTN_SETDAYS", "WAIT_BTN_RENAME", "WAIT_BTN_SETDESC",
            "WAIT_BROADCAST", "WAIT_PLAN_PRICE", "WAIT_INTL_PLAN_PRICE", "WAIT_ADD_INTL_PLAN",
            "WAIT_INTL_NOTE", "WAIT_CRY_WALLET", "WAIT_CRY_CUSTOM_WALLET",
        }
        if state in admin_states:
            if not is_admin_private_chat(user_id, chat_id, chat_type):
                clear_session(user_id)
                await report_unauthorized_admin_attempt(user_id, f"SESSION:{state}")
                return
            if state == "WAIT_CRY_WALLET":
                clear_session(user_id)
                save_cryptapi_wallet(payload, "" if text.lower() in ("clear", "none") else text.strip())
                await show_admin_cryptapi_panel(chat_id)
            elif state == "WAIT_CRY_CUSTOM_WALLET":
                parts = [p.strip() for p in text.split("|", 1)]
                if len(parts) != 2:
                    await send_message(chat_id, "Format: <code>ticker | address</code>")
                    return
                clear_session(user_id)
                save_cryptapi_wallet(parts[0].lower(), "" if parts[1].lower() in ("clear", "none") else parts[1])
                await show_admin_cryptapi_panel(chat_id)
            elif state == "WAIT_INTL_PLAN_PRICE":
                try:
                    usd_val = float(text.replace("$", "").strip())
                    assert usd_val > 0
                except Exception:
                    await send_message(chat_id, "⚠️ Enter a valid USD price (e.g. <code>25</code> or <code>19.99</code>).")
                    return
                clear_session(user_id)
                with connect_db() as db:
                    db.execute("UPDATE intl_plans SET price_usd=? WHERE name=?", (round(usd_val, 2), payload))
                await show_admin_intl_packages(chat_id)
            elif state == "WAIT_ADD_INTL_PLAN":
                parts = [p.strip() for p in text.split("|")]
                if len(parts) < 3:
                    await send_message(chat_id, "Format: <code>Title | USD Price | Days | Benefits</code>\nExample: <code>Weekend Global VIP | 12 | 3 | Weekend VIP picks</code>")
                    return
                try:
                    usd_val = float(parts[1].replace("$", "").strip())
                    days_val = int(parts[2])
                except Exception:
                    await send_message(chat_id, "⚠️ Price must be a number and Days an integer (use 0 for lifetime).")
                    return
                clear_session(user_id)
                slug = "intl_" + re.sub(r"[^a-z0-9]+", "_", parts[0].lower()).strip("_")[:24] + "_" + secrets.token_hex(2)
                is_lt = 1 if days_val <= 0 else 0
                eff_days = 36500 if is_lt else days_val
                benefits = parts[3] if len(parts) >= 4 else f"{eff_days} Days Global VIP Access"
                with connect_db() as db:
                    db.execute("INSERT INTO intl_plans(name,title,price_usd,days,benefits,is_lifetime,is_active) VALUES(?,?,?,?,?,?,1)", (slug, parts[0][:64], round(usd_val, 2), eff_days, benefits[:200], is_lt))
                await show_admin_intl_packages(chat_id)
            elif state == "WAIT_INTL_NOTE":
                clear_session(user_id)
                set_setting("intl_payment_note", text[:400])
                await show_admin_intl_packages(chat_id)
            elif state == "WAIT_BTN_SETINTLPRICE":
                try:
                    usd_val = float(text.replace("$", "").strip())
                    assert usd_val >= 0
                except Exception:
                    await send_message(chat_id, "⚠️ Enter a valid USD price (e.g. <code>10</code>, <code>15.50</code>, or <code>0</code>).")
                    return
                bid = int(payload)
                clear_session(user_id)
                with connect_db() as db:
                    db.execute("UPDATE custom_buttons SET intl_price_usd=? WHERE id=?", (round(usd_val, 2), bid))
                await show_admin_button_detail(chat_id, bid)
            elif state == "WAIT_ADMIN_REPLY":
                clear_session(user_id)
                await send_message(int(payload), f"💬 <b>ADMIN ({BRAND_TAG})</b>\n\n{html_escape(text)}")
                await send_message(chat_id, "✅ Sent.", admin_keyboard(user_id))
            elif state == "WAIT_CHANNEL_POST":
                clear_session(user_id)
                picker = json.loads(payload)
                await publish_custom_main_post(text, photo_file_id=photo_file_id, selected_btn_ids=picker.get("btn_ids", []), include_footer=picker.get("footer", True), include_reactions=picker.get("react", True))
                await send_message(chat_id, "✅ Published to Main Channel!", admin_keyboard(user_id))
            elif state == "WAIT_VIP_CHANNEL_POST":
                if not text and not photo_file_id:
                    await send_message(chat_id, "⚠️ Send text or photo.")
                    return
                await publish_vip_channel_post(text, photo_file_id=photo_file_id)
                await send_message(chat_id, "✅ Published to VIP Channel!", {"inline_keyboard": [[{"text": "✅ Done", "callback_data": "admin:home", "style": "success"}]]})
            elif state == "WAIT_BTN_MEDIA":
                bid = int(payload)
                fid, mtype = (photo_file_id, "photo") if photo_file_id else ((doc_file_id, "document") if doc_file_id else (None, None))
                if not fid:
                    await send_message(chat_id, "⚠️ Send a photo/file or /done.")
                    return
                with connect_db() as db:
                    db.execute("INSERT INTO ticket_media(offer_key,file_id,media_type,caption,created_at) VALUES(?,?,?,?,?)", (f"btn:{bid}", fid, mtype, text[:400], utcnow().isoformat()))
                await send_message(chat_id, f"✅ Saved image #{count_offer_media(f'btn:{bid}')}!", {"inline_keyboard": [[{"text": "✅ Done Uploading", "callback_data": f"admin:btn_media:{bid}", "style": "success"}]]})
            elif state == "WAIT_BTN_ADD_TICKET":
                parts = [p.strip() for p in text.split("|")]
                if len(parts) >= 2 and parts[1].isdigit():
                    price = int(parts[1])
                    iprice = float(parts[2]) if len(parts) >= 3 else 0.0
                    days = int(parts[3]) if (len(parts) >= 4 and parts[3].isdigit()) else 1
                    clear_session(user_id)
                    with connect_db() as db:
                        nid = insert_returning_id(db, "INSERT INTO custom_buttons(label,url,button_type,price,intl_price_usd,days,description,style,is_active) VALUES(?,?,?,?,?,?,?,?,1)", (parts[0][:64], "", "TICKET", price, iprice, days, "", "primary"))
                    await show_admin_button_detail(chat_id, nid)
                elif text:
                    set_session(user_id, "WAIT_BTN_ADD_PRICE", text[:64])
                    await send_message(chat_id, f"💰 Enter <b>Local KES price</b> for <b>{html_escape(text[:64])}</b>:")
            elif state == "WAIT_BTN_ADD_PRICE":
                if not text.isdigit():
                    await send_message(chat_id, "⚠️ Enter whole number KES.")
                    return
                clear_session(user_id)
                with connect_db() as db:
                    nid = insert_returning_id(db, "INSERT INTO custom_buttons(label,url,button_type,price,intl_price_usd,days,description,style,is_active) VALUES(?,?,?,?,?,?,?,?,1)", (payload[:64], "", "TICKET", int(text), 0.0, 1, "", "primary"))
                await show_admin_button_detail(chat_id, nid)
            elif state == "WAIT_BTN_ADD_URL":
                parts = [p.strip() for p in text.split("|", 1)]
                if len(parts) != 2 or not parts[1].startswith(("http://", "https://")):
                    await send_message(chat_id, "Format: <code>Label | https://...</code>")
                    return
                clear_session(user_id)
                with connect_db() as db:
                    nid = insert_returning_id(db, "INSERT INTO custom_buttons(label,url,button_type,price,intl_price_usd,days,description,style,is_active) VALUES(?,?,?,?,?,?,?,?,1)", (parts[0][:64], parts[1][:300], "URL", 0, 0.0, 0, "", "primary"))
                await show_admin_button_detail(chat_id, nid)
            elif state == "WAIT_BTN_SETPRICE" and text.isdigit():
                bid = int(payload)
                clear_session(user_id)
                with connect_db() as db:
                    db.execute("UPDATE custom_buttons SET price=? WHERE id=?", (int(text), bid))
                await show_admin_button_detail(chat_id, bid)
            elif state == "WAIT_BTN_SETDAYS" and text.isdigit():
                bid = int(payload)
                clear_session(user_id)
                with connect_db() as db:
                    db.execute("UPDATE custom_buttons SET days=? WHERE id=?", (int(text), bid))
                await show_admin_button_detail(chat_id, bid)
            elif state == "WAIT_BTN_RENAME" and text:
                bid = int(payload)
                clear_session(user_id)
                with connect_db() as db:
                    db.execute("UPDATE custom_buttons SET label=? WHERE id=?", (text[:64], bid))
                await show_admin_button_detail(chat_id, bid)
            elif state == "WAIT_BTN_SETDESC":
                bid = int(payload)
                clear_session(user_id)
                with connect_db() as db:
                    db.execute("UPDATE custom_buttons SET description=? WHERE id=?", (text[:300], bid))
                await show_admin_button_detail(chat_id, bid)
            elif state == "WAIT_BROADCAST":
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
            elif state == "WAIT_PLAN_PRICE" and text.isdigit():
                clear_session(user_id)
                with connect_db() as db:
                    db.execute("UPDATE plans SET price=? WHERE name=?", (int(text), payload))
                await show_admin_packages(chat_id)
            return

    await show_main_menu(chat_id, user_id)


async def handle_callback(query):
    cid = query["id"]
    user = query["from"]
    if not user or user.get("is_bot"):
        asyncio.create_task(answer_callback(cid))
        return
    user_id = user["id"]
    data = query.get("data", "")
    msg_obj = query.get("message") or {}
    chat_obj = msg_obj.get("chat") or {}
    chat_id, chat_type = chat_obj.get("id") or user_id, chat_obj.get("type", "private")
    message_id = msg_obj.get("message_id")

    if data.startswith("react:"):
        idx = int(data.split(":", 1)[1])
        if 0 <= idx < len(REACTION_EMOJIS) and message_id:
            emoji = REACTION_EMOJIS[idx]
            with connect_db() as db:
                exists = db.execute("SELECT 1 FROM post_reactions WHERE channel_msg_id=? AND user_id=? AND emoji=?", (message_id, user_id, emoji)).fetchone()
                if exists:
                    db.execute("DELETE FROM post_reactions WHERE channel_msg_id=? AND user_id=? AND emoji=?", (message_id, user_id, emoji))
                    fb = f"Removed {emoji}"
                else:
                    db.execute("INSERT INTO post_reactions(channel_msg_id,user_id,emoji) VALUES(?,?,?)", (message_id, user_id, emoji))
                    fb = f"Reacted {emoji}"
            asyncio.create_task(answer_callback(cid, fb))
            try:
                await ensure_bot_username()
                await telegram("editMessageReplyMarkup", {"chat_id": msg_obj["chat"]["id"], "message_id": message_id, "reply_markup": channel_post_keyboard(channel_msg_id=message_id)})
            except Exception:
                pass
        else:
            asyncio.create_task(answer_callback(cid))
        return

    # Acknowledge spinner immediately for instant feel
    asyncio.create_task(answer_callback(cid))
    register_user(user)
    db_user = get_user(user_id)
    if db_user and db_user["is_blocked"] and not is_admin(user_id):
        return

    if data == "menu:home":
        clear_session(user_id)
        await show_main_menu(chat_id, user_id)
    elif data == "menu:today":
        with connect_db() as db:
            rows = db.execute("SELECT * FROM matches WHERE tier='FREE' AND publish_status='PUBLISHED' ORDER BY id DESC LIMIT 10").fetchall()
        txt = "\n".join(format_match_block(m, i + 1) for i, m in enumerate(rows)) if rows else "<i>No free predictions yet.</i>"
        await send_message(chat_id, f"⚽ <b>TODAY'S PREDICTIONS</b>\n━━━━━━━━━━━━━━━━━━━━\n\n{txt}", {"inline_keyboard": [[{"text": "🏠 Main Menu", "callback_data": "menu:home", "style": "primary"}]]})
    elif data == "menu:ai_analysis":
        set_session(user_id, "WAIT_MANUAL_ANALYSIS")
        await send_message(chat_id, "🤖 Send a fixture name (e.g. <code>Arsenal vs Chelsea</code>) or /cancel:")
    elif data == "menu:accum":
        await show_accumulator_builder(chat_id, user_id)
    elif data.startswith("accum:"):
        parts = data.split(":", 2)
        sub = parts[1]
        sess = get_session(user_id)
        draft = json.loads(sess["payload"]) if (sess and sess["state"] == "ACCUM_BUILD") else {"category": "3-5 Odds", "match_ids": []}
        if sub == "cat" and len(parts) == 3:
            draft["category"] = parts[2]
            set_session(user_id, "ACCUM_BUILD", json.dumps(draft))
            await show_accumulator_builder(chat_id, user_id)
        elif sub == "toggle" and len(parts) == 3:
            mid = int(parts[2])
            if mid in draft["match_ids"]:
                draft["match_ids"].remove(mid)
            else:
                draft["match_ids"].append(mid)
            set_session(user_id, "ACCUM_BUILD", json.dumps(draft))
            await show_accumulator_builder(chat_id, user_id)
        elif sub == "clear":
            draft["match_ids"] = []
            set_session(user_id, "ACCUM_BUILD", json.dumps(draft))
            await show_accumulator_builder(chat_id, user_id)
        elif sub == "save" and draft["match_ids"]:
            with connect_db() as db:
                ph = ",".join(["?"] * len(draft["match_ids"]))
                rows = db.execute(f"SELECT * FROM matches WHERE id IN ({ph})", tuple(draft["match_ids"])).fetchall()
                total_odds, sels = 1.0, []
                for r in rows:
                    sels.append({"id": r["id"], "match": r["match_name"], "tip": r["tip"], "odds": r["odds"]})
                    try:
                        total_odds *= float(r["odds"])
                    except ValueError:
                        pass
                db.execute("INSERT INTO accumulators(category,selections_json,total_odds,risk_note,status,created_by,created_at) VALUES(?,?,?,?,?,?,?)", (draft["category"], json.dumps(sels), f"{total_odds:.2f}", "Accumulators are not guaranteed.", "PENDING_ADMIN", user_id, utcnow().isoformat()))
            clear_session(user_id)
            await send_message(chat_id, f"🎟️ <b>Accumulator Submitted!</b> ({total_odds:.2f}x)", main_menu_keyboard(user_id))
    elif data == "menu:stats":
        await show_stats_and_results(chat_id, user_id, only_settled=False)
    elif data == "menu:results":
        await show_stats_and_results(chat_id, user_id, only_settled=True)
    elif data == "menu:vip":
        await show_vip_screen(chat_id, user_id)
    elif data == "menu:vip_intl":
        await show_vip_screen(chat_id, user_id, force_scope="INTL")
    elif data == "menu:vip_local":
        await show_vip_screen(chat_id, user_id, force_scope="LOCAL")
    elif data == "menu:subscription":
        await ensure_bot_username()
        prefs = get_user_prefs(user_id)
        with connect_db() as db:
            sub = db.execute("SELECT * FROM subscriptions WHERE user_id=?", (user_id,)).fetchone()
            orders = db.execute("SELECT * FROM orders WHERE user_id=? ORDER BY created_at DESC LIMIT 6", (user_id,)).fetchall()
            ref_cnt = db.execute("SELECT COUNT(*) AS n FROM users WHERE referred_by=? AND referral_rewarded=1", (user_id,)).fetchone()["n"]
        sub_txt = f"✅ Active ({sub['plan']}) — Expires: {'Lifetime' if sub['is_lifetime'] else format_local_time(sub['expires_at'], prefs['tz'])}" if (sub and sub["status"] == "ACTIVE") else "❌ Inactive"
        ord_txt = "\n".join(f"• {html_escape(format_order_item_name(o['plan']))} — {o['local_amount'] or o['amount']} [{o['status']}]" for o in orders) or "No orders yet."
        ref_link = f"https://t.me/{bot_username}?start=ref_{user_id}" if bot_username else ""
        await send_message(chat_id, f"💳 <b>MY SUBSCRIPTION</b>\nStatus: <b>{sub_txt}</b>\n\n<b>Recent Orders:</b>\n{ord_txt}" + (f"\n\n🎁 Referral Link (Rewarded: {ref_cnt}): <code>{ref_link}</code>" if ref_link else ""), {"inline_keyboard": [[{"text": "👑 VIP Membership", "callback_data": "menu:vip", "style": "danger"}], [{"text": "🏠 Main Menu", "callback_data": "menu:home"}]]})
    elif data == "menu:country":
        await show_country_welcome(chat_id)
    elif data == "country:detect_ask":
        set_session(user_id, "WAIT_LOCATION_PERM")
        await send_message(chat_id, "📍 Share your approximate location to detect your country:", {"keyboard": [[{"text": "📍 Share My Location", "request_location": True}], [{"text": "/cancel"}]], "resize_keyboard": True, "one_time_keyboard": True})
    elif data == "country:manual":
        await send_message(chat_id, "🗺️ <b>Select your country:</b>", country_manual_keyboard())
    elif data.startswith("country:pick:"):
        code = data.split(":")[2]
        c = COUNTRIES.get(code)
        if c:
            await send_message(chat_id, f"📍 {c['flag']} <b>{c['name']}</b> ({c['currency']})\nConfirm?", {"inline_keyboard": [[{"text": "✅ Yes, Confirm", "callback_data": f"country:confirm:{code}", "style": "success"}], [{"text": "🔄 Change", "callback_data": "country:manual"}]]})
    elif data.startswith("country:confirm:"):
        code = data.split(":")[2]
        c = COUNTRIES.get(code)
        if c:
            with connect_db() as db:
                db.execute("UPDATE users SET country=?, country_confirmed=1, currency=?, timezone_name=? WHERE user_id=?", (code, c["currency"], c["tz"], user_id))
            await show_main_menu(chat_id, user_id)
    elif data == "currency:menu":
        await send_message(chat_id, "💱 <b>Choose display currency:</b>", currency_manual_keyboard())
    elif data.startswith("currency:set:"):
        curr = data.split(":")[2]
        if curr in CURRENCIES:
            with connect_db() as db:
                db.execute("UPDATE users SET currency=?, country_confirmed=1 WHERE user_id=?", (curr, user_id))
            await show_vip_screen(chat_id, user_id)
    elif data == "menu:contact":
        set_session(user_id, "WAIT_SUPPORT_MSG")
        await send_message(chat_id, f"💬 Send your message to admin ({BRAND_TAG}) or /cancel:")
    elif data.startswith("openbtn:") and data.split(":", 1)[1].isdigit():
        await show_ticket_button_checkout(chat_id, user_id, int(data.split(":", 1)[1]), force_scope="LOCAL")
    elif data.startswith("openibtn:") and data.split(":", 1)[1].isdigit():
        await show_ticket_button_checkout(chat_id, user_id, int(data.split(":", 1)[1]), force_scope="INTL")
    elif data.startswith("plan:"):
        plan_name = data.split(":", 1)[1]
        plan = get_plan(plan_name)
        if not plan:
            return
        prefs = get_user_prefs(user_id)
        c = COUNTRIES.get(prefs["country"] if prefs["country"] in LOCAL_COUNTRIES else "KE", COUNTRIES["KE"])
        disp, _ = format_converted_price(plan["price"], prefs["currency"])
        till_no, airtel_no = get_setting("mpesa_till_number", DEFAULT_MPESA_TILL), get_setting("airtel_money_number", DEFAULT_AIRTEL_NUMBER)
        rows = []
        for m in c["methods"]:
            lbl, sty = METHOD_LABELS[m]
            if m == "mpesa_till":
                lbl = f"🏪 Manual M-Pesa Till ({till_no})"
            elif m == "airtel":
                lbl = f"🔴 Airtel Money ({airtel_no})"
            rows.append([{"text": lbl, "callback_data": f"pay:{plan_name}:{m}", "style": sty}])
        rows.append([{"text": "⬅️ Back", "callback_data": "menu:vip_local"}])
        await send_message(chat_id, f"💳 <b>{html_escape(plan['title'])}</b> — {disp}\n\n{c['how']}", {"inline_keyboard": rows})
    elif data.startswith("iplan:"):
        pname = data.split(":", 1)[1]
        ip = get_intl_plan(pname)
        if not ip:
            return
        prefs = get_user_prefs(user_id)
        disp, _ = format_usd_price(ip["price_usd"], prefs["currency"] if prefs["is_intl"] else "USD")
        rows = [
            [{"text": "🪙 Crypto (CryptAPI — 100+ Coins)", "callback_data": f"ipay:{pname}:crypto", "style": "success"}],
            [{"text": "💲 Card / PayPal / Bank Transfer", "callback_data": f"ipay:{pname}:intl_transfer", "style": "primary"}],
            [{"text": "⬅️ Back", "callback_data": "menu:vip_intl"}],
        ]
        await send_message(chat_id, f"🌍 <b>{html_escape(ip['title'])}</b> — <b>{html_escape(disp)}</b>\n\n{html_escape(get_setting('intl_payment_note'))}", {"inline_keyboard": rows})
    elif data.startswith("pay:"):
        _, plan_name, provider = data.split(":", 2)
        if provider == "mpesa":
            set_session(user_id, "WAIT_MPESA_PHONE", plan_name)
            await send_message(chat_id, "📱 Enter Kenyan Safaricom number (<code>0712345678</code>) or /cancel:")
        else:
            await create_manual_order_and_prompt(user_id, plan_name, provider)
    elif data.startswith("ipay:"):
        _, pname, provider = data.split(":", 2)
        offer_key = f"intl:{pname}"
        if provider == "crypto":
            await show_crypto_checkout_menu(chat_id, user_id, offer_key, view="home")
        else:
            await create_manual_order_and_prompt(user_id, offer_key, provider)
    elif data.startswith("buybtn:"):
        _, raw_id, method = data.split(":", 2)
        if raw_id.isdigit():
            offer_key = f"btn:{raw_id}"
            if method == "mpesa":
                set_session(user_id, "WAIT_MPESA_PHONE", offer_key)
                await send_message(chat_id, "📲 Enter Kenyan Safaricom number (<code>0712345678</code>) or /cancel:")
            else:
                await create_manual_order_and_prompt(user_id, offer_key, method)
    elif data.startswith("buyibtn:"):
        _, raw_id, method = data.split(":", 2)
        if raw_id.isdigit():
            offer_key = f"ibtn:{raw_id}"
            if method == "crypto":
                await show_crypto_checkout_menu(chat_id, user_id, offer_key, view="home")
            else:
                await create_manual_order_and_prompt(user_id, offer_key, method)
    elif data.startswith("cryptolist|"):
        parts = data.split("|")
        if len(parts) == 4:
            await show_crypto_checkout_menu(chat_id, user_id, parts[1], view=parts[2], page=int(parts[3]) if parts[3].isdigit() else 0, message_id=message_id)
    elif data.startswith("cryptopay|"):
        parts = data.split("|", 2)
        if len(parts) == 3:
            await start_cryptapi_payment(user_id, chat_id, parts[1], parts[2])
    elif data.startswith("cryptocheck:"):
        ext_ref = data.split(":", 1)[1]
        with connect_db() as db:
            order = db.execute("SELECT * FROM orders WHERE external_ref=? AND user_id=?", (ext_ref, user_id)).fetchone()
        if order and order["status"] != "PAID":
            try:
                res = await verify_cryptapi_order_via_logs(order)
                if res == "NO_TX":
                    await send_message(chat_id, f"⏳ No transaction detected yet for <code>{ext_ref}</code>.")
            except Exception:
                await send_message(chat_id, "⚠️ Could not reach CryptAPI right now.")
    elif data.startswith("manualtill:"):
        ext_ref = data.split(":", 1)[1]
        with connect_db() as db:
            order = db.execute("SELECT * FROM orders WHERE external_ref=? AND user_id=?", (ext_ref, user_id)).fetchone()
        if order:
            await send_manual_till_fallback(user_id, ext_ref, format_order_item_name(order["plan"]), order["amount"])
    elif data.startswith("proof:"):
        ext_ref = data.split(":", 1)[1]
        set_session(user_id, "WAIT_PAYMENT_PROOF", ext_ref)
        await send_message(chat_id, f"📸 Send your payment screenshot/PDF for Ref <code>{ext_ref}</code> (or /cancel):")
    elif data.startswith("verify:"):
        ext_ref = data.split(":", 1)[1]
        with connect_db() as db:
            order = db.execute("SELECT * FROM orders WHERE external_ref=? AND user_id=?", (ext_ref, user_id)).fetchone()
        if order and order["payhero_reference"]:
            try:
                res = await finalize_paid_order(order, verified=await verify_payhero_transaction(order["payhero_reference"]))
                if res == "PENDING":
                    await send_message(chat_id, f"⏳ Still pending. Pay via Till <code>{get_setting('mpesa_till_number', DEFAULT_MPESA_TILL)}</code> if prompt didn't appear.")
            except Exception:
                await send_message(chat_id, "⚠️ PayHero status unavailable right now.")
    elif data.startswith("postpick:"):
        if not is_admin_private_chat(user_id, chat_id, chat_type):
            await report_unauthorized_admin_attempt(user_id, f"CB:{data}")
            return
        parts = data.split(":")
        sub = parts[1]
        sess = get_session(user_id)
        picker = json.loads(sess["payload"]) if (sess and sess["state"] == "POST_PICKER") else {"mode": "custom", "btn_ids": [], "footer": True, "react": True}
        if sub == "toggle" and len(parts) == 3 and parts[2].isdigit():
            bid = int(parts[2])
            if bid in picker["btn_ids"]:
                picker["btn_ids"].remove(bid)
            else:
                picker["btn_ids"].append(bid)
            await show_post_button_picker(chat_id, user_id, picker, message_id=message_id)
        elif sub == "all":
            picker["btn_ids"] = [int(b["id"]) for b in get_all_custom_buttons(only_active=True)]
            await show_post_button_picker(chat_id, user_id, picker, message_id=message_id)
        elif sub == "none":
            picker["btn_ids"] = []
            await show_post_button_picker(chat_id, user_id, picker, message_id=message_id)
        elif sub == "footer":
            picker["footer"] = not picker.get("footer", True)
            await show_post_button_picker(chat_id, user_id, picker, message_id=message_id)
        elif sub == "react":
            picker["react"] = not picker.get("react", True)
            await show_post_button_picker(chat_id, user_id, picker, message_id=message_id)
        elif sub == "next":
            mode = picker.get("mode", "custom")
            if mode == "custom":
                set_session(user_id, "WAIT_CHANNEL_POST", json.dumps(picker))
                await send_message(chat_id, "📝 Send text or photo+caption for Main Channel (or /cancel):")
            elif mode == "vip_promo":
                clear_session(user_id)
                await publish_vip_post(selected_btn_ids=picker.get("btn_ids", []), include_footer=picker.get("footer", True), include_reactions=picker.get("react", True))
                await send_message(chat_id, "✅ VIP Promo published!", admin_keyboard(user_id))
            elif mode == "free_matches":
                clear_session(user_id)
                res = await publish_match_batch("FREE", selected_btn_ids=picker.get("btn_ids", []), include_footer=picker.get("footer", True), include_reactions=picker.get("react", True))
                await send_message(chat_id, "✅ Published FREE matches!" if res else "ℹ️ No queued FREE matches.", admin_keyboard(user_id))
    elif data.startswith("admin:"):
        if not is_admin_private_chat(user_id, chat_id, chat_type):
            await report_unauthorized_admin_attempt(user_id, f"CB:{data}")
            return
        parts = data.split(":")
        action = parts[1]
        if action == "home":
            clear_session(user_id)
            await send_message(chat_id, f"⚡ <b>{PROJECT_NAME} ADMIN</b>", admin_keyboard(user_id))
        elif action == "stats":
            await show_admin_stats(chat_id, user_id)
        elif action == "matches":
            await show_admin_matches(chat_id, user_id)
        elif action == "pub_free":
            await show_post_button_picker(chat_id, user_id, {"mode": "free_matches", "btn_ids": [], "footer": True, "react": True})
        elif action == "pub_vip":
            res = await publish_match_batch("VIP", selected_btn_ids=[], include_footer=True, include_reactions=False)
            await send_message(chat_id, "✅ Published VIP matches!" if res else "ℹ️ No queued VIP matches.", admin_keyboard(user_id))
        elif action == "res" and len(parts) == 4:
            with connect_db() as db:
                db.execute("UPDATE matches SET result=? WHERE id=?", (parts[3], int(parts[2])))
            await sync_channel_message_for_match(int(parts[2]))
            await show_admin_matches(chat_id, user_id)
        elif action == "accums":
            await show_admin_accumulators(chat_id, user_id, message_id=message_id)
        elif action == "accum_pub" and len(parts) == 4 and parts[2].isdigit():
            await publish_accumulator_to_channel(int(parts[2]), target=parts[3].upper())
            await show_admin_accumulators(chat_id, user_id, message_id=message_id)
        elif action == "accum_rej" and len(parts) == 3 and parts[2].isdigit():
            with connect_db() as db:
                db.execute("UPDATE accumulators SET status='REJECTED' WHERE id=?", (int(parts[2]),))
            await show_admin_accumulators(chat_id, user_id, message_id=message_id)
        elif action == "transactions":
            await show_admin_transactions(chat_id, user_id, message_id=message_id)
        elif action == "viewproof" and len(parts) == 3:
            with connect_db() as db:
                o = db.execute("SELECT * FROM orders WHERE external_ref=?", (parts[2],)).fetchone()
            if o and o["proof_file_id"]:
                kb = {"inline_keyboard": [[{"text": "✅ Approve", "callback_data": f"admin:approve:{parts[2]}", "style": "success"}, {"text": "❌ Reject", "callback_data": f"admin:reject:{parts[2]}", "style": "danger"}]]}
                await telegram("sendDocument" if o["proof_type"] == "document" else "sendPhoto", {"chat_id": chat_id, ("document" if o["proof_type"] == "document" else "photo"): o["proof_file_id"], "caption": f"Proof {parts[2]}", "reply_markup": kb})
        elif action == "approve" and len(parts) == 3:
            with connect_db() as db:
                order = db.execute("SELECT * FROM orders WHERE external_ref=?", (parts[2],)).fetchone()
            if order:
                res = await finalize_paid_order(order, manual_admin=True)
                await send_message(chat_id, f"✅ Approved <code>{parts[2]}</code> ({res}).", {"inline_keyboard": [[{"text": "🧾 Transactions", "callback_data": "admin:transactions"}]]})
        elif action == "reject" and len(parts) == 3:
            with connect_db() as db:
                order = db.execute("SELECT * FROM orders WHERE external_ref=?", (parts[2],)).fetchone()
                db.execute("UPDATE orders SET status='FAILED' WHERE external_ref=?", (parts[2],))
            if order:
                asyncio.create_task(send_message(int(order["user_id"]), f"❌ Payment <code>{parts[2]}</code> rejected. Contact {BRAND_TAG}."))
            await send_message(chat_id, f"🚫 Rejected <code>{parts[2]}</code>.")
        elif action == "users":
            await show_admin_users(chat_id, user_id)
        elif action == "buttons":
            await show_admin_buttons(chat_id, message_id=message_id)
        elif action == "btn_view" and len(parts) == 3 and parts[2].isdigit():
            await show_admin_button_detail(chat_id, int(parts[2]), message_id=message_id)
        elif action == "btn_media" and len(parts) == 3 and parts[2].isdigit():
            clear_session(user_id)
            await show_admin_button_media(chat_id, int(parts[2]), message_id=message_id)
        elif action == "btn_media_add" and len(parts) == 3 and parts[2].isdigit():
            set_session(user_id, "WAIT_BTN_MEDIA", parts[2])
            await send_message(chat_id, "🖼️ Send ticket photo(s) now, then /done:")
        elif action == "btn_media_prev" and len(parts) == 3 and parts[2].isdigit():
            b = get_custom_button(int(parts[2]))
            if b:
                await send_offer_ticket_media(chat_id, f"btn:{b['id']}", b["label"])
        elif action == "btn_media_clr" and len(parts) == 3 and parts[2].isdigit():
            with connect_db() as db:
                db.execute("DELETE FROM ticket_media WHERE offer_key=?", (f"btn:{parts[2]}",))
            await show_admin_button_media(chat_id, int(parts[2]), message_id=message_id)
        elif action == "btn_add_ticket":
            set_session(user_id, "WAIT_BTN_ADD_TICKET")
            await send_message(chat_id, "➕ Send: <code>Label | Local_KES | Intl_USD | Days</code>\nExample: <code>💎 Diamond | 1500 | 20 | 0</code>")
        elif action == "btn_add_url":
            set_session(user_id, "WAIT_BTN_ADD_URL")
            await send_message(chat_id, "🔗 Send: <code>Label | https://...</code>")
        elif action == "btn_setprice" and len(parts) == 3:
            set_session(user_id, "WAIT_BTN_SETPRICE", parts[2])
            await send_message(chat_id, "🇰🇪 Enter <b>Local KES price</b>:")
        elif action == "btn_setintlprice" and len(parts) == 3:
            set_session(user_id, "WAIT_BTN_SETINTLPRICE", parts[2])
            await send_message(chat_id, "🌍 Enter <b>International USD price</b> (e.g. <code>15</code> or <code>19.99</code>, or <code>0</code>):")
        elif action == "btn_setdays" and len(parts) == 3:
            set_session(user_id, "WAIT_BTN_SETDAYS", parts[2])
            await send_message(chat_id, "📅 Enter VIP days (0 = ticket image only):")
        elif action == "btn_rename" and len(parts) == 3:
            set_session(user_id, "WAIT_BTN_RENAME", parts[2])
            await send_message(chat_id, "✏️ Send new label:")
        elif action == "btn_setdesc" and len(parts) == 3:
            set_session(user_id, "WAIT_BTN_SETDESC", parts[2])
            await send_message(chat_id, "📝 Send description:")
        elif action == "btn_style" and len(parts) == 3 and parts[2].isdigit():
            b = get_custom_button(int(parts[2]))
            if b:
                cur = b["style"] if b["style"] in BUTTON_STYLES else "primary"
                nxt = BUTTON_STYLES[(BUTTON_STYLES.index(cur) + 1) % len(BUTTON_STYLES)]
                with connect_db() as db:
                    db.execute("UPDATE custom_buttons SET style=? WHERE id=?", (nxt, int(parts[2])))
                await show_admin_button_detail(chat_id, int(parts[2]), message_id=message_id)
        elif action == "btn_del" and len(parts) == 3:
            await edit_or_send(chat_id, message_id, "🗑️ Confirm remove button?", {"inline_keyboard": [[{"text": "✅ Yes", "callback_data": f"admin:btn_del_yes:{parts[2]}", "style": "danger"}, {"text": "❌ Cancel", "callback_data": f"admin:btn_view:{parts[2]}"}]]})
        elif action == "btn_del_yes" and len(parts) == 3:
            with connect_db() as db:
                db.execute("DELETE FROM custom_buttons WHERE id=?", (int(parts[2]),))
            await show_admin_buttons(chat_id, message_id=message_id)
        elif action == "post_channel":
            await show_post_button_picker(chat_id, user_id, {"mode": "custom", "btn_ids": [], "footer": True, "react": True})
        elif action == "post_vip_channel":
            set_session(user_id, "WAIT_VIP_CHANNEL_POST")
            await send_message(chat_id, "💎 Send VIP post text or photo+caption (or /cancel):")
        elif action == "post":
            await show_post_button_picker(chat_id, user_id, {"mode": "vip_promo", "btn_ids": [], "footer": True, "react": True})
        elif action == "broadcast":
            set_session(user_id, "WAIT_BROADCAST")
            await send_message(chat_id, "📣 Send broadcast text or photo:")
        elif action == "reply" and len(parts) == 3:
            set_session(user_id, "WAIT_ADMIN_REPLY", parts[2])
            await send_message(chat_id, f"💬 Reply to <code>{parts[2]}</code>:")
        elif action == "packages":
            await show_admin_packages(chat_id, message_id=message_id)
        elif action == "editprice" and len(parts) == 3:
            set_session(user_id, "WAIT_PLAN_PRICE", parts[2])
            await send_message(chat_id, f"🇰🇪 Enter new KES price for <b>{parts[2]}</b>:")
        elif action == "intl_packages":
            await show_admin_intl_packages(chat_id, message_id=message_id)
        elif action == "edit_intl_price" and len(parts) == 3:
            set_session(user_id, "WAIT_INTL_PLAN_PRICE", parts[2])
            await send_message(chat_id, f"💵 Enter new <b>USD price</b> for <b>{html_escape(parts[2])}</b> (e.g. <code>25</code> or <code>19.99</code>):")
        elif action == "add_intl_plan":
            set_session(user_id, "WAIT_ADD_INTL_PLAN")
            await send_message(chat_id, "➕ Send International Plan:\n<code>Title | USD Price | Days | Benefits</code>\nExample: <code>Weekend Global VIP | 12 | 3 | Weekend VIP Picks</code>\n<i>(Use 0 days for Lifetime)</i>")
        elif action == "del_intl_plan" and len(parts) == 3:
            with connect_db() as db:
                db.execute("DELETE FROM intl_plans WHERE name=?", (parts[2],))
            await show_admin_intl_packages(chat_id, message_id=message_id)
        elif action == "edit_intl_note":
            set_session(user_id, "WAIT_INTL_NOTE")
            await send_message(chat_id, "📝 Send international transfer instructions (shown on Card/PayPal/Bank checkout):")
        elif action == "channels":
            await show_admin_settings(chat_id, message_id=message_id)
        elif action == "payhero_diag":
            await show_admin_payhero_diag(chat_id, message_id=message_id)
        elif action == "ph_use_ch" and len(parts) == 3 and parts[2].isdigit():
            set_setting("payhero_channel_id", parts[2])
            await show_admin_payhero_diag(chat_id, message_id=message_id)
        elif action == "cryptapi":
            await show_admin_cryptapi_panel(chat_id, message_id=message_id)
        elif action == "cry_refresh":
            await fetch_cryptapi_catalog(force=True)
            await show_admin_cryptapi_panel(chat_id, message_id=message_id)
        elif action == "cry_set" and len(parts) == 3:
            set_session(user_id, "WAIT_CRY_WALLET", parts[2].lower())
            await send_message(chat_id, f"🪙 Paste <b>{html_escape(parts[2].upper())}</b> wallet address (or <code>clear</code>):")
        elif action == "cry_set_custom":
            set_session(user_id, "WAIT_CRY_CUSTOM_WALLET")
            await send_message(chat_id, "🎯 Send: <code>ticker | wallet_address</code>")
        elif action == "toggle_autoshot":
            set_setting("auto_confirm_screenshot", "0" if get_setting("auto_confirm_screenshot", "0") == "1" else "1")
            await show_admin_settings(chat_id, message_id=message_id)
        elif action == "export":
            await export_csv_backups(chat_id)
        elif action == "audit":
            with connect_db() as db:
                logs = db.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 15").fetchall()
            await send_message(chat_id, "🛡️ <b>AUDIT LOGS</b>\n\n" + ("\n".join(f"• {l['action']}: {html_escape(l['details'])}" for l in logs) or "None."), admin_keyboard(user_id))


# --------------------------------------------------
# ADMIN HELPERS & CSV EXPORT
# --------------------------------------------------

async def show_admin_settings(chat_id, message_id=None):
    if str(chat_id) != str(ADMIN_USER_ID):
        return
    auto_shot = get_setting("auto_confirm_screenshot", "0") == "1"
    txt = (
        "⚙️ <b>CHANNELS & PAYMENT SETTINGS</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        f"📢 Main: <code>{html_escape(get_setting('main_channel_id'))}</code> | 💎 VIP: <code>{html_escape(get_setting('vip_channel_id'))}</code>\n"
        f"📲 PayHero Ch ID: <code>{html_escape(str(get_setting('payhero_channel_id', PAYHERO_CHANNEL_ID)))}</code> | 🏪 Till: <code>{html_escape(get_setting('mpesa_till_number'))}</code>\n"
        f"🔴 Airtel: <code>{html_escape(get_setting('airtel_money_number'))}</code> | 🪙 Crypto Wallets: <b>{len(get_cryptapi_wallets_map())}</b>\n"
        f"📸 Screenshot Auto-Deliver: <b>{'🟢 ON' if auto_shot else '🔒 OFF'}</b>"
    )
    kb = {"inline_keyboard": [
        [{"text": "🩺 Test PayHero Connection & Channels", "callback_data": "admin:payhero_diag", "style": "primary"}],
        [{"text": "🪙 CryptAPI Crypto Wallets (100+ Coins)", "callback_data": "admin:cryptapi", "style": "success"}],
        [{"text": "🌍 International Plans & Prices ($)", "callback_data": "admin:intl_packages", "style": "danger"}],
        [{"text": f"📸 Screenshot Auto-Deliver: {'ON' if auto_shot else 'OFF'}", "callback_data": "admin:toggle_autoshot", "style": "success" if auto_shot else "primary"}],
        [{"text": "⬅️ Back to Admin", "callback_data": "admin:home"}],
    ]}
    await edit_or_send(chat_id, message_id, txt, kb)


async def show_admin_stats(chat_id, user_id):
    if not is_admin(user_id) or str(chat_id) != str(ADMIN_USER_ID):
        return
    with connect_db() as db:
        u_cnt = db.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        v_cnt = db.execute("SELECT COUNT(*) AS n FROM subscriptions WHERE status='ACTIVE'").fetchone()["n"]
        p_cnt = db.execute("SELECT COUNT(*) AS n FROM orders WHERE status='PAID'").fetchone()["n"]
        rev_kes = db.execute("SELECT COALESCE(SUM(amount),0) AS n FROM orders WHERE status='PAID' AND market_scope='LOCAL'").fetchone()["n"]
        rev_usd = db.execute("SELECT COALESCE(SUM(amount_usd),0) AS n FROM orders WHERE status='PAID' AND market_scope='INTL'").fetchone()["n"]
    await send_message(chat_id, f"📊 <b>{PROJECT_NAME} OVERVIEW</b>\n🗄 DB: {'PostgreSQL ✅ (Pooled)' if USE_POSTGRES else 'SQLite'}\n👤 Users: <b>{u_cnt}</b> | 👑 Active VIPs: <b>{v_cnt}</b> | ✅ Paid Orders: <b>{p_cnt}</b>\n🇰🇪 Local Revenue: <b>KES {rev_kes:,}</b>\n🌍 International Revenue: <b>${float(rev_usd):,.2f} USD</b>", admin_keyboard(user_id))


async def show_admin_matches(chat_id, user_id):
    if not is_admin(user_id) or str(chat_id) != str(ADMIN_USER_ID):
        return
    with connect_db() as db:
        qf = db.execute("SELECT COUNT(*) AS n FROM matches WHERE tier='FREE' AND publish_status='QUEUED'").fetchone()["n"]
        qv = db.execute("SELECT COUNT(*) AS n FROM matches WHERE tier='VIP' AND publish_status='QUEUED'").fetchone()["n"]
        recent = db.execute("SELECT * FROM matches ORDER BY id DESC LIMIT 6").fetchall()
    rows = [[{"text": f"📢 Post FREE ({qf})", "callback_data": "admin:pub_free", "style": "success"}, {"text": f"💎 Post VIP ({qv})", "callback_data": "admin:pub_vip", "style": "danger"}]]
    txt = "⚽ <b>FIXTURES</b> (<code>/addmatch FREE | A vs B | 1X2 | Home | 1.85 | 2026-11-16 | 19:45 UTC</code>)\n\n"
    for m in recent:
        txt += f"• #{m['id']} [{m['tier']}] {html_escape(m['match_name'])} — {m['result']}\n"
        rows.append([{"text": f"#{m['id']} ✅Won", "callback_data": f"admin:res:{m['id']}:WON", "style": "success"}, {"text": "❌Lost", "callback_data": f"admin:res:{m['id']}:LOST", "style": "danger"}, {"text": "⚪Void", "callback_data": f"admin:res:{m['id']}:VOID", "style": "primary"}])
    rows.append([{"text": "⬅️ Admin", "callback_data": "admin:home"}])
    await send_message(chat_id, txt, {"inline_keyboard": rows})


async def show_admin_accumulators(chat_id, user_id, message_id=None):
    if not is_admin(user_id) or str(chat_id) != str(ADMIN_USER_ID):
        return
    with connect_db() as db:
        accums = db.execute("SELECT * FROM accumulators ORDER BY id DESC LIMIT 6").fetchall()
    lines, rows = [], []
    for a in accums:
        lines.append(f"• <b>#{a['id']} [{html_escape(a['category'])}] {html_escape(str(a['total_odds']))}x</b> — <code>{html_escape(a['status'])}</code>")
        r = [{"text": f"📢 Main #{a['id']}", "callback_data": f"admin:accum_pub:{a['id']}:MAIN", "style": "success"}, {"text": f"💎 VIP #{a['id']}", "callback_data": f"admin:accum_pub:{a['id']}:VIP", "style": "primary"}]
        if a["status"] == "PENDING_ADMIN":
            r.append({"text": "❌", "callback_data": f"admin:accum_rej:{a['id']}", "style": "danger"})
        rows.append(r)
    rows.append([{"text": "⬅️ Admin", "callback_data": "admin:home"}])
    await edit_or_send(chat_id, message_id, "🎯 <b>ACCUMULATOR TICKETS</b>\n\n" + ("\n".join(lines) if lines else "None."), {"inline_keyboard": rows})


async def show_admin_transactions(chat_id, user_id, message_id=None):
    if not is_admin(user_id) or str(chat_id) != str(ADMIN_USER_ID):
        return
    with connect_db() as db:
        pending = db.execute("SELECT * FROM orders WHERE status IN ('PENDING_REVIEW','PENDING_PROOF') ORDER BY created_at DESC LIMIT 6").fetchall()
        orders = db.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT 10").fetchall()
    rows, p_lines = [], []
    for o in pending:
        p_lines.append(f"• [{o['market_scope']}] <code>{o['external_ref']}</code> 👤<code>{o['user_id']}</code> | {html_escape(format_order_item_name(o['plan']))} ({html_escape(o['local_amount'] or str(o['amount']))})")
        r = [{"text": f"✅ {o['external_ref']}", "callback_data": f"admin:approve:{o['external_ref']}", "style": "success"}, {"text": "❌", "callback_data": f"admin:reject:{o['external_ref']}", "style": "danger"}]
        if o["proof_file_id"]:
            r.append({"text": "👁️", "callback_data": f"admin:viewproof:{o['external_ref']}", "style": "primary"})
        rows.append(r)
    r_lines = [f"• [{o['market_scope']}] <code>{o['external_ref']}</code> {html_escape(format_order_item_name(o['plan']))} [{o['status']}]" for o in orders]
    rows.append([{"text": "🔄 Refresh", "callback_data": "admin:transactions"}, {"text": "⬅️ Admin", "callback_data": "admin:home"}])
    txt = (("⏳ <b>PENDING REVIEW</b>\n" + "\n".join(p_lines) + "\n\n") if p_lines else "") + "🧾 <b>RECENT ORDERS</b>\n" + ("\n".join(r_lines) if r_lines else "None.")
    await edit_or_send(chat_id, message_id, txt, {"inline_keyboard": rows})


async def show_admin_users(chat_id, user_id):
    if not is_admin(user_id) or str(chat_id) != str(ADMIN_USER_ID):
        return
    with connect_db() as db:
        users = db.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT 10").fetchall()
    await send_message(chat_id, "👥 <b>RECENT USERS</b>\n\n" + "\n".join(f"• <code>{u['user_id']}</code> @{u['username'] or u['first_name']} [{u['country'] or 'Unset'}/{u['currency']}]" for u in users), admin_keyboard(user_id))


async def show_admin_packages(chat_id, message_id=None):
    if str(chat_id) != str(ADMIN_USER_ID):
        return
    with connect_db() as db:
        plans = db.execute("SELECT * FROM plans ORDER BY price").fetchall()
    rows = [[{"text": f"🇰🇪 {p['title']} (KES {p['price']:,})", "callback_data": f"admin:editprice:{p['name']}", "style": "success"}] for p in plans]
    rows.append([{"text": "🌍 Edit International Plans ($)", "callback_data": "admin:intl_packages", "style": "danger"}])
    rows.append([{"text": "⬅️ Admin", "callback_data": "admin:home"}])
    await edit_or_send(chat_id, message_id, "🇰🇪 <b>LOCAL VIP PLANS (KES)</b>", {"inline_keyboard": rows})


async def export_csv_backups(chat_id):
    if str(chat_id) != str(ADMIN_USER_ID):
        return
    with connect_db() as db:
        orders = db.execute("SELECT * FROM orders ORDER BY created_at DESC").fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["external_ref", "scope", "user_id", "plan", "amount_kes", "amount_usd", "currency", "local_amount", "provider", "status", "receipt", "created_at"])
    for o in orders:
        w.writerow([o["external_ref"], o["market_scope"], o["user_id"], format_order_item_name(o["plan"]), o["amount"], o["amount_usd"], o["currency"], o["local_amount"], o["provider"], o["status"], o["mpesa_receipt"], o["created_at"]])
    await telegram_send_document(chat_id, "prime_tips_empire_orders.csv", buf.getvalue().encode("utf-8"), "📦 Orders CSV Backup")


# --------------------------------------------------
# BACKGROUND WORKER & FASTAPI ROUTES
# --------------------------------------------------

async def _process_update_safe(update):
    try:
        if "message" in update:
            await handle_message(update["message"])
        elif "callback_query" in update:
            await handle_callback(update["callback_query"])
    except Exception:
        log.exception("Telegram update processing failed")


async def background_worker():
    global bot_username
    for attempt in range(5):
        try:
            me = await telegram("getMe", {})
            bot_username = me["username"]
            await telegram("setWebhook", {"url": PUBLIC_URL.rstrip("/") + "/telegram", "secret_token": WEBHOOK_SECRET, "allowed_updates": ["message", "callback_query"]})
            log.info("%s started as @%s", PROJECT_NAME, bot_username)
            break
        except Exception:
            await asyncio.sleep(5)

    while True:
        try:
            await asyncio.gather(refresh_exchange_rates(force=False), fetch_cryptapi_catalog(force=False), return_exceptions=True)
            cutoff_iso = (utcnow() - timedelta(minutes=30)).isoformat()
            crypto_cutoff_iso = (utcnow() - timedelta(hours=3)).isoformat()
            with connect_db() as db:
                pending_stk = db.execute("SELECT * FROM orders WHERE status='PENDING' AND provider='mpesa' AND payhero_reference IS NOT NULL AND created_at>=?", (cutoff_iso,)).fetchall()
                pending_cry = db.execute("SELECT * FROM orders WHERE status='PENDING' AND provider='crypto' AND crypto_ticker IS NOT NULL AND created_at>=?", (crypto_cutoff_iso,)).fetchall()
            for o in pending_stk:
                try:
                    await finalize_paid_order(o, verified=await verify_payhero_transaction(o["payhero_reference"]))
                except Exception:
                    pass
            for co in pending_cry:
                try:
                    await verify_cryptapi_order_via_logs(co)
                except Exception:
                    pass
            now_iso, remind_iso = utcnow().isoformat(), (utcnow() + timedelta(hours=12)).isoformat()
            with connect_db() as db:
                reminds = db.execute("SELECT * FROM subscriptions WHERE status='ACTIVE' AND is_lifetime=0 AND reminded=0 AND expires_at>? AND expires_at<=?", (now_iso, remind_iso)).fetchall()
                expired = db.execute("SELECT * FROM subscriptions WHERE status='ACTIVE' AND is_lifetime=0 AND expires_at<=?", (now_iso,)).fetchall()
            for r in reminds:
                with connect_db() as db:
                    db.execute("UPDATE subscriptions SET reminded=1 WHERE user_id=?", (r["user_id"],))
                try:
                    await send_message(r["user_id"], f"⏳ Your {PROJECT_NAME} VIP membership expires soon. Use /start to renew.")
                except Exception:
                    pass
            vip_chat = get_setting("vip_channel_id", VIP_CHANNEL_ID)
            for e in expired:
                try:
                    await telegram("banChatMember", {"chat_id": vip_chat, "user_id": e["user_id"]})
                    await telegram("unbanChatMember", {"chat_id": vip_chat, "user_id": e["user_id"], "only_if_banned": True})
                except Exception:
                    pass
                with connect_db() as db:
                    db.execute("UPDATE subscriptions SET status='EXPIRED' WHERE user_id=?", (e["user_id"],))
        except Exception:
            log.exception("Background worker error")
        await asyncio.sleep(120)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    check_settings()
    init_db()
    _load_fx_into_mem()
    http_client = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0), limits=httpx.Limits(max_connections=60, max_keepalive_connections=20))
    try:
        await ensure_bot_username()
    except Exception:
        pass
    worker = asyncio.create_task(background_worker())
    try:
        yield
    finally:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        if http_client and not http_client.is_closed:
            await http_client.aclose()
        if _pg_pool:
            _pg_pool.closeall()


app = FastAPI(lifespan=lifespan)
cors_origins = [o.strip() for o in CORS_ORIGINS_RAW.split(",") if o.strip()]
if PUBLIC_URL and PUBLIC_URL.rstrip("/") not in cors_origins:
    cors_origins.append(PUBLIC_URL.rstrip("/"))
app.add_middleware(CORSMiddleware, allow_origins=cors_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.get("/")
async def home():
    return {"status": f"{PROJECT_NAME} running", "brand": BRAND_TAG, "storage": "postgresql-pooled" if USE_POSTGRES else "sqlite"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.get("/api/packages")
async def api_packages(scope: str = Query(default="LOCAL"), currency: str = Query(default="KES")):
    curr = currency.upper() if currency.upper() in CURRENCIES else "KES"
    if scope.upper() == "INTL":
        plans = get_all_intl_plans(only_active=True)
        out = [{**dict(p), "display_price": format_usd_price(p["price_usd"], curr if curr != "KES" else "USD")[0]} for p in plans]
        return {"scope": "INTL", "currency": curr if curr != "KES" else "USD", "packages": out}
    with connect_db() as db:
        plans = db.execute("SELECT name,title,price,days,benefits,is_lifetime FROM plans ORDER BY price").fetchall()
    out = [{**dict(p), "display_price": format_converted_price(p["price"], curr)[0]} for p in plans]
    return {"scope": "LOCAL", "currency": curr, "packages": out}


@app.get("/api/predictions")
async def api_predictions():
    with connect_db() as db:
        free = db.execute("SELECT id,match_name,market,tip,odds,match_date,kickoff_time,result FROM matches WHERE tier='FREE' AND publish_status='PUBLISHED' ORDER BY id DESC LIMIT 15").fetchall()
        won = db.execute("SELECT COUNT(*) AS n FROM matches WHERE result='WON'").fetchone()["n"]
        lost = db.execute("SELECT COUNT(*) AS n FROM matches WHERE result='LOST'").fetchone()["n"]
    dec = won + lost
    return {"win_rate": round(won / dec * 100, 1) if dec else None, "free_predictions": [dict(r) for r in free]}


@app.get("/app", response_class=HTMLResponse)
async def empire_portal():
    bot_link = f"https://t.me/{bot_username}?start=vip" if bot_username else "#"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{PROJECT_NAME} • {BRAND_TAG}</title>
<style>
  body{{margin:0;font-family:system-ui,sans-serif;background:radial-gradient(circle at top,#1e1b4b,#06070d 65%);color:#f8fafc;min-height:100vh;padding:16px}}
  .hero{{background:linear-gradient(135deg,rgba(15,23,42,.92),rgba(30,27,75,.9));border:1px solid rgba(245,158,11,.45);border-radius:20px;padding:20px;box-shadow:0 0 28px rgba(124,58,237,.35)}}
  h1{{margin:0 0 4px;font-size:24px;background:linear-gradient(90deg,#f59e0b,#10b981,#3b82f6);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
  .card{{background:#0d142b;border:1px solid #1e293b;border-radius:14px;padding:14px;margin-top:12px}}
  select{{width:100%;margin-top:10px;padding:10px;border-radius:10px;background:#0f172a;color:#f8fafc;border:1px solid #334155}}
  .btn{{display:block;text-align:center;margin-top:14px;padding:13px;border-radius:12px;background:linear-gradient(90deg,#f59e0b,#7c3aed);color:#fff;font-weight:800;text-decoration:none}}
</style>
</head>
<body>
  <div class="hero">
    <h1>👑 {PROJECT_NAME}</h1>
    <div style="color:#94a3b8;font-size:13px">International Football Analytics & VIP Platform • {BRAND_TAG}</div>
    <select id="scopeSel" onchange="loadPortal()">
      <option value="LOCAL">🇰🇪 East Africa Local Plans (KES / Mobile Money)</option>
      <option value="INTL">🌍 International Plans (USD / Crypto)</option>
    </select>
    <select id="currSel" onchange="loadPortal()">
      {''.join(f'<option value="{c}">{c}</option>' for c in CURRENCIES)}
    </select>
  </div>
  <div id="portalBody" class="card">Loading predictions & plans...</div>
  <a class="btn" href="{bot_link}">⚡ Join VIP in Telegram</a>
<script>
const esc=s=>String(s??'').replace(/[&<>"']/g,m=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[m]));
function loadPortal(){{
  const scope=document.getElementById('scopeSel').value;
  const curr=document.getElementById('currSel').value;
  Promise.all([fetch('/api/predictions').then(r=>r.json()),fetch('/api/packages?scope='+scope+'&currency='+curr).then(r=>r.json())])
  .then(([p,pkg])=>{{
    let h=`<div><b>Verified Win Rate:</b> ${{esc(p.win_rate ?? 'Tracking')}}%</div><h3>⚽ Today's Free Predictions</h3>`;
    if(!p.free_predictions.length) h+=`<p style="color:#94a3b8">No free picks published yet.</p>`;
    p.free_predictions.forEach(m=>{{h+=`<div class="card"><b>${{esc(m.match_name)}}</b><br>🎯 ${{esc(m.market)}}: ${{esc(m.tip)}} | 📈 ${{esc(m.odds)}} | ${{esc(m.result)}}</div>`;}});
    h+=`<h3 style="margin-top:16px">👑 VIP Plans (${{esc(pkg.scope)}} / ${{esc(pkg.currency)}})</h3>`;
    pkg.packages.forEach(x=>{{h+=`<div class="card"><b>${{esc(x.title)}}</b> — <span style="color:#f59e0b">${{esc(x.display_price)}}</span><br><small style="color:#94a3b8">${{esc(x.benefits)}}</small></div>`;}});
    document.getElementById('portalBody').innerHTML=h;
  }});
}}
loadPortal();
</script>
</body>
</html>"""


@app.post("/telegram")
async def telegram_webhook(request: Request):
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not secrets.compare_digest(secret, WEBHOOK_SECRET):
        raise HTTPException(status_code=403, detail="Unauthorized")
    update = await request.json()
    asyncio.create_task(_process_update_safe(update))
    return {"ok": True}


@app.post("/payhero/callback")
async def payhero_callback(request: Request):
    payload = await request.json()
    try:
        await process_payhero_callback(payload)
    except Exception:
        log.exception("PayHero callback failed")
        raise HTTPException(status_code=503, detail="Verification failed")
    return {"ok": True}


@app.api_route("/cryptapi/callback", methods=["GET", "POST"])
async def cryptapi_callback(request: Request):
    qp = dict(request.query_params)
    order_ref, nonce = qp.get("order_ref", ""), qp.get("nonce", "")
    if not order_ref or not nonce:
        return PlainTextResponse("missing params", status_code=400)

    with connect_db() as db:
        order = db.execute("SELECT * FROM orders WHERE external_ref=?", (order_ref,)).fetchone()
    if not order or not order["callback_nonce"] or not secrets.compare_digest(nonce, order["callback_nonce"]):
        return PlainTextResponse("unauthorized", status_code=403)
    if order["status"] == "PAID":
        return PlainTextResponse("*ok*")

    body_data = dict(qp)
    if request.method == "POST":
        ct = request.headers.get("content-type", "")
        try:
            if "application/json" in ct:
                j = await request.json()
                if isinstance(j, dict):
                    body_data.update(j)
            else:
                body_data.update(dict(await request.form()))
        except Exception:
            pass

    try:
        res = await verify_cryptapi_order_via_logs(order)
        if res == "NO_TX" and body_data.get("txid_in"):
            await process_cryptapi_event(order, body_data)
    except Exception:
        log.exception("CryptAPI callback failed for %s", order_ref)
        return PlainTextResponse("retry", status_code=503)

    return PlainTextResponse("*ok*")


@app.api_route("/.well-known/cryptapi-verification.txt", methods=["GET", "HEAD"])
async def cryptapi_wellknown_verify():
    content = CRYPTAPI_VERIFY_FILE_CONTENT or "cryptapi-verification=00bdc4cdec3ec5d8bcc7577811bc1fb6"
    return PlainTextResponse(content, status_code=200)


if CRYPTAPI_VERIFY_FILE_NAME and CRYPTAPI_VERIFY_FILE_CONTENT:
    extra_path = "/" + CRYPTAPI_VERIFY_FILE_NAME.lstrip("/")
    if extra_path != "/.well-known/cryptapi-verification.txt":
        @app.api_route(extra_path, methods=["GET", "HEAD"])
        async def cryptapi_domain_verify_file():
            return PlainTextResponse(CRYPTAPI_VERIFY_FILE_CONTENT, status_code=200)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "10000")))

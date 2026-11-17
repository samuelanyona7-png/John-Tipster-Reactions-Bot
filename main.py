
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
    "KE": {"flag": "🇰🇪", "name": "Kenya", "currency": "KES", "tz": "Africa/Nairobi", "methods": ["mpesa", "mpesa_till", "airtel"], "how": f"1️⃣ <b>M-Pesa STK Push (PayHero)</b>\n2️⃣ <b>Manual Till: <code>{DEFAULT_MPESA_TILL}</code></b>\n3️⃣ <b>Airtel Money: <code>{DEFAULT_AIRTEL_NUMBER}</code></b>"},
    "TZ": {"flag": "🇹🇿", "name": "Tanzania", "currency": "TZS", "tz": "Africa/Dar_es_Salaam", "methods": ["airtel", "mpesa_tz", "tigo"], "how": f"1️⃣ <b>Airtel Money: <code>{DEFAULT_AIRTEL_NUMBER}</code></b> (or {BRAND_TAG} for M-Pesa TZ / Mixx)\n2️⃣ Send screenshot/PDF receipt here."},
    "UG": {"flag": "🇺🇬", "name": "Uganda", "currency": "UGX", "tz": "Africa/Kampala", "methods": ["airtel", "mtn"], "how": f"1️⃣ <b>Airtel Money: <code>{DEFAULT_AIRTEL_NUMBER}</code></b> (or {BRAND_TAG} for MTN MoMo)\n2️⃣ Send screenshot/PDF receipt here."},
    "ZM": {"flag": "🇿🇲", "name": "Zambia", "currency": "ZMW", "tz": "Africa/Lusaka", "methods": ["airtel", "mtn"], "how": f"1️⃣ <b>Airtel Money: <code>{DEFAULT_AIRTEL_NUMBER}</code></b> (or {BRAND_TAG} for MTN MoMo)\n2️⃣ Send screenshot/PDF receipt here."},
    "INTL": {"flag": "🌍", "name": "International (Global)", "currency": "USD", "tz": "UTC", "methods": ["crypto", "intl_transfer"], "how": f"1️⃣ <b>Crypto (CryptAPI — 100+ Coins)</b>\n2️⃣ <b>Card / PayPal / Transfer</b> via {BRAND_TAG}."},
    "US": {"flag": "🇺🇸", "name": "United States", "currency": "USD", "tz": "America/New_York", "methods": ["crypto", "intl_transfer"], "how": f"1️⃣ <b>Crypto (CryptAPI)</b>\n2️⃣ Card / PayPal via {BRAND_TAG}."},
    "GB": {"flag": "🇬🇧", "name": "United Kingdom", "currency": "GBP", "tz": "Europe/London", "methods": ["crypto", "intl_transfer"], "how": f"1️⃣ <b>Crypto (CryptAPI)</b>\n2️⃣ UK Bank / PayPal via {BRAND_TAG}."},
    "EU": {"flag": "🇪🇺", "name": "Europe (Eurozone)", "currency": "EUR", "tz": "Europe/Berlin", "methods": ["crypto", "intl_transfer"], "how": f"1️⃣ <b>Crypto (CryptAPI)</b>\n2️⃣ SEPA / PayPal via {BRAND_TAG}."},
    "NG": {"flag": "🇳🇬", "name": "Nigeria", "currency": "NGN", "tz": "Africa/Lagos", "methods": ["crypto", "ng_bank"], "how": f"1️⃣ <b>Crypto (CryptAPI)</b>\n2️⃣ Nigeria Bank / OPay via {BRAND_TAG}."},
    "GH": {"flag": "🇬🇭", "name": "Ghana", "currency": "GHS", "tz": "Africa/Accra", "methods": ["crypto", "intl_transfer"], "how": f"1️⃣ <b>Crypto (CryptAPI)</b>\n2️⃣ International transfer via {BRAND_TAG}."},
    "ZA": {"flag": "🇿🇦", "name": "South Africa", "currency": "ZAR", "tz": "Africa/Johannesburg", "methods": ["crypto", "za_eft"], "how": f"1️⃣ <b>Crypto (CryptAPI)</b>\n2️⃣ Instant EFT via {BRAND_TAG}."},
    "IN": {"flag": "🇮🇳", "name": "India", "currency": "INR", "tz": "Asia/Kolkata", "methods": ["crypto", "intl_transfer"], "how": f"1️⃣ <b>Crypto (CryptAPI)</b>\n2️⃣ UPI / Transfer via {BRAND_TAG}."},
    "CA": {"flag": "🇨🇦", "name": "Canada", "currency": "CAD", "tz": "America/Toronto", "methods": ["crypto", "intl_transfer"], "how": f"1️⃣ <b>Crypto (CryptAPI)</b>\n2️⃣ Interac / PayPal via {BRAND_TAG}."},
    "AU": {"flag": "🇦🇺", "name": "Australia", "currency": "AUD", "tz": "Australia/Sydney", "methods": ["crypto", "intl_transfer"], "how": f"1️⃣ <b>Crypto (CryptAPI)</b>\n2️⃣ PayID / PayPal via {BRAND_TAG}."},
}

CURRENCIES = {
    "KES": {"symbol": "KES ", "decimals": 0}, "TZS": {"symbol": "TZS ", "decimals": 0}, "UGX": {"symbol": "UGX ", "decimals": 0},
    "ZMW": {"symbol": "ZMW ", "decimals": 2}, "NGN": {"symbol": "₦", "decimals": 0}, "GHS": {"symbol": "GHS ", "decimals": 2},
    "ZAR": {"symbol": "R", "decimals": 2}, "USD": {"symbol": "$", "decimals": 2}, "GBP": {"symbol": "£", "decimals": 2},
    "EUR": {"symbol": "€", "decimals": 2}, "INR": {"symbol": "₹", "decimals": 0}, "CAD": {"symbol": "CA$", "decimals": 2}, "AUD": {"symbol": "AU$", "decimals": 2},
}
EUROZONE_ISO = {"AT", "BE", "CY", "EE", "FI", "FR", "DE", "GR", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PT", "SK", "SI", "ES", "HR"}
METHOD_LABELS = {
    "mpesa": ("📲 M-Pesa STK Push (PayHero)", "success"),
    "mpesa_till": (f"🏪 Manual M-Pesa Till ({DEFAULT_MPESA_TILL})", "primary"),
    "airtel": (f"🔴 Airtel Money ({DEFAULT_AIRTEL_NUMBER})", "danger"),
    "crypto": ("🪙 Crypto (CryptAPI — 100+ Coins)", "success"),
    "mtn": ("🟡 MTN Mobile Money", "primary"), "mpesa_tz": ("🇹🇿 Vodacom M-Pesa TZ", "primary"),
    "tigo": ("🔵 Mixx / Tigo Pesa", "primary"), "ng_bank": ("🇳🇬 Bank / OPay / PalmPay", "primary"),
    "za_eft": ("🇿🇦 Instant EFT / Capitec", "primary"), "intl_transfer": ("💲 Card / PayPal / Bank Transfer", "primary"),
}
ACCUM_CATEGORIES = ["3-5 Odds", "5-10 Odds", "10-15 Odds", "15-20 Odds", "20+ Odds"]
CRYPTO_WALLET_GROUPS = [
    ("evm", "EVM (ETH/ERC20/BEP20/Polygon/Arbitrum/Base/AVAX)"), ("tron", "TRON & TRC20"),
    ("sol", "Solana & SPL"), ("ton", "TON & Jettons"), ("btc", "Bitcoin"),
    ("ltc", "Litecoin"), ("bch", "Bitcoin Cash"), ("doge", "Dogecoin"), ("zec", "Zcash"),
]
CRYPTO_NETWORK_LABELS = {
    "coins": "🪙 Native Coins", "trc20": "🔴 Tron (TRC-20)", "bep20": "🟡 BNB Chain (BEP-20)",
    "erc20": "🔷 Ethereum (ERC-20)", "polygon": "🟣 Polygon", "sol": "🧬 Solana", "ton": "💎 TON",
    "arbitrum": "🔵 Arbitrum", "optimism": "🔺 Optimism", "base": "🟦 Base",
    "avax-c": "❄️ Avalanche-C", "linea": "📏 Linea", "monad": "🟪 Monad", "berachain": "🐻 Berachain",
}
POPULAR_CRYPTO_TICKERS = [
    "trc20/usdt", "bep20/usdt", "sol/usdt", "ton/usdt", "polygon/usdt", "erc20/usdt",
    "trc20/usdc", "bep20/usdc", "sol/usdc", "base/usdc", "btc", "eth", "ltc", "trx", "sol/sol", "ton/ton", "doge", "bch",
]


def check_settings():
    req = {"TELEGRAM_BOT_TOKEN": BOT_TOKEN, "ADMIN_USER_ID": ADMIN_USER_ID, "CHANNEL_ID": CHANNEL_ID, "VIP_CHANNEL_ID": VIP_CHANNEL_ID, "WEBHOOK_SECRET": WEBHOOK_SECRET, "PAYHERO_CHANNEL_ID": PAYHERO_CHANNEL_ID, "PUBLIC_URL": PUBLIC_URL}
    if not PAYHERO_AUTH_TOKEN and not (PAYHERO_API_USERNAME and PAYHERO_API_PASSWORD):
        req["PAYHERO_AUTH_TOKEN"] = ""
    missing = [k for k, v in req.items() if not v]
    if missing:
        raise RuntimeError("Missing env vars: " + ", ".join(missing))
    if not ADMIN_USER_ID.isdigit() or not PAYHERO_CHANNEL_ID.isdigit():
        raise RuntimeError("ADMIN_USER_ID and PAYHERO_CHANNEL_ID must be numeric")
    if USE_POSTGRES and psycopg2 is None:
        raise RuntimeError("Add psycopg2-binary to requirements.txt")


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
    return parse_utc(dt_or_iso).astimezone(tz).strftime("%d %b %Y, %I:%M %p ") + (tz_name.split("/")[-1] if tz_name != "UTC" else "UTC")


async def get_http():
    global http_client
    if http_client is None or http_client.is_closed:
        http_client = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0), limits=httpx.Limits(max_connections=60, max_keepalive_connections=20))
    return http_client


def is_admin(user_id):
    return bool(ADMIN_USER_ID) and ADMIN_USER_ID.isdigit() and str(user_id) == str(ADMIN_USER_ID)


def is_admin_private_chat(user_id, chat_id, chat_type="private"):
    return is_admin(user_id) and chat_type == "private" and str(chat_id) == str(ADMIN_USER_ID)


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
        return int(db.execute(sql + " RETURNING id", params).fetchone()["id"])
    return int(db.execute(sql, params).lastrowid)


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
        if column not in [r["name"] for r in db.execute(f"PRAGMA table_info({table})").fetchall()]:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def reload_settings_cache():
    global _settings_cache, _settings_loaded
    with connect_db() as db:
        _settings_cache = {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM app_settings").fetchall()}
    _settings_loaded = True


def init_db():
    pk = "SERIAL PRIMARY KEY" if USE_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"
    with connect_db() as db:
        db.execute("CREATE TABLE IF NOT EXISTS users (user_id BIGINT PRIMARY KEY, username TEXT, first_name TEXT, country TEXT NOT NULL DEFAULT '', country_confirmed INTEGER NOT NULL DEFAULT 0, currency TEXT NOT NULL DEFAULT 'USD', timezone_name TEXT NOT NULL DEFAULT 'UTC', referred_by BIGINT, referral_rewarded INTEGER NOT NULL DEFAULT 0, is_blocked INTEGER NOT NULL DEFAULT 0, notify_free INTEGER NOT NULL DEFAULT 1, notify_vip INTEGER NOT NULL DEFAULT 1, notify_news INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS plans (name TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '', price INTEGER NOT NULL, days INTEGER NOT NULL, benefits TEXT NOT NULL DEFAULT '', is_lifetime INTEGER NOT NULL DEFAULT 0)")
        db.execute("CREATE TABLE IF NOT EXISTS intl_plans (name TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '', price_usd DOUBLE PRECISION NOT NULL, days INTEGER NOT NULL, benefits TEXT NOT NULL DEFAULT '', is_lifetime INTEGER NOT NULL DEFAULT 0, is_active INTEGER NOT NULL DEFAULT 1)")
        db.execute("CREATE TABLE IF NOT EXISTS sessions (user_id BIGINT PRIMARY KEY, state TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}')")
        has_old = db.execute("SELECT 1 FROM information_schema.columns WHERE table_name='sessions' AND column_name='plan'").fetchone() if USE_POSTGRES else ("plan" in [r["name"] for r in db.execute("PRAGMA table_info(sessions)").fetchall()])
        if has_old:
            db.execute("DROP TABLE sessions")
            db.execute("CREATE TABLE sessions (user_id BIGINT PRIMARY KEY, state TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}')")
        db.execute("CREATE TABLE IF NOT EXISTS orders (external_ref TEXT PRIMARY KEY, user_id BIGINT NOT NULL, plan TEXT NOT NULL, amount INTEGER NOT NULL, amount_usd DOUBLE PRECISION NOT NULL DEFAULT 0, market_scope TEXT NOT NULL DEFAULT 'LOCAL', currency TEXT NOT NULL DEFAULT 'KES', local_amount TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL, provider TEXT NOT NULL DEFAULT 'mpesa', status TEXT NOT NULL, payhero_reference TEXT, checkout_id TEXT, mpesa_receipt TEXT, proof_file_id TEXT, proof_type TEXT NOT NULL DEFAULT 'photo', crypto_ticker TEXT, crypto_address_in TEXT, crypto_amount TEXT, crypto_txid TEXT, crypto_uuid TEXT, callback_nonce TEXT, created_at TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS subscriptions (user_id BIGINT PRIMARY KEY, plan TEXT NOT NULL, expires_at TEXT NOT NULL, status TEXT NOT NULL, is_lifetime INTEGER NOT NULL DEFAULT 0, reminded INTEGER NOT NULL DEFAULT 0)")
        db.execute(f"CREATE TABLE IF NOT EXISTS matches (id {pk}, tier TEXT NOT NULL, match_name TEXT NOT NULL, market TEXT NOT NULL DEFAULT '1X2', tip TEXT NOT NULL, odds TEXT NOT NULL, match_date TEXT NOT NULL, kickoff_time TEXT NOT NULL, publish_status TEXT NOT NULL DEFAULT 'QUEUED', result TEXT NOT NULL DEFAULT 'PENDING', scheduled_for TEXT, channel_msg_id BIGINT, created_at TEXT NOT NULL)")
        db.execute(f"CREATE TABLE IF NOT EXISTS accumulators (id {pk}, category TEXT NOT NULL, selections_json TEXT NOT NULL, total_odds TEXT NOT NULL, risk_note TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'PENDING_ADMIN', created_by BIGINT NOT NULL, channel_msg_id BIGINT, created_at TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS post_reactions (channel_msg_id BIGINT NOT NULL, user_id BIGINT NOT NULL, emoji TEXT NOT NULL, PRIMARY KEY (channel_msg_id, user_id, emoji))")
        db.execute("CREATE TABLE IF NOT EXISTS exchange_rates (currency TEXT PRIMARY KEY, rate_from_kes DOUBLE PRECISION NOT NULL, updated_at TEXT NOT NULL)")
        db.execute(f"CREATE TABLE IF NOT EXISTS custom_buttons (id {pk}, label TEXT NOT NULL, url TEXT NOT NULL DEFAULT '', button_type TEXT NOT NULL DEFAULT 'TICKET', price INTEGER NOT NULL DEFAULT 0, intl_price_usd DOUBLE PRECISION NOT NULL DEFAULT 0, days INTEGER NOT NULL DEFAULT 1, description TEXT NOT NULL DEFAULT '', style TEXT NOT NULL DEFAULT 'primary', is_active INTEGER NOT NULL DEFAULT 1)")
        db.execute(f"CREATE TABLE IF NOT EXISTS ticket_media (id {pk}, offer_key TEXT NOT NULL, file_id TEXT NOT NULL, media_type TEXT NOT NULL DEFAULT 'photo', caption TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS channel_posts (channel_msg_id BIGINT PRIMARY KEY, button_ids TEXT NOT NULL DEFAULT '[]', include_reactions INTEGER NOT NULL DEFAULT 1, include_footer INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute(f"CREATE TABLE IF NOT EXISTS audit_logs (id {pk}, actor_id BIGINT NOT NULL, action TEXT NOT NULL, details TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)")

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
            ("matches", "market", "TEXT NOT NULL DEFAULT '1X2'"), ("custom_buttons", "button_type", "TEXT NOT NULL DEFAULT 'URL'"),
            ("custom_buttons", "price", "INTEGER NOT NULL DEFAULT 0"), ("custom_buttons", "intl_price_usd", "DOUBLE PRECISION NOT NULL DEFAULT 0"),
            ("custom_buttons", "days", "INTEGER NOT NULL DEFAULT 1"), ("custom_buttons", "description", "TEXT NOT NULL DEFAULT ''"),
            ("custom_buttons", "style", "TEXT NOT NULL DEFAULT 'primary'"), ("custom_buttons", "is_active", "INTEGER NOT NULL DEFAULT 1"),
        ]:
            ensure_column(db, tbl, col, defn)

        for row in [
            ("daily", "Daily VIP", 100, 1, "24 Hours VIP access • Daily picks", 0),
            ("weekly", "Weekly VIP", 300, 7, "7 Days VIP access • Daily slips", 0),
            ("monthly", "Monthly VIP", 1000, 30, "30 Days VIP access • Best value", 0),
            ("premium", "Premium VIP", 2500, 90, "90 Days VIP access • Priority support", 0),
            ("lifetime", "Lifetime VIP", 10000, 36500, "Permanent VIP access • Never expires", 1),
        ]:
            db.execute("INSERT INTO plans(name,title,price,days,benefits,is_lifetime) VALUES(?,?,?,?,?,?) ON CONFLICT(name) DO NOTHING", row)

        for row in [
            ("intl_daily", "Global Daily VIP", 5.0, 1, "24 Hours Global VIP access", 0, 1),
            ("intl_weekly", "Global Weekly VIP", 15.0, 7, "7 Days Global VIP access", 0, 1),
            ("intl_monthly", "Global Monthly VIP", 40.0, 30, "30 Days Global VIP access • Best value", 0, 1),
            ("intl_premium", "Global Premium VIP", 99.0, 90, "90 Days Global VIP access", 0, 1),
            ("intl_lifetime", "Global Lifetime VIP", 299.0, 36500, "Permanent Global VIP access", 1, 1),
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

        prev_ph = db.execute("SELECT value FROM app_settings WHERE key='env_payhero_channel_id'").fetchone()
        if PAYHERO_CHANNEL_ID and (not prev_ph or prev_ph["value"] != PAYHERO_CHANNEL_ID):
            db.execute("INSERT INTO app_settings(key,value) VALUES('payhero_channel_id',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (PAYHERO_CHANNEL_ID,))
            db.execute("INSERT INTO app_settings(key,value) VALUES('env_payhero_channel_id',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (PAYHERO_CHANNEL_ID,))

        if not db.execute("SELECT value FROM app_settings WHERE key='seeded_ticket_buttons'").fetchone():
            for lbl, url, bt, p, ip, d, desc, st in [
                ("🔵 Get Blue Ticket", "", "TICKET", 500, 10.0, 1, "Blue VIP Ticket — High-confidence picks", "primary"),
                ("🎫 Get Daily Ticket", "", "TICKET", 200, 5.0, 1, "Daily Ticket — Today's top selections", "success"),
                ("🥇 Golden Ticket", "", "TICKET", 1000, 20.0, 3, "Golden VIP Ticket — Premium multi-match slip", "danger"),
                ("🇲🇰 Get Macedonia Ticket", "", "TICKET", 1500, 25.0, 1, "Macedonia Special Ticket — Exclusive fixture", "primary"),
                ("🇪🇺 Euro Ticket", "", "TICKET", 800, 15.0, 1, "European Leagues Ticket — Top Euro picks", "success"),
            ]:
                db.execute("INSERT INTO custom_buttons(label,url,button_type,price,intl_price_usd,days,description,style,is_active) VALUES(?,?,?,?,?,?,?,?,1)", (lbl, url, bt, p, ip, d, desc, st))
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
    u = get_setting("website_url", DEFAULT_WEBSITE_URL).strip()
    return ("https://" + u) if (u and not u.startswith(("http://", "https://"))) else u


def get_user(user_id):
    with connect_db() as db:
        return db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def register_user(user, referred_by=None):
    uid = user.get("id")
    if not uid:
        return
    uname, fname = user.get("username") or "", user.get("first_name") or "Member"
    if referred_by == uid or (referred_by and not get_user(referred_by)):
        referred_by = None
    with connect_db() as db:
        ex = db.execute("SELECT user_id, username, first_name FROM users WHERE user_id=?", (uid,)).fetchone()
        if ex:
            if ex["username"] != uname or ex["first_name"] != fname:
                db.execute("UPDATE users SET username=?, first_name=? WHERE user_id=?", (uname, fname, uid))
        else:
            db.execute("INSERT INTO users(user_id,username,first_name,country,country_confirmed,currency,timezone_name,referred_by,created_at) VALUES(?,?,?,'',0,'KES','UTC',?,?)", (uid, uname, fname, referred_by, utcnow().isoformat()))


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


def get_all_custom_buttons(only_active=True):
    with connect_db() as db:
        return db.execute("SELECT * FROM custom_buttons WHERE is_active=1 ORDER BY id" if only_active else "SELECT * FROM custom_buttons ORDER BY id").fetchall()


def get_custom_button(btn_id):
    with connect_db() as db:
        return db.execute("SELECT * FROM custom_buttons WHERE id=?", (int(btn_id),)).fetchone()


def get_plan(name):
    with connect_db() as db:
        return db.execute("SELECT * FROM plans WHERE name=?", (name,)).fetchone()


def get_intl_plan(name):
    with connect_db() as db:
        return db.execute("SELECT * FROM intl_plans WHERE name=?", (name,)).fetchone()


def get_all_intl_plans(only_active=True):
    with connect_db() as db:
        return db.execute("SELECT * FROM intl_plans WHERE is_active=1 ORDER BY price_usd" if only_active else "SELECT * FROM intl_plans ORDER BY price_usd").fetchall()


def media_key_for_offer(k):
    s = str(k)
    return ("btn:" + s.split(":", 1)[1]) if s.startswith("ibtn:") else s


def get_offer_media(k):
    with connect_db() as db:
        return db.execute("SELECT * FROM ticket_media WHERE offer_key=? ORDER BY id", (media_key_for_offer(k),)).fetchall()


def count_offer_media(k):
    with connect_db() as db:
        return int(db.execute("SELECT COUNT(*) AS n FROM ticket_media WHERE offer_key=?", (media_key_for_offer(k),)).fetchone()["n"])


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
        p_kes, p_usd = int(b["price"]), float(b["intl_price_usd"] or 0)
        if (is_intl and p_usd <= 0) or (not is_intl and p_kes <= 0):
            return None
        return {"kind": "button", "scope": "INTL" if is_intl else "LOCAL", "key": k, "btn_id": int(b["id"]), "title": b["label"], "price": p_kes, "price_usd": p_usd, "days": int(b["days"]), "is_lifetime": 0, "description": b["description"] or ""}
    if k.startswith("intl:"):
        ip = get_intl_plan(k.split(":", 1)[1])
        if not ip or not ip["is_active"]:
            return None
        return {"kind": "intl_plan", "scope": "INTL", "key": k, "btn_id": None, "title": ip["title"], "price": 0, "price_usd": float(ip["price_usd"]), "days": int(ip["days"]), "is_lifetime": int(ip["is_lifetime"]), "description": ip["benefits"] or ""}
    p = get_plan(k)
    if not p:
        return None
    return {"kind": "plan", "scope": "LOCAL", "key": p["name"], "btn_id": None, "title": p["title"] or p["name"].title(), "price": int(p["price"]), "price_usd": 0.0, "days": int(p["days"]), "is_lifetime": int(p["is_lifetime"]), "description": p["benefits"] or ""}


def format_order_item_name(plan_key):
    k = str(plan_key or "")
    if k.startswith("btn:") or k.startswith("ibtn:"):
        rid = k.split(":", 1)[1]
        if rid.isdigit():
            b = get_custom_button(int(rid))
            if b:
                tag = "Intl" if k.startswith("ibtn:") else "Local"
                return f"{b['label']} ({tag})"
        return f"Ticket #{rid}"
    if k.startswith("intl:"):
        ip = get_intl_plan(k.split(":", 1)[1])
        return ip["title"] if ip else k
    p = get_plan(k)
    return p["title"] if (p and p["title"]) else k.title()


def _load_fx_into_mem():
    global _fx_cache, _fx_cache_ts
    with connect_db() as db:
        rows = db.execute("SELECT currency, rate_from_kes FROM exchange_rates").fetchall()
    _fx_cache = {"KES": 1.0, **{r["currency"]: float(r["rate_from_kes"]) for r in rows}}
    _fx_cache_ts = time.time()


async def refresh_exchange_rates(force=False):
    if not force:
        with connect_db() as db:
            row = db.execute("SELECT updated_at FROM exchange_rates WHERE currency='USD'").fetchone()
        if row and (utcnow() - parse_utc(row["updated_at"])).total_seconds() / 3600.0 < FX_CACHE_HOURS:
            _load_fx_into_mem()
            return True
    url = f"https://v6.exchangerate-api.com/v6/{EXCHANGE_RATE_API_KEY}/latest/KES" if EXCHANGE_RATE_API_KEY else "https://open.er-api.com/v6/latest/KES"
    try:
        resp = await (await get_http()).get(url, timeout=12)
        rates = resp.json().get("conversion_rates") or resp.json().get("rates") or {}
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
    except Exception:
        return False


def get_cached_rate(code):
    c = (code or "KES").upper()
    if c == "KES":
        return 1.0
    if time.time() - _fx_cache_ts > 300:
        _load_fx_into_mem()
    return _fx_cache.get(c)


def format_converted_price(kes_amt, disp_curr):
    code = (disp_curr or "KES").upper()
    rate = get_cached_rate(code)
    if code == "KES" or rate is None:
        return f"KES {int(kes_amt):,}", False
    meta = CURRENCIES.get(code, {"symbol": f"{code} ", "decimals": 2})
    conv = float(kes_amt) * rate
    fmt = f"{meta['symbol']}{int(round(conv)):,}" if meta["decimals"] == 0 else f"{meta['symbol']}{conv:,.2f}"
    return f"{fmt} {code} (KES {int(kes_amt):,})", True


def format_usd_price(usd_amt, disp_curr="USD"):
    usd = float(usd_amt or 0)
    code = (disp_curr or "USD").upper()
    if code == "USD":
        return f"${usd:,.2f} USD", False
    r_usd, r_t = get_cached_rate("USD"), get_cached_rate(code)
    if not r_usd or not r_t:
        return f"${usd:,.2f} USD", False
    meta = CURRENCIES.get(code, {"symbol": f"{code} ", "decimals": 2})
    conv = usd * (r_t / r_usd)
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
    resp = await (await get_http()).get("https://nominatim.openstreetmap.org/reverse", params={"format": "jsonv2", "lat": lat, "lon": lon, "zoom": 3}, headers={"User-Agent": "PrimeTipsEmpireBot/1.0"}, timeout=10)
    if resp.status_code != 200:
        return None
    iso2 = ((resp.json().get("address") or {}).get("country_code") or "").upper()
    return "EU" if iso2 in EUROZONE_ISO else (iso2 if iso2 in COUNTRIES else "INTL")


async def telegram(method, data):
    res = (await (await get_http()).post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", json=data)).json()
    if not res.get("ok"):
        raise RuntimeError(f"Telegram {method}: {res.get('description', res)}")
    return res.get("result")


async def ensure_bot_username():
    global bot_username
    if not bot_username:
        bot_username = (await telegram("getMe", {}))["username"]
    return bot_username


async def telegram_send_document(chat_id, filename, content_bytes, caption=""):
    res = (await (await get_http()).post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument", data={"chat_id": str(chat_id), "caption": caption, "parse_mode": "HTML"}, files={"document": (filename, content_bytes, "text/csv")})).json()
    if not res.get("ok"):
        raise RuntimeError(res.get("description", res))
    return res.get("result")


async def telegram_send_photo_bytes(chat_id, filename, photo_bytes, caption="", keyboard=None):
    data = {"chat_id": str(chat_id), "caption": caption[:1020], "parse_mode": "HTML"}
    if keyboard:
        data["reply_markup"] = json.dumps(keyboard)
    res = (await (await get_http()).post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto", data=data, files={"photo": (filename, photo_bytes, "image/png")})).json()
    if not res.get("ok"):
        raise RuntimeError(res.get("description", res))
    return res.get("result")


async def send_message(chat_id, text, keyboard=None):
    chunks, rem = [], text
    while len(rem) > 3900:
        cut = rem.rfind("\n\n", 0, 3900)
        if cut < 400:
            cut = rem.rfind("\n", 0, 3900)
        if cut < 200:
            cut = 3900
        chunks.append(rem[:cut])
        rem = rem[cut:].lstrip()
    if rem:
        chunks.append(rem)
    last = None
    for i, ch in enumerate(chunks):
        d = {"chat_id": chat_id, "text": ch, "parse_mode": "HTML", "disable_web_page_preview": True}
        if keyboard and i == len(chunks) - 1:
            d["reply_markup"] = keyboard
        last = await telegram("sendMessage", d)
    return last


async def edit_or_send(chat_id, message_id, text, keyboard=None):
    if message_id:
        try:
            p = {"chat_id": chat_id, "message_id": message_id, "text": text[:4090], "parse_mode": "HTML", "disable_web_page_preview": True}
            if keyboard:
                p["reply_markup"] = keyboard
            return await telegram("editMessageText", p)
        except Exception:
            pass
    return await send_message(chat_id, text, keyboard)


async def answer_callback(cid, text=""):
    try:
        await telegram("answerCallbackQuery", {"callback_query_id": cid, "text": text})
    except Exception:
        pass


async def report_unauthorized_admin_attempt(user_id, label):
    log_audit(user_id, "UNAUTHORIZED_ADMIN", label)
    try:
        await send_message(ADMIN_USER_ID, f"🚨 <b>UNAUTHORIZED ADMIN BLOCKED</b>\nUser: <code>{user_id}</code>\nAttempt: <code>{html_escape(label)}</code>")
    except Exception:
        pass


def main_menu_keyboard(user_id=None):
    ch_url = get_setting("main_channel_url") or get_website_url()
    portal_url = PUBLIC_URL.rstrip("/") + "/app"
    portal_btn = {"text": "🌐 Web Portal", "web_app": {"url": portal_url}, "style": "primary"} if portal_url.startswith("https://") else {"text": "🌐 Web Portal", "url": portal_url, "style": "primary"}
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
    return {"inline_keyboard": [
        [{"text": "🌍 Detect My Country", "callback_data": "country:detect_ask", "style": "primary"}],
        [{"text": "🗺️ Select Country Manually", "callback_data": "country:manual", "style": "success"}],
        [{"text": "🌍 International Global ($ USD / Crypto)", "callback_data": "country:confirm:INTL", "style": "danger"}],
        [{"text": "⏭️ Continue to Free Features", "callback_data": "menu:home"}],
    ]}


def country_manual_keyboard():
    items = list(COUNTRIES.items())
    rows = [[{"text": f"{v['flag']} {v['name']} ({v['currency']})", "callback_data": f"country:pick:{k}", "style": "primary"} for k, v in items[i:i + 2]] for i in range(0, len(items), 2)]
    rows.append([{"text": "💱 Change Currency Only", "callback_data": "currency:menu", "style": "success"}])
    rows.append([{"text": "🏠 Main Menu", "callback_data": "menu:home"}])
    return {"inline_keyboard": rows}


def currency_manual_keyboard():
    items = list(CURRENCIES.keys())
    rows = [[{"text": f"💱 {c}", "callback_data": f"currency:set:{c}", "style": "primary"} for c in items[i:i + 3]] for i in range(0, len(items), 3)]
    rows.append([{"text": "⬅️ Back", "callback_data": "menu:country"}])
    return {"inline_keyboard": rows}


def local_plans_keyboard(disp_curr="KES"):
    with connect_db() as db:
        plans = db.execute("SELECT * FROM plans ORDER BY price").fetchall()
    smap = {"daily": "success", "weekly": "primary", "monthly": "primary", "premium": "danger", "lifetime": "danger"}
    rows = []
    for p in plans:
        dur = "Lifetime" if p["is_lifetime"] else f"{p['days']}d"
        p_str, _ = format_converted_price(p["price"], disp_curr)
        title = p["title"] or p["name"].title()
        rows.append([{"text": f"👑 {title} ({dur}) — {p_str}", "callback_data": f"plan:{p['name']}", "style": smap.get(p["name"], "primary")}])
    rows.append([{"text": "🌍 Switch to International Plans ($ USD)", "callback_data": "menu:vip_intl", "style": "primary"}])
    rows.append([{"text": "🏠 Main Menu", "callback_data": "menu:home"}])
    return {"inline_keyboard": rows}


def intl_plans_keyboard(disp_curr="USD"):
    rows = []
    for p in get_all_intl_plans(only_active=True):
        dur = "Lifetime" if p["is_lifetime"] else f"{p['days']}d"
        p_str, _ = format_usd_price(p["price_usd"], disp_curr)
        rows.append([{"text": f"🌍 {p['title']} ({dur}) — {p_str}", "callback_data": f"iplan:{p['name']}", "style": "success"}])
    rows.append([{"text": "🇰🇪 Switch to East Africa Local Plans", "callback_data": "menu:vip_local", "style": "primary"}])
    rows.append([{"text": "🏠 Main Menu", "callback_data": "menu:home"}])
    return {"inline_keyboard": rows}


def get_reaction_counts(msg_id):
    counts = {e: 0 for e in REACTION_EMOJIS}
    if not msg_id:
        return counts
    with connect_db() as db:
        for r in db.execute("SELECT emoji, COUNT(*) AS cnt FROM post_reactions WHERE channel_msg_id=? GROUP BY emoji", (msg_id,)).fetchall():
            if r["emoji"] in counts:
                counts[r["emoji"]] = int(r["cnt"])
    return counts


def save_channel_post_meta(msg_id, btn_ids, reacts, footer):
    with connect_db() as db:
        db.execute("INSERT INTO channel_posts(channel_msg_id,button_ids,include_reactions,include_footer,created_at) VALUES(?,?,?,?,?) ON CONFLICT(channel_msg_id) DO UPDATE SET button_ids=excluded.button_ids, include_reactions=excluded.include_reactions, include_footer=excluded.include_footer", (int(msg_id), json.dumps([int(x) for x in (btn_ids or [])]), 1 if reacts else 0, 1 if footer else 0, utcnow().isoformat()))


def channel_post_keyboard(channel_msg_id=None, selected_btn_ids=None, include_footer=True, include_reactions=True):
    if channel_msg_id and selected_btn_ids is None:
        with connect_db() as db:
            meta = db.execute("SELECT * FROM channel_posts WHERE channel_msg_id=?", (int(channel_msg_id),)).fetchone()
        if meta:
            selected_btn_ids = json.loads(meta["button_ids"]) if meta["button_ids"] else []
            include_reactions, include_footer = bool(meta["include_reactions"]), bool(meta["include_footer"])

    contact_url = get_setting("admin_contact_url", f"https://t.me/{BRAND_TAG.lstrip('@')}")
    join_url = f"https://t.me/{bot_username}?start=vip" if bot_username else contact_url
    website_url = get_website_url()
    rows = []
    if include_reactions:
        counts = get_reaction_counts(channel_msg_id)
        rows.append([{"text": f"{e} {counts[e]}", "callback_data": f"react:{i}"} for i, e in enumerate(REACTION_EMOJIS[:3])])
        rows.append([{"text": f"{e} {counts[e]}", "callback_data": f"react:{i + 3}"} for i, e in enumerate(REACTION_EMOJIS[3:])])
    if selected_btn_ids:
        with connect_db() as db:
            ph = ",".join(["?"] * len(selected_btn_ids))
            btns = db.execute(f"SELECT * FROM custom_buttons WHERE id IN ({ph}) AND is_active=1 ORDER BY id", tuple(int(x) for x in selected_btn_ids)).fetchall()
        bmap = {int(b["id"]): b for b in btns}
        for bid in selected_btn_ids:
            b = bmap.get(int(bid))
            if not b:
                continue
            st = b["style"] if b["style"] in BUTTON_STYLES else "primary"
            turl = b["url"] if (b["button_type"] == "URL" and b["url"]) else (f"https://t.me/{bot_username}?start=btn_{b['id']}" if bot_username else contact_url)
            rows.append([{"text": b["label"], "url": turl, "style": st}])
    if include_footer:
        rows.append([{"text": "👑 JOIN VIP NOW", "url": join_url, "style": "danger"}, {"text": "💬 Contact Admin", "url": contact_url, "style": "success"}])
        if website_url:
            short_dom = re.sub(r"^https?://", "", website_url).rstrip("/")
            rows.append([{"text": f"🌐 {short_dom}", "url": website_url, "style": "primary"}])
    return {"inline_keyboard": rows} if rows else None


def admin_keyboard(user_id):
    if not is_admin(user_id):
        return {"inline_keyboard": []}
    return {"inline_keyboard": [
        [{"text": "📊 Overview & Stats", "callback_data": "admin:stats", "style": "primary"}, {"text": "⚽ Fixtures & Matches", "callback_data": "admin:matches", "style": "success"}],
        [{"text": "🎯 Accumulator Tickets", "callback_data": "admin:accums", "style": "primary"}, {"text": "🧾 Transactions", "callback_data": "admin:transactions", "style": "success"}],
        [{"text": "👥 Users & VIPs", "callback_data": "admin:users", "style": "primary"}, {"text": "🎛️ Channel Buttons & Tickets", "callback_data": "admin:buttons", "style": "primary"}],
        [{"text": "🇰🇪 Local VIP Plans (KES)", "callback_data": "admin:packages", "style": "success"}, {"text": "🌍 International Plans & Prices ($)", "callback_data": "admin:intl_packages", "style": "danger"}],
        [{"text": "📝 Post Main Channel", "callback_data": "admin:post_channel", "style": "success"}, {"text": "💎 Post VIP Channel", "callback_data": "admin:post_vip_channel", "style": "danger"}],
        [{"text": "📢 VIP Promo Post", "callback_data": "admin:post", "style": "primary"}, {"text": "📣 Broadcast", "callback_data": "admin:broadcast", "style": "danger"}],
        [{"text": "⚙️ Channels & Payment Settings", "callback_data": "admin:channels", "style": "primary"}, {"text": "📥 Export CSV", "callback_data": "admin:export", "style": "success"}],
        [{"text": "🛡️ Audit Logs", "callback_data": "admin:audit", "style": "primary"}, {"text": "🏠 Main Menu", "callback_data": "menu:home"}],
    ]}


def payhero_headers():
    if PAYHERO_API_USERNAME and PAYHERO_API_PASSWORD and not PAYHERO_AUTH_TOKEN:
        raw_c = f"{PAYHERO_API_USERNAME}:{PAYHERO_API_PASSWORD}".encode("utf-8")
        return {"Authorization": "Basic " + base64.b64encode(raw_c).decode("utf-8"), "Content-Type": "application/json"}
    tok = re.sub(r"^(basic|bearer)\s+", "", PAYHERO_AUTH_TOKEN.strip().strip('"').strip("'").strip(), flags=re.IGNORECASE).strip()
    auth = f"Basic {base64.b64encode(tok.encode('utf-8')).decode('utf-8')}" if ":" in tok else f"Basic {tok}"
    return {"Authorization": auth, "Content-Type": "application/json"}


def normalize_phone(phone):
    p = re.sub(r"[\s+\-()]", "", phone)
    if p.startswith("2540") and len(p) == 13:
        p = "0" + p[4:]
    elif p.startswith("254") and len(p) == 12:
        p = "0" + p[3:]
    elif (p.startswith("7") or p.startswith("1")) and len(p) == 9:
        p = "0" + p
    return p if re.fullmatch(r"0[17]\d{8}", p) else None


def recent_stk_cooldown_remaining(user_id):
    with connect_db() as db:
        last = db.execute("SELECT created_at FROM orders WHERE user_id=? AND provider='mpesa' ORDER BY created_at DESC LIMIT 1", (user_id,)).fetchone()
    return max(0, int(STK_COOLDOWN_SECONDS - (utcnow() - parse_utc(last["created_at"])).total_seconds())) if last else 0


async def send_manual_till_fallback(user_id, ext_ref, title, amount, reason=""):
    till = get_setting("mpesa_till_number", DEFAULT_MPESA_TILL)
    with connect_db() as db:
        db.execute("UPDATE orders SET provider='mpesa_till', status='PENDING_PROOF' WHERE external_ref=?", (ext_ref,))
    set_session(user_id, "WAIT_PAYMENT_PROOF", ext_ref)
    pre = f"⚠️ <i>{html_escape(reason)}</i>\n\n" if reason else ""
    await send_message(user_id, f"{pre}🏪 <b>M-PESA TILL {html_escape(till)}</b>\n🎫 <b>{html_escape(title)}</b> | 💰 <b>KES {int(amount):,}</b>\n🔗 Ref: <code>{ext_ref}</code>\n\nPay via <b>Lipa na M-Pesa → Buy Goods ({html_escape(till)})</b> then send your screenshot or PDF receipt here!", {"inline_keyboard": [[{"text": "📸 Send Screenshot / PDF", "callback_data": f"proof:{ext_ref}", "style": "success"}], [{"text": f"💬 Contact {BRAND_TAG}", "url": get_setting("admin_contact_url"), "style": "primary"}], [{"text": "🏠 Main Menu", "callback_data": "menu:home"}]]})


async def create_manual_order_and_prompt(user_id, offer_key, method_key):
    offer = resolve_offer(offer_key)
    if not offer:
        await send_message(user_id, "⚠️ Selected item unavailable.")
        return
    prefs = get_user_prefs(user_id)
    if offer["scope"] == "INTL":
        disp, _ = format_usd_price(offer["price_usd"], prefs["currency"] if prefs["is_intl"] else "USD")
        amt_kes, amt_usd, curr = 0, float(offer["price_usd"]), "USD"
    else:
        disp, _ = format_converted_price(offer["price"], prefs["currency"])
        amt_kes, amt_usd, curr = int(offer["price"]), 0.0, prefs["currency"]

    ext_ref = "MAN" + secrets.token_hex(6).upper()
    with connect_db() as db:
        db.execute("INSERT INTO orders(external_ref,user_id,plan,amount,amount_usd,market_scope,currency,local_amount,phone,provider,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (ext_ref, user_id, offer["key"], amt_kes, amt_usd, offer["scope"], curr, disp, method_key, method_key, "PENDING_PROOF", utcnow().isoformat()))
    set_session(user_id, "WAIT_PAYMENT_PROOF", ext_ref)
    if method_key == "mpesa_till":
        await send_manual_till_fallback(user_id, ext_ref, offer["title"], amt_kes)
        return
    if method_key == "airtel":
        airtel = get_setting("airtel_money_number", DEFAULT_AIRTEL_NUMBER)
        await send_message(user_id, f"🔴 <b>AIRTEL MONEY</b>\n🎫 <b>{html_escape(offer['title'])}</b> | 💰 <b>{html_escape(disp)}</b>\n📞 Number: <code>{html_escape(airtel)}</code> | 🔗 Ref: <code>{ext_ref}</code>\n\nSend screenshot/PDF receipt here after paying!", {"inline_keyboard": [[{"text": "📸 Send Screenshot / PDF", "callback_data": f"proof:{ext_ref}", "style": "success"}], [{"text": "🏠 Main Menu", "callback_data": "menu:home"}]]})
        return
    await send_message(user_id, f"🌍 <b>INTERNATIONAL PAYMENT</b>\n🎫 <b>{html_escape(offer['title'])}</b> | 💰 <b>{html_escape(disp)}</b>\n🔗 Ref: <code>{ext_ref}</code>\n\n{html_escape(get_setting('intl_payment_note'))}\n\nSend screenshot/PDF receipt here after paying!", {"inline_keyboard": [[{"text": f"💬 Contact {BRAND_TAG}", "url": get_setting("admin_contact_url"), "style": "primary"}], [{"text": "📸 Send Screenshot / PDF", "callback_data": f"proof:{ext_ref}", "style": "success"}], [{"text": "🏠 Main Menu", "callback_data": "menu:home"}]]})


async def fetch_payhero_diagnostics():
    client = await get_http()
    hdrs = payhero_headers()

    async def get_ep(path, params):
        try:
            r = await client.get(PAYHERO_URL + path, headers=hdrs, params=params, timeout=15)
            return {"status": r.status_code, "data": r.json() if "application/json" in r.headers.get("content-type", "") else r.text[:300]}
        except Exception as e:
            return {"status": 0, "error": str(e)}

    ch_res, wal_res = await asyncio.gather(get_ep("/payment_channels", {"is_active": "true"}), get_ep("/wallets", {"wallet_type": "service_wallet"}))
    return {"channels": ch_res, "wallet": wal_res}


async def start_mpesa_payment(user_id, offer_key, phone, notify_telegram=True):
    offer = resolve_offer(offer_key)
    if not offer or offer["scope"] != "LOCAL":
        if notify_telegram:
            await send_message(user_id, "⚠️ Selected item unavailable for M-Pesa.")
        return {"ok": False}
    wait_sec = recent_stk_cooldown_remaining(user_id)
    if wait_sec > 0:
        if notify_telegram:
            await send_message(user_id, f"⏳ Please wait {wait_sec}s before another STK request.")
        return {"ok": False}

    ext_ref = "PTE" + secrets.token_hex(7).upper()
    amount = max(1, int(offer["price"]))
    raw_ch = get_setting("payhero_channel_id", PAYHERO_CHANNEL_ID)
    ph_ch = int(raw_ch if str(raw_ch).isdigit() else PAYHERO_CHANNEL_ID)
    u = get_user(user_id)
    cname = (u["first_name"] if u and u["first_name"] else "Member")[:40]
    with connect_db() as db:
        db.execute("INSERT INTO orders(external_ref,user_id,plan,amount,amount_usd,market_scope,currency,local_amount,phone,provider,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (ext_ref, user_id, offer["key"], amount, 0.0, "LOCAL", "KES", f"KES {amount:,}", phone, "mpesa", "PENDING", utcnow().isoformat()))

    payload = {"amount": amount, "phone_number": phone, "channel_id": ph_ch, "provider": "m-pesa", "external_reference": ext_ref, "customer_name": cname, "callback_url": PUBLIC_URL.rstrip("/") + "/payhero/callback"}
    if PAYHERO_CREDENTIAL_ID:
        payload["credential_id"] = PAYHERO_CREDENTIAL_ID
    try:
        resp = await (await get_http()).post(PAYHERO_URL + "/payments", headers=payhero_headers(), json=payload)
        res = resp.json() if "application/json" in resp.headers.get("content-type", "") else {"raw": resp.text[:300]}
        if not ((resp.status_code in (200, 201)) and (res.get("success") or res.get("reference"))):
            err = res.get("error_message") or res.get("message") or str(res)[:200]
            asyncio.create_task(send_message(ADMIN_USER_ID, f"⚠️ <b>PayHero STK Failed (HTTP {resp.status_code})</b>\nChannel: <code>{ph_ch}</code> | <code>{html_escape(str(err))}</code>"))
            if notify_telegram:
                await send_manual_till_fallback(user_id, ext_ref, offer["title"], amount, f"STK Push unavailable — use Manual Till {get_setting('mpesa_till_number', DEFAULT_MPESA_TILL)}!")
            return {"ok": False}

        ref = res.get("reference")
        cid = res.get("CheckoutRequestID") or res.get("checkout_request_id")
        if not ref:
            if notify_telegram:
                await send_manual_till_fallback(user_id, ext_ref, offer["title"], amount, f"STK Push unavailable — use Manual Till {get_setting('mpesa_till_number', DEFAULT_MPESA_TILL)}!")
            return {"ok": False}

        with connect_db() as db:
            db.execute("UPDATE orders SET payhero_reference=?, checkout_id=? WHERE external_ref=?", (ref, cid, ext_ref))
        till = get_setting("mpesa_till_number", DEFAULT_MPESA_TILL)
        if notify_telegram:
            await send_message(user_id, f"📲 <b>M-PESA STK PUSH SENT!</b>\n🎫 <b>{html_escape(offer['title'])}</b> — <b>KES {amount:,}</b>\n📞 <code>{html_escape(phone)}</code> | 🔗 <code>{ext_ref}</code>\n🔐 Enter your M-Pesa PIN.", {"inline_keyboard": [[{"text": "🔄 Verify Payment", "callback_data": f"verify:{ext_ref}", "style": "success"}], [{"text": f"🏪 Use Till {till}", "callback_data": f"manualtill:{ext_ref}", "style": "primary"}], [{"text": "📸 Upload Screenshot", "callback_data": f"proof:{ext_ref}", "style": "primary"}], [{"text": "🏠 Main Menu", "callback_data": "menu:home"}]]})
        return {"ok": True, "external_ref": ext_ref}
    except Exception:
        if notify_telegram:
            await send_manual_till_fallback(user_id, ext_ref, offer["title"], amount, f"STK Push timed out — use Manual Till {get_setting('mpesa_till_number', DEFAULT_MPESA_TILL)}!")
        return {"ok": False}


async def verify_payhero_transaction(ref):
    r = await (await get_http()).get(PAYHERO_URL + "/transaction-status", headers=payhero_headers(), params={"reference": ref})
    r.raise_for_status()
    return r.json()


def get_cryptapi_wallets_map():
    m = {}
    for k, en in [("evm", "CRYPTAPI_EVM_ADDRESS"), ("tron", "CRYPTAPI_TRON_ADDRESS"), ("sol", "CRYPTAPI_SOL_ADDRESS"), ("ton", "CRYPTAPI_TON_ADDRESS"), ("btc", "CRYPTAPI_BTC_ADDRESS"), ("ltc", "CRYPTAPI_LTC_ADDRESS"), ("bch", "CRYPTAPI_BCH_ADDRESS"), ("doge", "CRYPTAPI_DOGE_ADDRESS"), ("zec", "CRYPTAPI_ZEC_ADDRESS")]:
        v = env_str(en)
        if v:
            m[k] = v
    for raw in (CRYPTAPI_WALLETS_JSON, get_setting("cryptapi_wallets", "{}")):
        if raw:
            try:
                for k, v in json.loads(raw).items():
                    if v:
                        m[str(k).lower().strip()] = str(v).strip()
            except Exception:
                pass
    return m


def save_cryptapi_wallet(key, address):
    try:
        d = json.loads(get_setting("cryptapi_wallets", "{}"))
        if not isinstance(d, dict):
            d = {}
    except Exception:
        d = {}
    if address and address.strip():
        d[key.lower().strip()] = address.strip()
    else:
        d.pop(key.lower().strip(), None)
    set_setting("cryptapi_wallets", json.dumps(d))


def get_wallet_for_ticker(ticker):
    t = (ticker or "").lower().strip()
    w = get_cryptapi_wallets_map()
    if w.get(t):
        return w[t]
    net = t.split("/", 1)[0] if "/" in t else "coins"
    if t in ("btc", "bch", "ltc", "doge", "zec"):
        return w.get(t, "")
    if t == "trx" or net == "trc20":
        return w.get("tron", "")
    if t == "sol" or net == "sol":
        return w.get("sol", "")
    if t == "ton" or net == "ton":
        return w.get("ton", "")
    if t == "eth" or net in ("erc20", "bep20", "bsc", "polygon", "arbitrum", "optimism", "base", "avax-c", "avax", "linea", "monad", "berachain", "bera"):
        return w.get("evm", "")
    return w.get(net, "")


def _parse_cryptapi_info_payload(raw):
    coins, nets = {}, {}

    def add(guess, info, nk):
        t = (info.get("ticker") or guess or "").lower().strip()
        if not t:
            return
        mn = info.get("minimum_transaction_coin")
        if mn is None:
            mn = info.get("minimum_transaction")
        e = {"ticker": t, "name": str(info.get("coin") or t.upper()), "network": nk, "min_coin": float(mn) if mn not in (None, "") else 0.0}
        coins[t] = e
        nets.setdefault(nk, []).append(e)

    for tk, tv in (raw or {}).items():
        if tk in ("fee_tiers", "status", "minimum_fee") or not isinstance(tv, dict):
            continue
        if "coin" in tv or "minimum_transaction_coin" in tv or "ticker" in tv:
            add(tk, tv, "coins")
        else:
            for sk, sv in tv.items():
                if isinstance(sv, dict) and ("coin" in sv or "minimum_transaction_coin" in sv or "ticker" in sv):
                    add(f"{tk}/{sk}", sv, tk)
    for nk in nets:
        nets[nk].sort(key=lambda x: x["name"])
    return coins, nets


async def fetch_cryptapi_catalog(force=False):
    now = utcnow()
    if not force and _cryptapi_catalog_cache["fetched_at"] and _cryptapi_catalog_cache["coins"]:
        if (now - _cryptapi_catalog_cache["fetched_at"]).total_seconds() / 60.0 < CRYPTAPI_CACHE_MINUTES:
            return _cryptapi_catalog_cache["coins"], _cryptapi_catalog_cache["networks"]
    try:
        r = await (await get_http()).get(f"{CRYPTAPI_URL}/info/", params={"prices": "1"}, timeout=15)
        if r.status_code == 200:
            coins, nets = _parse_cryptapi_info_payload(r.json())
            if coins:
                _cryptapi_catalog_cache.update({"fetched_at": now, "coins": coins, "networks": nets})
                return coins, nets
    except Exception:
        pass
    if _cryptapi_catalog_cache["coins"]:
        return _cryptapi_catalog_cache["coins"], _cryptapi_catalog_cache["networks"]
    coins, nets = {}, {}
    for t, nm, nk, mn in [("btc", "Bitcoin", "coins", 0.00008), ("eth", "Ethereum", "coins", 0.0015), ("ltc", "Litecoin", "coins", 0.002), ("trx", "TRON", "coins", 10.0), ("trc20/usdt", "USDT (TRC20)", "trc20", 1.0), ("bep20/usdt", "USDT (BEP20)", "bep20", 1.0), ("erc20/usdt", "USDT (ERC20)", "erc20", 5.0), ("sol/usdt", "USDT (Solana)", "sol", 1.0), ("ton/usdt", "USDT (TON)", "ton", 1.0)]:
        e = {"ticker": t, "name": nm, "network": nk, "min_coin": mn}
        coins[t] = e
        nets.setdefault(nk, []).append(e)
    return coins, nets


def get_cryptapi_callback_base():
    return (get_setting("cryptapi_callback_base", "").strip() or CRYPTAPI_CALLBACK_BASE_URL or PUBLIC_URL).rstrip("/")


def build_cryptapi_callback_url(ext_ref, nonce):
    return f"{get_cryptapi_callback_base()}/cryptapi/callback?{urlencode({'order_ref': ext_ref, 'nonce': nonce})}"


async def show_crypto_checkout_menu(chat_id, user_id, offer_key, view="home", page=0, message_id=None):
    coins, nets = await fetch_cryptapi_catalog()
    enabled = {t: info for t, info in coins.items() if get_wallet_for_ticker(t)}
    if not enabled:
        msg = "⚠️ <b>Crypto payments not configured yet.</b>" + ("\nAdmin: Go to <b>Admin → Channels & Payment Settings → 🪙 CryptAPI Crypto Wallets</b>." if is_admin(user_id) else f"\nPlease contact {BRAND_TAG}.")
        await edit_or_send(chat_id, message_id, msg, {"inline_keyboard": [[{"text": f"💬 Contact {BRAND_TAG}", "url": get_setting("admin_contact_url"), "style": "primary"}], [{"text": "🏠 Main Menu", "callback_data": "menu:home"}]]})
        return
    offer = resolve_offer(offer_key)
    if not offer or offer["scope"] != "INTL":
        await send_message(chat_id, "⚠️ Crypto checkout is for International prices only.")
        return
    usd_amt = float(offer["price_usd"])
    back_cb = f"iplan:{offer_key.split(':', 1)[1]}" if offer_key.startswith("intl:") else f"openibtn:{offer_key.split(':', 1)[1]}"
    rows = []
    if view == "home":
        pop = [t for t in POPULAR_CRYPTO_TICKERS if t in enabled]
        for i in range(0, len(pop), 2):
            rows.append([{"text": f"🪙 {enabled[t]['name']}", "callback_data": f"cryptopay|{offer_key}|{t}", "style": "success"} for t in pop[i:i + 2]])
        rows.append([{"text": "💵 All Stablecoins", "callback_data": f"cryptolist|{offer_key}|stable|0", "style": "primary"}, {"text": "🌐 All Networks", "callback_data": f"cryptolist|{offer_key}|nets|0", "style": "primary"}])
        rows.append([{"text": "⬅️ Back", "callback_data": back_cb}])
        await edit_or_send(chat_id, message_id, f"🪙 <b>CRYPTO CHECKOUT</b>\n🎫 <b>{html_escape(offer['title'])}</b>\n💵 <b>${usd_amt:,.2f} USD</b> | ✅ <b>{len(enabled)} Coins</b>\n\nChoose coin:", {"inline_keyboard": rows})
        return
    if view == "nets":
        anets = [(nk, sum(1 for it in nl if it["ticker"] in enabled)) for nk, nl in nets.items()]
        anets = [(nk, c) for nk, c in anets if c > 0]
        for i in range(0, len(anets), 2):
            rows.append([{"text": f"{CRYPTO_NETWORK_LABELS.get(nk, nk.upper())} ({c})", "callback_data": f"cryptolist|{offer_key}|net:{nk}|0", "style": "primary"} for nk, c in anets[i:i + 2]])
        rows.append([{"text": "⬅️ Popular", "callback_data": f"cryptolist|{offer_key}|home|0"}])
        await edit_or_send(chat_id, message_id, f"🌐 <b>CRYPTAPI NETWORKS</b> (${usd_amt:,.2f} USD)", {"inline_keyboard": rows})
        return
    if view == "stable":
        items = sorted([c for t, c in enabled.items() if any(s in t for s in ("usdt", "usdc", "dai", "busd", "pyusd", "eurc", "usd1", "fdusd"))], key=lambda x: x["name"])
    elif view.startswith("net:"):
        items = [c for c in nets.get(view.split(":", 1)[1], []) if c["ticker"] in enabled]
    else:
        items = sorted(enabled.values(), key=lambda x: x["name"])
    per_page = 12
    tpages = max(1, (len(items) + per_page - 1) // per_page)
    page = max(0, min(int(page), tpages - 1))
    sl = items[page * per_page:(page + 1) * per_page]
    for i in range(0, len(sl), 2):
        rows.append([{"text": c["name"], "callback_data": f"cryptopay|{offer_key}|{c['ticker']}", "style": "success"} for c in sl[i:i + 2]])
    nav = []
    if page > 0:
        nav.append({"text": "⬅️ Prev", "callback_data": f"cryptolist|{offer_key}|{view}|{page - 1}"})
    if page < tpages - 1:
        nav.append({"text": "Next ➡️", "callback_data": f"cryptolist|{offer_key}|{view}|{page + 1}"})
    if nav:
        rows.append(nav)
    rows.append([{"text": "⬅️ Crypto Menu", "callback_data": f"cryptolist|{offer_key}|home|0"}])
    await edit_or_send(chat_id, message_id, f"🪙 <b>SELECT COIN</b> (Page {page + 1}/{tpages}) — ${usd_amt:,.2f} USD", {"inline_keyboard": rows})


async def start_cryptapi_payment(user_id, chat_id, offer_key, ticker):
    offer = resolve_offer(offer_key)
    if not offer or offer["scope"] != "INTL":
        await send_message(chat_id, "⚠️ International item unavailable.")
        return
    ticker = ticker.lower().strip()
    addr_out = get_wallet_for_ticker(ticker)
    if not addr_out:
        await send_message(chat_id, "⚠️ No wallet configured for this coin.")
        return
    coins, _ = await fetch_cryptapi_catalog()
    cmeta = coins.get(ticker, {"name": ticker.upper(), "min_coin": 0.0})
    usd_amt = float(offer["price_usd"])
    client = await get_http()
    try:
        cv = (await client.get(f"{CRYPTAPI_URL}/{ticker}/convert/", params={"value": f"{usd_amt:.2f}", "from": "USD"}, timeout=15)).json()
        val_coin = float(cv["value_coin"])
    except Exception as e:
        await send_message(chat_id, f"⚠️ Conversion failed: <code>{html_escape(str(e))}</code>")
        return
    mn = float(cmeta.get("min_coin") or 0)
    if mn > 0 and val_coin < mn:
        await send_message(chat_id, f"⚠️ Below minimum for <b>{html_escape(cmeta['name'])}</b> ({val_coin:.8f} &lt; {mn}). Try <b>USDT TRC20/BEP20/Solana</b> or <b>LTC/TRX/DOGE</b>.", {"inline_keyboard": [[{"text": "🔄 Choose Another Coin", "callback_data": f"cryptolist|{offer_key}|home|0", "style": "primary"}]]})
        return

    ext_ref, nonce = "CRY" + secrets.token_hex(6).upper(), secrets.token_hex(12)
    confs = int(get_setting("cryptapi_confirmations", "1") or "1")
    cb_url = build_cryptapi_callback_url(ext_ref, nonce)
    try:
        cr = (await client.get(f"{CRYPTAPI_URL}/{ticker}/create/", params={"callback": cb_url, "address": addr_out, "pending": "1", "confirmations": str(confs), "post": "1"}, timeout=18)).json()
        if cr.get("status") == "error" or not cr.get("address_in"):
            raise RuntimeError(cr.get("error") or str(cr))
    except Exception as e:
        if "verify your domain" in str(e).lower():
            asyncio.create_task(send_message(ADMIN_USER_ID, f"🚨 Verify domain <code>{html_escape(get_cryptapi_callback_base())}</code> at https://cryptapi.io/verify_domain/"))
        await send_message(chat_id, f"⚠️ Could not create deposit address: <code>{html_escape(str(e)[:180])}</code>")
        return

    addr_in = cr["address_in"]
    coin_str = f"{val_coin:.8f}".rstrip("0").rstrip(".")
    if "." not in coin_str:
        coin_str += ".0"
    with connect_db() as db:
        db.execute("INSERT INTO orders(external_ref,user_id,plan,amount,amount_usd,market_scope,currency,local_amount,phone,provider,status,crypto_ticker,crypto_address_in,crypto_amount,callback_nonce,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (ext_ref, user_id, offer["key"], 0, usd_amt, "INTL", "USD", f"${usd_amt:.2f} ({coin_str} {cmeta['name']})", ticker, "crypto", "PENDING", ticker, addr_in, coin_str, nonce, utcnow().isoformat()))

    cap = f"🪙 <b>PAY WITH {html_escape(cmeta['name'].upper())}</b>\n🎫 <b>{html_escape(offer['title'])}</b> — <b>${usd_amt:.2f} USD</b>\n💰 Send EXACT: <code>{coin_str}</code> <b>{html_escape(cmeta['name'])}</b>\n📥 Address:\n<code>{html_escape(addr_in)}</code>\n🔗 Ref: <code>{ext_ref}</code> | Min: <code>{cr.get('minimum_transaction_coin') or mn}</code>\n⚡ <i>Auto-activates after {confs} confirmation(s).</i>"
    kb = {"inline_keyboard": [[{"text": "🔄 Check Crypto Payment", "callback_data": f"cryptocheck:{ext_ref}", "style": "success"}], [{"text": "🔄 Switch Coin", "callback_data": f"cryptolist|{offer_key}|home|0", "style": "primary"}], [{"text": "🏠 Main Menu", "callback_data": "menu:home"}]]}
    try:
        qr_res = (await client.get(f"{CRYPTAPI_URL}/{ticker}/qrcode/", params={"address": addr_in, "value": coin_str, "size": "512"}, timeout=15)).json()
        await telegram_send_photo_bytes(chat_id, f"{ext_ref}.png", base64.b64decode(qr_res["qr_code"]), caption=cap, keyboard=kb)
    except Exception:
        await send_message(chat_id, cap, kb)


async def process_cryptapi_event(order, data):
    ext_ref = order["external_ref"]
    pending = str(data.get("pending", "0")) in ("1", "true", "True")
    txid, tx_uuid = data.get("txid_in") or "", data.get("uuid") or ""
    try:
        paid = float(data.get("value_coin") or 0)
    except (ValueError, TypeError):
        paid = 0.0
    try:
        exp = float(order["crypto_amount"] or 0)
    except (ValueError, TypeError):
        exp = 0.0
    if pending:
        with connect_db() as db:
            db.execute("UPDATE orders SET crypto_txid=COALESCE(?, crypto_txid) WHERE external_ref=?", (txid or None, ext_ref))
        try:
            await send_message(int(order["user_id"]), f"⏳ <b>CRYPTO DETECTED!</b> Ref: <code>{ext_ref}</code>\nWaiting for blockchain confirmation...")
        except Exception:
            pass
        return "PENDING_CONFIRMATION"
    if exp > 0 and paid > 0 and paid < exp * 0.97:
        with connect_db() as db:
            db.execute("UPDATE orders SET status='PENDING_REVIEW', crypto_txid=?, crypto_uuid=?, mpesa_receipt=? WHERE external_ref=? AND status!='PAID'", (txid, tx_uuid, txid, ext_ref))
        asyncio.create_task(send_message(ADMIN_USER_ID, f"⚠️ <b>UNDERPAID CRYPTO</b> {ext_ref} Exp:{exp} Got:{paid}", {"inline_keyboard": [[{"text": "✅ Approve", "callback_data": f"admin:approve:{ext_ref}", "style": "success"}, {"text": "❌ Reject", "callback_data": f"admin:reject:{ext_ref}", "style": "danger"}]]}))
        return "UNDERPAID"
    with connect_db() as db:
        db.execute("UPDATE orders SET crypto_txid=?, crypto_uuid=?, mpesa_receipt=? WHERE external_ref=?", (txid, tx_uuid, txid or f"CRYPTO-{ext_ref}", ext_ref))
        fresh = db.execute("SELECT * FROM orders WHERE external_ref=?", (ext_ref,)).fetchone()
    await finalize_paid_order(fresh, callback_data={"MpesaReceiptNumber": txid or f"CRYPTO-{ext_ref}"}, manual_admin=True)
    return "PAID"


async def verify_cryptapi_order_via_logs(order):
    if not order["crypto_ticker"] or not order["callback_nonce"]:
        return "NOT_CRYPTO"
    r = await (await get_http()).get(f"{CRYPTAPI_URL}/{order['crypto_ticker']}/logs/", params={"callback": build_cryptapi_callback_url(order["external_ref"], order["callback_nonce"])}, timeout=15)
    if r.status_code != 200:
        return "NO_TX"
    cbs = r.json().get("callbacks") or []
    if not cbs:
        return "NO_TX"
    conf = [c for c in cbs if str(c.get("pending", "1")) in ("0", "false", "False")]
    return await process_cryptapi_event(order, conf[-1] if conf else cbs[-1])


async def send_offer_ticket_media(user_id, offer_key, title, receipt=None):
    rows = get_offer_media(offer_key)
    if not rows:
        return 0
    rtxt = f"\n🧾 Receipt: <code>{html_escape(receipt)}</code>" if receipt else ""
    sent = 0
    for idx, m in enumerate(rows):
        cap = m["caption"].strip() if m["caption"] else f"🎫 <b>{html_escape(title)}</b> — Confirmed!{rtxt if idx == 0 else ''}"
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
        days, display_title, is_lt = int(days_override), title_override or f"{days_override}-Day VIP", 1 if is_lifetime_override else 0
    else:
        offer = resolve_offer(plan_name)
        if not offer:
            return
        days, display_title, is_lt = int(offer["days"]), title_override or offer["title"], int(offer["is_lifetime"])

    contact_url = get_setting("admin_contact_url", f"https://t.me/{BRAND_TAG.lstrip('@')}")
    rline = f"🧾 Receipt: <code>{html_escape(receipt)}</code>\n" if receipt else ""
    dm = await send_offer_ticket_media(user_id, plan_name, display_title, receipt=receipt)

    if days <= 0 and not is_lt:
        note = f"🖼️ Delivered <b>{dm}</b> ticket image(s) above!" if dm > 0 else f"✅ Payment confirmed! Admin ({BRAND_TAG}) notified."
        await send_message(user_id, f"🎉 <b>PAYMENT CONFIRMED!</b>\n👑 <b>{PROJECT_NAME}</b>\n🎫 <b>{html_escape(display_title)}</b>\n{rline}\n{note}", {"inline_keyboard": [[{"text": f"💬 Contact {BRAND_TAG}", "url": contact_url, "style": "success"}], [{"text": "🏠 Main Menu", "callback_data": "menu:home", "style": "primary"}]]})
        return

    with connect_db() as db:
        ex = db.execute("SELECT * FROM subscriptions WHERE user_id=?", (user_id,)).fetchone()
    now = utcnow()
    if is_lt:
        expiry = datetime(2099, 12, 31, 23, 59, tzinfo=timezone.utc)
    else:
        start = max(now, parse_utc(ex["expires_at"])) if (ex and ex["status"] == "ACTIVE" and not ex["is_lifetime"]) else now
        expiry = start + timedelta(days=days)
    with connect_db() as db:
        db.execute("INSERT INTO subscriptions(user_id,plan,expires_at,status,is_lifetime,reminded) VALUES(?,?,?,'ACTIVE',?,0) ON CONFLICT(user_id) DO UPDATE SET plan=excluded.plan, expires_at=excluded.expires_at, status='ACTIVE', is_lifetime=excluded.is_lifetime, reminded=0", (user_id, plan_name, expiry.isoformat(), is_lt))

    vip_chat = get_setting("vip_channel_id", VIP_CHANNEL_ID)
    try:
        await telegram("unbanChatMember", {"chat_id": vip_chat, "user_id": user_id, "only_if_banned": True})
    except Exception:
        pass
    prefs = get_user_prefs(user_id)
    try:
        inv_p = {"chat_id": vip_chat, "member_limit": 1, "name": f"Prime Tips {user_id}"}
        if not is_lt:
            inv_p["expire_date"] = int(expiry.timestamp())
        inv = await telegram("createChatInviteLink", inv_p)
        exp_txt = "Never (Lifetime)" if is_lt else format_local_time(expiry, prefs["tz"])
        mline = f"🖼️ Sent <b>{dm}</b> ticket image(s) above!\n" if dm > 0 else ""
        await send_message(user_id, f"🎉 <b>VIP ACTIVATED!</b>\n👑 <b>{PROJECT_NAME}</b>\n💎 <b>{html_escape(display_title)}</b>\n{rline}{mline}📅 Expires: <b>{exp_txt}</b>", {"inline_keyboard": [[{"text": "👑 ENTER VIP CHANNEL", "url": inv["invite_link"], "style": "danger"}], [{"text": "🏠 Main Menu", "callback_data": "menu:home", "style": "primary"}]]})
    except Exception:
        await send_message(ADMIN_USER_ID, f"⚠️ VIP activated for <code>{user_id}</code>, invite failed.")


async def reward_referrer_if_eligible(payer_id):
    p = get_user(payer_id)
    if not p or not p["referred_by"] or p["referral_rewarded"]:
        return
    rid = int(p["referred_by"])
    if rid == int(payer_id) or not get_user(rid):
        return
    with connect_db() as db:
        if db.execute("UPDATE users SET referral_rewarded=1 WHERE user_id=? AND referral_rewarded=0", (int(payer_id),)).rowcount == 0:
            return
    rd = int(get_setting("referral_reward_days", "2") or "2")
    if rd > 0:
        try:
            await give_vip_access(rid, f"referral-{rd}d", days_override=rd, title_override=f"🎁 Referral Bonus (+{rd}d VIP)")
        except Exception:
            pass


def _extract_payhero_status(v):
    if not isinstance(v, dict):
        return ""
    n = v.get("data") if isinstance(v.get("data"), dict) else {}
    return str(v.get("status") or v.get("transaction_status") or v.get("Status") or n.get("status") or n.get("transaction_status") or "").upper().strip()


async def finalize_paid_order(order, verified=None, callback_data=None, manual_admin=False):
    ext_ref = order["external_ref"]
    if not manual_admin:
        st = _extract_payhero_status(verified)
        cb_st = str((callback_data or {}).get("Status") or (callback_data or {}).get("status") or "").upper()
        cb_rc = (callback_data or {}).get("ResultCode")
        if st in ("FAILED", "CANCELLED", "CANCELED", "EXPIRED") or (cb_rc not in (None, 0, "0") and cb_st in ("FAILED", "CANCELLED", "CANCELED", "")):
            with connect_db() as db:
                if db.execute("UPDATE orders SET status='FAILED' WHERE external_ref=? AND status='PENDING'", (ext_ref,)).rowcount > 0:
                    till = get_setting("mpesa_till_number", DEFAULT_MPESA_TILL)
                    asyncio.create_task(send_message(int(order["user_id"]), f"❌ <b>STK Push failed/cancelled.</b>\nPay via Till <code>{till}</code>:", {"inline_keyboard": [[{"text": f"🏪 Pay via Till {till}", "callback_data": f"manualtill:{ext_ref}", "style": "success"}], [{"text": "🏠 Main Menu", "callback_data": "menu:home"}]]}))
            return "FAILED"
        if st not in ("SUCCESS", "COMPLETED", "PAID"):
            return "PENDING"
        if callback_data and callback_data.get("Amount") is not None:
            try:
                if int(float(callback_data["Amount"])) != int(order["amount"]):
                    return "PENDING"
            except (ValueError, TypeError):
                return "PENDING"

    vn = (verified or {}).get("data") if isinstance((verified or {}).get("data"), dict) else {}
    rcpt = (callback_data or {}).get("MpesaReceiptNumber") or (callback_data or {}).get("MPESA_Reference") or (verified or {}).get("provider_reference") or (verified or {}).get("transaction_reference") or vn.get("provider_reference") or order["mpesa_receipt"]
    with connect_db() as db:
        if db.execute("UPDATE orders SET status='PAID', mpesa_receipt=COALESCE(?, mpesa_receipt) WHERE external_ref=? AND status IN ('PENDING','PENDING_PROOF','PENDING_REVIEW')", (rcpt, ext_ref)).rowcount == 0:
            return "ALREADY_PAID"

    offer = resolve_offer(order["plan"])
    title = offer["title"] if offer else format_order_item_name(order["plan"])
    if offer and offer["kind"] == "button":
        await give_vip_access(int(order["user_id"]), order["plan"], days_override=offer["days"], receipt=rcpt, title_override=title)
    else:
        await give_vip_access(int(order["user_id"]), order["plan"], receipt=rcpt, title_override=title)
    await reward_referrer_if_eligible(int(order["user_id"]))
    amt_d = order["local_amount"] or (f"${order['amount_usd']:.2f} USD" if order["market_scope"] == "INTL" else f"KES {order['amount']}")
    await send_message(ADMIN_USER_ID, f"✅ <b>PAYMENT CONFIRMED</b>\nUser: <code>{order['user_id']}</code> | Scope: <b>{order['market_scope']}</b>\nItem: <b>{html_escape(title)}</b> ({html_escape(amt_d)})\nReceipt: <code>{html_escape(rcpt or 'N/A')}</code>")
    return "PAID"


async def process_payhero_callback(payload):
    data = payload.get("response") if isinstance(payload.get("response"), dict) else payload
    ext_ref = data.get("ExternalReference") or data.get("external_reference") or payload.get("external_reference")
    cid = data.get("CheckoutRequestID") or data.get("checkout_request_id") or payload.get("CheckoutRequestID")
    ph_ref = data.get("reference") or payload.get("reference")
    order = None
    with connect_db() as db:
        if ext_ref:
            order = db.execute("SELECT * FROM orders WHERE external_ref=?", (ext_ref,)).fetchone()
        if not order and cid:
            order = db.execute("SELECT * FROM orders WHERE checkout_id=?", (cid,)).fetchone()
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


async def handle_payment_proof_submission(user_id, chat_id, ext_ref, text, photo_fid, doc_fid=None):
    with connect_db() as db:
        order = db.execute("SELECT * FROM orders WHERE external_ref=? AND user_id=?", (ext_ref, user_id)).fetchone()
    if not order or order["status"] == "PAID":
        await send_message(chat_id, "✅ Already processed or order not found.", main_menu_keyboard(user_id))
        return
    mfid = photo_fid or doc_fid
    mkind = "photo" if photo_fid else ("document" if doc_fid else "text")
    if not mfid and not text:
        await send_message(chat_id, "⚠️ Send a payment screenshot, PDF, or transaction code.")
        return
    with connect_db() as db:
        db.execute("UPDATE orders SET status='PENDING_REVIEW', proof_file_id=COALESCE(?, proof_file_id), proof_type=?, mpesa_receipt=? WHERE external_ref=?", (mfid, mkind, text[:120] if text else mkind.upper(), ext_ref))
        order = db.execute("SELECT * FROM orders WHERE external_ref=?", (ext_ref,)).fetchone()
    auto = get_setting("auto_confirm_screenshot", "0") == "1" and bool(mfid)
    cap = f"📸 <b>PROOF ({order['market_scope']}/{mkind.upper()})</b>\nUser: <code>{user_id}</code> | <b>{html_escape(format_order_item_name(order['plan']))}</b>\nAmt: <b>{html_escape(order['local_amount'] or str(order['amount']))}</b> | Ref: <code>{ext_ref}</code>" + (f"\nNote: <code>{html_escape(text[:150])}</code>" if text else "")
    kb = None if auto else {"inline_keyboard": [[{"text": "✅ Confirm & Deliver", "callback_data": f"admin:approve:{ext_ref}", "style": "success"}, {"text": "❌ Reject", "callback_data": f"admin:reject:{ext_ref}", "style": "danger"}], [{"text": "💬 Reply", "callback_data": f"admin:reply:{user_id}", "style": "primary"}]]}
    if photo_fid:
        await telegram("sendPhoto", {"chat_id": ADMIN_USER_ID, "photo": photo_fid, "caption": cap[:1020], "parse_mode": "HTML", **({"reply_markup": kb} if kb else {})})
    elif doc_fid:
        await telegram("sendDocument", {"chat_id": ADMIN_USER_ID, "document": doc_fid, "caption": cap[:1020], "parse_mode": "HTML", **({"reply_markup": kb} if kb else {})})
    else:
        await send_message(ADMIN_USER_ID, cap, kb)
    if auto:
        await finalize_paid_order(order, manual_admin=True)
    else:
        await send_message(chat_id, f"✅ <b>PROOF RECEIVED!</b> Ref: <code>{ext_ref}</code>\n⏳ Delivery is automatic once confirmed!", {"inline_keyboard": [[{"text": "🏠 Main Menu", "callback_data": "menu:home"}]]})


def format_match_block(m, i=None):
    b = {"PENDING": "⏳ PENDING", "WON": "✅ WON", "LOST": "❌ LOST", "VOID": "⚪ VOID"}
    pre = f"<b>#{i}</b> " if i else ""
    return f"{pre}⚽ <b>{html_escape(m['match_name'])}</b>\n🎯 {html_escape(m['market'])}: <b>{html_escape(m['tip'])}</b> | 📈 <b>{html_escape(m['odds'])}</b>\n🗓 {html_escape(m['match_date'])} ⏰ {html_escape(m['kickoff_time'])} • {b.get(m['result'], m['result'])}\n"


def render_batch_text(tier, rows):
    hdr = f"💎 <b>{PROJECT_NAME} — VIP PICKS</b>\n━━━━━━━━━━━━━━━━━━━━\n\n" if tier == "VIP" else f"⚽ <b>{PROJECT_NAME} — TODAY'S PREDICTIONS</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    body = "\n".join(format_match_block(m, i + 1) for i, m in enumerate(rows))
    return hdr + body + f"\n━━━━━━━━━━━━━━━━━━━━\n🎯 {html_escape(get_setting('vip_odds_info'))}\n🔥 <b>{PROJECT_NAME} ({BRAND_TAG})</b>", body


async def sync_channel_message_for_match(match_id):
    with connect_db() as db:
        m = db.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
        if not m or not m["channel_msg_id"]:
            return
        rows = db.execute("SELECT * FROM matches WHERE channel_msg_id=? ORDER BY id", (m["channel_msg_id"],)).fetchall()
    if not rows:
        return
    tc = get_setting("vip_channel_id", VIP_CHANNEL_ID) if m["tier"] == "VIP" else get_setting("main_channel_id", CHANNEL_ID)
    txt, _ = render_batch_text(m["tier"], rows)
    try:
        await telegram("editMessageText", {"chat_id": tc, "message_id": m["channel_msg_id"], "parse_mode": "HTML", "text": txt[:4090], "reply_markup": channel_post_keyboard(channel_msg_id=m["channel_msg_id"], include_reactions=(m["tier"] == "FREE")), "disable_web_page_preview": True})
    except Exception:
        pass


async def publish_match_batch(tier, selected_btn_ids=None, include_footer=True, include_reactions=True):
    await ensure_bot_username()
    with connect_db() as db:
        rows = db.execute("SELECT * FROM matches WHERE tier=? AND publish_status='QUEUED' ORDER BY id", (tier,)).fetchall()
    if not rows:
        return None
    tc = get_setting("vip_channel_id", VIP_CHANNEL_ID) if tier == "VIP" else get_setting("main_channel_id", CHANNEL_ID)
    txt, _ = render_batch_text(tier, rows)
    sr = bool(include_reactions) and (tier == "FREE")
    msg = await telegram("sendMessage", {"chat_id": tc, "parse_mode": "HTML", "text": txt[:4090], "reply_markup": channel_post_keyboard(selected_btn_ids=selected_btn_ids or [], include_footer=include_footer, include_reactions=False), "disable_web_page_preview": True})
    mid = msg["message_id"]
    save_channel_post_meta(mid, selected_btn_ids or [], sr, include_footer)
    with connect_db() as db:
        for r in rows:
            db.execute("UPDATE matches SET publish_status='PUBLISHED', channel_msg_id=? WHERE id=?", (mid, r["id"]))
    if sr:
        try:
            await telegram("editMessageReplyMarkup", {"chat_id": tc, "message_id": mid, "reply_markup": channel_post_keyboard(channel_msg_id=mid)})
        except Exception:
            pass
    return msg


async def publish_vip_post(selected_btn_ids=None, include_footer=True, include_reactions=True):
    await ensure_bot_username()
    mc = get_setting("main_channel_id", CHANNEL_ID)
    msg = await telegram("sendMessage", {"chat_id": mc, "parse_mode": "HTML", "text": f"👑 <b>{PROJECT_NAME}</b> ({BRAND_TAG})\n\n🎯 {html_escape(get_setting('vip_odds_info'))}\n\n🌍 Local M-Pesa/Airtel & International USD/Crypto supported.\n\n👇 <b>TAP BELOW TO JOIN VIP OR GET A TICKET</b>", "reply_markup": channel_post_keyboard(selected_btn_ids=selected_btn_ids or [], include_footer=include_footer, include_reactions=False)})
    mid = msg["message_id"]
    save_channel_post_meta(mid, selected_btn_ids or [], include_reactions, include_footer)
    if include_reactions:
        try:
            await telegram("editMessageReplyMarkup", {"chat_id": mc, "message_id": mid, "reply_markup": channel_post_keyboard(channel_msg_id=mid)})
        except Exception:
            pass
    return msg


async def publish_custom_main_post(text_body="", photo_file_id=None, selected_btn_ids=None, include_footer=True, include_reactions=True):
    await ensure_bot_username()
    mc = get_setting("main_channel_id", CHANNEL_ID)
    fmt = f"{html_escape(text_body)}\n\n🔥 <b>{PROJECT_NAME}</b> ({BRAND_TAG})" if text_body else f"🔥 <b>{PROJECT_NAME}</b> ({BRAND_TAG})"
    kb = channel_post_keyboard(selected_btn_ids=selected_btn_ids or [], include_footer=include_footer, include_reactions=False)
    msg = await telegram("sendPhoto", {"chat_id": mc, "photo": photo_file_id, "caption": fmt[:1020], "parse_mode": "HTML", **({"reply_markup": kb} if kb else {})}) if photo_file_id else await telegram("sendMessage", {"chat_id": mc, "parse_mode": "HTML", "text": fmt[:4090], "disable_web_page_preview": True, **({"reply_markup": kb} if kb else {})})
    mid = msg["message_id"]
    save_channel_post_meta(mid, selected_btn_ids or [], include_reactions, include_footer)
    if include_reactions:
        try:
            await telegram("editMessageReplyMarkup", {"chat_id": mc, "message_id": mid, "reply_markup": channel_post_keyboard(channel_msg_id=mid)})
        except Exception:
            pass
    return msg


async def publish_vip_channel_post(text_body="", photo_file_id=None):
    vc = get_setting("vip_channel_id", VIP_CHANNEL_ID)
    fmt = f"💎 <b>{PROJECT_NAME} — VIP EXCLUSIVE</b> ({BRAND_TAG})\n\n{html_escape(text_body)}" if text_body else f"💎 <b>{PROJECT_NAME} — VIP EXCLUSIVE</b>"
    return await telegram("sendPhoto", {"chat_id": vc, "photo": photo_file_id, "caption": fmt[:1020], "parse_mode": "HTML"}) if photo_file_id else await telegram("sendMessage", {"chat_id": vc, "text": fmt[:4090], "parse_mode": "HTML", "disable_web_page_preview": True})


async def publish_accumulator_to_channel(accum_id, target="MAIN"):
    await ensure_bot_username()
    with connect_db() as db:
        a = db.execute("SELECT * FROM accumulators WHERE id=?", (int(accum_id),)).fetchone()
    if not a:
        return None
    sels = json.loads(a["selections_json"]) if a["selections_json"] else []
    legs = "\n".join(f"<b>{i + 1}.</b> ⚽ <b>{html_escape(s.get('match', ''))}</b> — <b>{html_escape(s.get('tip', ''))}</b> (<code>{html_escape(str(s.get('odds', '')))}</code>)" for i, s in enumerate(sels))
    is_vip = (target.upper() == "VIP")
    tc = get_setting("vip_channel_id", VIP_CHANNEL_ID) if is_vip else get_setting("main_channel_id", CHANNEL_ID)
    msg = await telegram("sendMessage", {"chat_id": tc, "parse_mode": "HTML", "text": f"🎯 <b>{'VIP ' if is_vip else ''}ACCUMULATOR #{a['id']}</b> ({html_escape(str(a['total_odds']))}x)\n\n{legs}\n\n🔥 <b>{PROJECT_NAME} ({BRAND_TAG})</b>", "reply_markup": channel_post_keyboard(selected_btn_ids=[], include_footer=not is_vip, include_reactions=False), "disable_web_page_preview": True})
    mid = msg["message_id"]
    save_channel_post_meta(mid, [], not is_vip, not is_vip)
    if not is_vip:
        try:
            await telegram("editMessageReplyMarkup", {"chat_id": tc, "message_id": mid, "reply_markup": channel_post_keyboard(channel_msg_id=mid)})
        except Exception:
            pass
    with connect_db() as db:
        db.execute("UPDATE accumulators SET status=?, channel_msg_id=? WHERE id=?", ("PUBLISHED_VIP" if is_vip else "PUBLISHED_MAIN", mid, int(accum_id)))
    return msg


async def show_country_welcome(chat_id):
    await send_message(chat_id, "🌍 <b>Welcome to Prime Tips Empire!</b>\n\nChoose your country/region to load the right plans & payment methods:", country_welcome_keyboard())


async def show_main_menu(chat_id, user_id):
    prefs = get_user_prefs(user_id)
    ci = COUNTRIES.get(prefs["country"])
    cl = f"{ci['flag']} {ci['name']} ({prefs['currency']})" if (prefs["confirmed"] and ci) else f"Not set ({prefs['currency']})"
    vb = "💎 <b>VIP ACTIVE</b>" if has_active_vip(user_id) else "🆓 <b>FREE MEMBER</b>"
    await send_message(chat_id, f"👑 <b>{PROJECT_NAME}</b> • {BRAND_TAG}\n━━━━━━━━━━━━━━━━━━━━━━\nStatus: {vb} | {cl}\n🎯 {html_escape(get_setting('vip_odds_info'))}", main_menu_keyboard(user_id))


async def show_ticket_button_checkout(chat_id, user_id, btn_id, force_scope=None):
    b = get_custom_button(btn_id)
    if not b or not b["is_active"]:
        await send_message(chat_id, "⚠️ Ticket unavailable.", main_menu_keyboard(user_id))
        return
    curl = get_setting("admin_contact_url", f"https://t.me/{BRAND_TAG.lstrip('@')}")
    if b["button_type"] == "URL" and b["url"]:
        await send_message(chat_id, f"🔗 <b>{html_escape(b['label'])}</b>", {"inline_keyboard": [[{"text": b["label"], "url": b["url"], "style": "primary"}], [{"text": "🏠 Main Menu", "callback_data": "menu:home"}]]})
        return
    prefs = get_user_prefs(user_id)
    use_intl = (force_scope == "INTL") if force_scope else prefs["is_intl"]
    img_cnt = count_offer_media("btn:" + str(b["id"]))
    dline = f"⏳ Access: <b>{b['days']}d VIP</b>\n" if int(b["days"]) > 0 else "🎟️ Instant Ticket Delivery\n"
    iline = f"🖼️ Auto-Delivery: <b>{img_cnt} Image(s)</b>\n" if img_cnt > 0 else ""

    if use_intl:
        iusd = float(b["intl_price_usd"] or 0)
        kb = []
        if iusd > 0:
            pline, _ = format_usd_price(iusd, prefs["currency"] if prefs["is_intl"] else "USD")
            kb.append([{"text": "🪙 Pay with Crypto (CryptAPI — 100+ Coins)", "callback_data": f"buyibtn:{b['id']}:crypto", "style": "success"}])
            kb.append([{"text": "💳 International Card / PayPal / Transfer", "callback_data": f"buyibtn:{b['id']}:intl_transfer", "style": "primary"}])
        else:
            pline = "Contact Admin for International Price"
            kb.append([{"text": f"💬 Contact {BRAND_TAG}", "url": curl, "style": "success"}])
        kb.append([{"text": "🇰🇪 Switch to East Africa Local Price (KES)", "callback_data": f"openbtn:{b['id']}"}])
        kb.append([{"text": "🏠 Main Menu", "callback_data": "menu:home"}])
        await send_message(chat_id, f"🌍 <b>{html_escape(b['label'])} — INTERNATIONAL</b>\n💵 Price: <b>{html_escape(pline)}</b>\n{dline}{iline}", {"inline_keyboard": kb})
        return

    price = int(b["price"])
    till, airtel = get_setting("mpesa_till_number", DEFAULT_MPESA_TILL), get_setting("airtel_money_number", DEFAULT_AIRTEL_NUMBER)
    pline = format_converted_price(price, prefs["currency"])[0] if (price > 0 and prefs["currency"] in ("TZS", "UGX", "ZMW")) else f"KES {price:,}"
    kb = []
    if price > 0:
        kb.append([{"text": f"📲 Kenya: M-Pesa STK Push (KES {price:,})", "callback_data": f"buybtn:{b['id']}:mpesa", "style": "success"}])
        kb.append([{"text": f"🏪 Kenya: Manual M-Pesa Till {till}", "callback_data": f"buybtn:{b['id']}:mpesa_till", "style": "primary"}])
        kb.append([{"text": f"🔴 TZ/UG/ZM: Airtel Money ({airtel})", "callback_data": f"buybtn:{b['id']}:airtel", "style": "danger"}])
    else:
        kb.append([{"text": f"💬 Contact {BRAND_TAG}", "url": curl, "style": "success"}])
    kb.append([{"text": "🌍 Outside East Africa? Pay International ($ / Crypto)", "callback_data": f"openibtn:{b['id']}", "style": "primary"}])
    kb.append([{"text": "🏠 Main Menu", "callback_data": "menu:home"}])
    await send_message(chat_id, f"🎫 <b>{html_escape(b['label'])} — LOCAL</b>\n💰 Price: <b>{html_escape(pline if price > 0 else 'Contact Admin')}</b>\n{dline}{iline}", {"inline_keyboard": kb})


async def show_accumulator_builder(chat_id, user_id):
    sess = get_session(user_id)
    draft = json.loads(sess["payload"]) if (sess and sess["state"] == "ACCUM_BUILD") else {"category": "3-5 Odds", "match_ids": []}
    if not sess or sess["state"] != "ACCUM_BUILD":
        set_session(user_id, "ACCUM_BUILD", json.dumps(draft))
    with connect_db() as db:
        avail = db.execute("SELECT * FROM matches WHERE publish_status='PUBLISHED' AND result='PENDING' ORDER BY id DESC LIMIT 8").fetchall()
    sel = [m for m in avail if m["id"] in draft["match_ids"]]
    to = 1.0
    for m in sel:
        try:
            to *= float(m["odds"])
        except ValueError:
            pass
    rows = [[{"text": ("✅ " if draft["category"] == c else "") + c.replace(" Odds", ""), "callback_data": f"accum:cat:{c}"} for c in ACCUM_CATEGORIES[:3]], [{"text": ("✅ " if draft["category"] == c else "") + c.replace(" Odds", ""), "callback_data": f"accum:cat:{c}"} for c in ACCUM_CATEGORIES[3:]]]
    for m in avail:
        pk = m["id"] in draft["match_ids"]
        rows.append([{"text": f"{'✅' if pk else '➕'} {m['match_name']} | {m['tip']} ({m['odds']})", "callback_data": f"accum:toggle:{m['id']}", "style": "success" if pk else "primary"}])
    if sel:
        rows.append([{"text": "🎟️ Submit Ticket", "callback_data": "accum:save", "style": "danger"}, {"text": "🗑 Clear", "callback_data": "accum:clear"}])
    rows.append([{"text": "🏠 Main Menu", "callback_data": "menu:home"}])
    stxt = "\n".join(f"• <b>{html_escape(m['match_name'])}</b> — {html_escape(m['tip'])} ({m['odds']})" for m in sel) or "<i>Tap matches below to add legs.</i>"
    await send_message(chat_id, f"🎯 <b>ACCUMULATOR BUILDER</b>\nCategory: <b>{html_escape(draft['category'])}</b> | Odds: <b>{to:.2f}x</b>\n\n{stxt}", {"inline_keyboard": rows})


async def show_vip_screen(chat_id, user_id, force_scope=None):
    prefs = get_user_prefs(user_id)
    if not prefs["confirmed"] and not force_scope:
        await send_message(chat_id, "🌍 Please select your country/region first:", country_welcome_keyboard())
        return
    use_intl = (force_scope == "INTL") if force_scope else prefs["is_intl"]
    if use_intl:
        plans = get_all_intl_plans(only_active=True)
        dc = prefs["currency"] if prefs["is_intl"] else "USD"
        det = "\n".join(f"🌍 <b>{html_escape(p['title'])}</b> — <b>{format_usd_price(p['price_usd'], dc)[0]}</b>\n<i>   {html_escape(p['benefits'])}</i>" for p in plans)
        await send_message(chat_id, f"🌍 <b>{PROJECT_NAME} — INTERNATIONAL VIP PLANS</b>\n<i>Global USD Pricing • Isolated Crypto & Intl Payments</i>\n\n{det}", intl_plans_keyboard(dc))
        return
    c = COUNTRIES.get(prefs["country"], COUNTRIES["KE"])
    with connect_db() as db:
        plans = db.execute("SELECT * FROM plans ORDER BY price").fetchall()
    det = "\n".join(f"👑 <b>{html_escape(p['title'])}</b> — <b>{format_converted_price(p['price'], prefs['currency'])[0]}</b>\n<i>   {html_escape(p['benefits'])}</i>" for p in plans)
    await send_message(chat_id, f"👑 <b>{PROJECT_NAME} — LOCAL VIP</b> ({c['flag']} {c['name']})\n\n{det}", local_plans_keyboard(prefs["currency"]))


async def show_stats_and_results(chat_id, user_id, only_settled=False):
    with connect_db() as db:
        tot = db.execute("SELECT COUNT(*) AS n FROM matches").fetchone()["n"]
        won = db.execute("SELECT COUNT(*) AS n FROM matches WHERE result='WON'").fetchone()["n"]
        lost = db.execute("SELECT COUNT(*) AS n FROM matches WHERE result='LOST'").fetchone()["n"]
        rows = db.execute("SELECT * FROM matches WHERE result!='PENDING' ORDER BY id DESC LIMIT 12" if only_settled else "SELECT * FROM matches WHERE publish_status='PUBLISHED' ORDER BY id DESC LIMIT 12").fetchall()
    dec = won + lost
    wp = f"{(won / dec * 100):.1f}%" if dec > 0 else "N/A"
    body = "\n".join(format_match_block(m, i + 1) for i, m in enumerate(rows)) if rows else "<i>No records yet.</i>"
    hdr = "🏆 <b>RESULTS</b>" if only_settled else "📊 <b>STATS</b>"
    await send_message(chat_id, f"{hdr} | Win Rate: <b>{wp}</b> (✅{won} ❌{lost} / {tot})\n━━━━━━━━━━━━━━━━━━━━\n\n{body}", {"inline_keyboard": [[{"text": "🏠 Main Menu", "callback_data": "menu:home", "style": "primary"}]]})


async def show_admin_intl_packages(chat_id, message_id=None):
    if str(chat_id) != str(ADMIN_USER_ID):
        return
    plans = get_all_intl_plans(only_active=False)
    buttons = [b for b in get_all_custom_buttons(only_active=False) if b["button_type"] == "TICKET"]
    lines = ["🌍 <b>INTERNATIONAL-ONLY PLANS & PRICES ($ USD)</b>", "━━━━━━━━━━━━━━━━━━━━", "<i>Completely isolated from East Africa KES plans & payments.</i>\n", "<b>VIP International Plans:</b>"]
    rows = []
    for p in plans:
        dur = "Lifetime" if p["is_lifetime"] else f"{p['days']}d"
        pusd = float(p["price_usd"])
        lines.append(f"{'🟢' if p['is_active'] else '⚫'} <b>{html_escape(p['title'])}</b> ({dur}) — <b>${pusd:,.2f} USD</b>")
        rows.append([{"text": f"💵 {p['title']} (${pusd:,.2f})", "callback_data": f"admin:edit_intl_price:{p['name']}", "style": "success"}, {"text": "🗑️", "callback_data": f"admin:del_intl_plan:{p['name']}", "style": "danger"}])
    lines.append("\n<b>Ticket Buttons — International USD Prices:</b>")
    for b in buttons:
        ip = float(b["intl_price_usd"] or 0)
        ip_txt = f"${ip:,.2f} USD" if ip > 0 else "Not set"
        lines.append(f"• {html_escape(b['label'])} — <b>{ip_txt}</b>")
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
        img_c = count_offer_media("btn:" + str(b["id"]))
        ip = float(b["intl_price_usd"] or 0)
        info = "🔗 Link" if b["button_type"] == "URL" else f"KES {int(b['price']):,} | ${ip:,.2f} | 🖼️{img_c}"
        lines.append(f"{dot} <b>{html_escape(b['label'])}</b> — {info}")
        rows.append([{"text": f"⚙️ {b['label']} ({info})", "callback_data": f"admin:btn_view:{b['id']}", "style": b["style"] if b["style"] in BUTTON_STYLES else "primary"}])
    rows.append([{"text": "➕ Add Ticket Button", "callback_data": "admin:btn_add_ticket", "style": "success"}, {"text": "🔗 Add Link Button", "callback_data": "admin:btn_add_url", "style": "primary"}])
    rows.append([{"text": "⬅️ Back to Admin", "callback_data": "admin:home"}])
    await edit_or_send(chat_id, message_id, "🎛️ <b>CHANNEL BUTTONS & TICKETS</b>\n\n" + ("\n".join(lines) if lines else "<i>No buttons yet.</i>"), {"inline_keyboard": rows})


async def show_admin_button_detail(chat_id, btn_id, message_id=None):
    if str(chat_id) != str(ADMIN_USER_ID):
        return
    b = get_custom_button(btn_id)
    if not b:
        await show_admin_buttons(chat_id, message_id)
        return
    if b["button_type"] == "URL":
        rows = [[{"text": "✏️ Rename", "callback_data": f"admin:btn_rename:{b['id']}", "style": "primary"}], [{"text": f"🎨 Color: {b['style'].title()}", "callback_data": f"admin:btn_style:{b['id']}"}], [{"text": "🗑️ Remove", "callback_data": f"admin:btn_del:{b['id']}", "style": "danger"}], [{"text": "⬅️ All Buttons", "callback_data": "admin:buttons"}]]
        await edit_or_send(chat_id, message_id, f"🔗 <b>{html_escape(b['label'])}</b>\nURL: <code>{html_escape(b['url'])}</code>", {"inline_keyboard": rows})
        return
    img_c = count_offer_media("btn:" + str(b["id"]))
    ip = float(b["intl_price_usd"] or 0)
    txt = f"🎫 <b>{html_escape(b['label'])}</b>\n🇰🇪 Local: <b>KES {int(b['price']):,}</b>\n🌍 Intl: <b>${ip:,.2f} USD</b>\n📅 Days: <b>{b['days']}d</b> | 🖼️ Images: <b>{img_c}</b>"
    rows = [
        [{"text": f"🖼️ Ticket Images ({img_c})", "callback_data": f"admin:btn_media:{b['id']}", "style": "success"}],
        [{"text": f"🇰🇪 Local Price (KES {int(b['price']):,})", "callback_data": f"admin:btn_setprice:{b['id']}", "style": "success"}, {"text": f"🌍 Intl Price (${ip:,.2f})", "callback_data": f"admin:btn_setintlprice:{b['id']}", "style": "danger"}],
        [{"text": f"📅 VIP Days ({b['days']}d)", "callback_data": f"admin:btn_setdays:{b['id']}", "style": "primary"}, {"text": "✏️ Rename", "callback_data": f"admin:btn_rename:{b['id']}", "style": "primary"}],
        [{"text": "📝 Description", "callback_data": f"admin:btn_setdesc:{b['id']}"}, {"text": f"🎨 Color: {b['style'].title()}", "callback_data": f"admin:btn_style:{b['id']}"}],
        [{"text": "🗑️ Remove", "callback_data": f"admin:btn_del:{b['id']}", "style": "danger"}, {"text": "⬅️ All Buttons", "callback_data": "admin:buttons"}],
    ]
    await edit_or_send(chat_id, message_id, txt, {"inline_keyboard": rows})


async def show_admin_button_media(chat_id, btn_id, message_id=None):
    if str(chat_id) != str(ADMIN_USER_ID):
        return
    b = get_custom_button(btn_id)
    if not b:
        await show_admin_buttons(chat_id, message_id)
        return
    mr = get_offer_media("btn:" + str(btn_id))
    rows = [[{"text": "➕ Upload Ticket Image(s)", "callback_data": f"admin:btn_media_add:{btn_id}", "style": "success"}]]
    if mr:
        rows.append([{"text": f"👁️ Preview ({len(mr)})", "callback_data": f"admin:btn_media_prev:{btn_id}", "style": "primary"}, {"text": "🗑️ Clear", "callback_data": f"admin:btn_media_clr:{btn_id}", "style": "danger"}])
    rows.append([{"text": "⬅️ Back", "callback_data": f"admin:btn_view:{btn_id}"}])
    await edit_or_send(chat_id, message_id, f"🖼️ <b>TICKET IMAGES — {html_escape(b['label'])}</b>\nSaved: <b>{len(mr)}</b>", {"inline_keyboard": rows})


async def show_post_button_picker(chat_id, user_id, picker, message_id=None):
    if not is_admin(user_id) or str(chat_id) != str(ADMIN_USER_ID):
        return
    set_session(user_id, "POST_PICKER", json.dumps(picker))
    buttons = get_all_custom_buttons(only_active=True)
    sel = set(int(x) for x in picker.get("btn_ids", []))
    mode = picker.get("mode", "custom")
    act = {"custom": "➡️ Next: Write Post / Photo", "vip_promo": "🚀 Publish VIP Promo", "free_matches": "🚀 Publish FREE Matches"}.get(mode, "➡️ Next")
    rows = [[{"text": f"{'✅' if int(b['id']) in sel else '⬜'} {b['label']}", "callback_data": f"postpick:toggle:{b['id']}", "style": "success" if int(b["id"]) in sel else "primary"}] for b in buttons]
    rows.append([{"text": "✅ Select All", "callback_data": "postpick:all", "style": "success"}, {"text": "⬜ Clear", "callback_data": "postpick:none", "style": "primary"}])
    rows.append([{"text": f"👑 Footer: {'ON' if picker.get('footer', True) else 'OFF'}", "callback_data": "postpick:footer"}, {"text": f"🔥 Reacts: {'ON' if picker.get('react', True) else 'OFF'}", "callback_data": "postpick:react"}])
    rows.append([{"text": act, "callback_data": "postpick:next", "style": "success"}])
    rows.append([{"text": "🎛️ Manage Buttons", "callback_data": "admin:buttons"}, {"text": "❌ Cancel", "callback_data": "admin:home", "style": "danger"}])
    await edit_or_send(chat_id, message_id, f"📝 <b>CHOOSE CHANNEL BUTTONS</b> (Selected: <b>{len(sel)}</b>)", {"inline_keyboard": rows})


async def show_admin_cryptapi_panel(chat_id, message_id=None):
    if str(chat_id) != str(ADMIN_USER_ID):
        return
    coins, nets = await fetch_cryptapi_catalog()
    wallets = get_cryptapi_wallets_map()
    en_cnt = sum(1 for t in coins if get_wallet_for_ticker(t))
    lines = ["🪙 <b>CRYPTAPI CRYPTO WALLETS</b>", f"🌐 Callback: <code>{html_escape(get_cryptapi_callback_base())}</code>", f"✅ Enabled: <b>{en_cnt} / {len(coins)}</b> ({len(nets)} networks)\n"]
    rows = []
    for k, _ in CRYPTO_WALLET_GROUPS:
        val = wallets.get(k, "")
        sv = f"<code>{html_escape(val[:8])}...{html_escape(val[-6:])}</code>" if len(val) > 16 else (f"<code>{html_escape(val)}</code>" if val else "<i>Not set</i>")
        lines.append(f"{'✅' if val else '⬜'} <b>{k.upper()}</b>: {sv}")
        rows.append([{"text": f"{'✏️' if val else '➕'} Set {k.upper()} Wallet", "callback_data": f"admin:cry_set:{k}", "style": "success" if val else "primary"}])
    rows.append([{"text": "🎯 Specific Coin Override", "callback_data": "admin:cry_set_custom", "style": "primary"}, {"text": "🔄 Refresh Catalog", "callback_data": "admin:cry_refresh", "style": "success"}])
    rows.append([{"text": "⬅️ Back", "callback_data": "admin:channels"}])
    await edit_or_send(chat_id, message_id, "\n".join(lines), {"inline_keyboard": rows})


async def show_admin_payhero_diag(chat_id, message_id=None):
    if str(chat_id) != str(ADMIN_USER_ID):
        return
    cur_ch = get_setting("payhero_channel_id", PAYHERO_CHANNEL_ID)
    diag = await fetch_payhero_diagnostics()
    ch_res, wal_res = diag["channels"], diag["wallet"]
    lines = ["🩺 <b>PAYHERO DIAGNOSTICS</b>", f"🔢 Channel ID: <code>{html_escape(str(cur_ch))}</code>\n"]
    ws, wd = wal_res.get("status"), wal_res.get("data")
    if ws == 200 and isinstance(wd, dict):
        bal = wd.get("balance") or (wd.get("data") or {}).get("balance")
        lines.append(f"💰 Service Wallet: <b>HTTP 200</b> — <b>KES {html_escape(str(bal if bal is not None else wd))}</b>")
    else:
        lines.append(f"❌ Service Wallet: <b>HTTP {ws}</b> — <code>{html_escape(str(wal_res.get('error') or wd)[:150])}</code>")
    cs, cd = ch_res.get("status"), ch_res.get("data")
    rows = []
    if cs == 200:
        items = cd if isinstance(cd, list) else (cd.get("channels") or cd.get("data") or [] if isinstance(cd, dict) else [])
        lines.append(f"✅ Active Channels (<b>{len(items)}</b>):")
        for ch in items[:10]:
            cid_v, cn = ch.get("id") or ch.get("channel_id"), ch.get("name") or ch.get("channel_type") or "Channel"
            is_c = str(cid_v) == str(cur_ch)
            lines.append(f"  {'✅' if is_c else '•'} ID <code>{cid_v}</code> — {html_escape(str(cn))}")
            if cid_v and not is_c:
                rows.append([{"text": f"🔄 Use Channel {cid_v} ({cn})", "callback_data": f"admin:ph_use_ch:{cid_v}", "style": "success"}])
    else:
        lines.append(f"❌ Channels Auth: <b>HTTP {cs}</b> — <code>{html_escape(str(ch_res.get('error') or cd)[:150])}</code>")
    rows.append([{"text": "🔄 Re-run", "callback_data": "admin:payhero_diag", "style": "primary"}, {"text": "⬅️ Settings", "callback_data": "admin:channels"}])
    await edit_or_send(chat_id, message_id, "\n".join(lines), {"inline_keyboard": rows})


async def handle_join_request(req):
    chat_id = (req.get("chat") or {}).get("id")
    user = req.get("from") or {}
    uid = user.get("id")
    if not chat_id or not uid or user.get("is_bot"):
        return
    register_user(user)
    try:
        await telegram("approveChatJoinRequest", {"chat_id": chat_id, "user_id": uid})
        log_audit(uid, "APPROVE_JOIN", f"chat={chat_id}")
    except Exception as e:
        log.warning("approveChatJoinRequest failed (%s -> %s): %s", uid, chat_id, e)


async def handle_message(message):
    user = message.get("from", {})
    if not user or user.get("is_bot"):
        return
    uid = user.get("id")
    co = message.get("chat", {})
    cid, ctype = co.get("id"), co.get("type", "private")
    if not uid or not cid:
        return
    plist = message.get("photo") or []
    pfid = plist[-1]["file_id"] if plist else None
    dfid = (message.get("document") or {}).get("file_id")
    text = (message.get("text") or message.get("caption") or "").strip()

    ref_by = int(text.split(None, 1)[1][4:]) if (text.startswith("/start ref_") and text.split(None, 1)[1][4:].isdigit()) else None
    register_user(user, referred_by=ref_by)
    du = get_user(uid)
    if du and du["is_blocked"] and not is_admin(uid):
        await send_message(cid, "🚫 Suspended.")
        return

    if message.get("location"):
        sess = get_session(uid)
        if sess and sess["state"] == "WAIT_LOCATION_PERM":
            clear_session(uid)
            det = await reverse_geocode_country(message["location"]["latitude"], message["location"]["longitude"])
            await send_message(cid, "🔍 Detecting...", {"remove_keyboard": True})
            c = COUNTRIES.get(det)
            if c:
                await send_message(cid, f"📍 {c['flag']} <b>{c['name']}</b> ({c['currency']})\nConfirm?", {"inline_keyboard": [[{"text": "✅ Confirm", "callback_data": f"country:confirm:{det}", "style": "success"}], [{"text": "🔄 Change", "callback_data": "country:manual"}]]})
            else:
                await send_message(cid, "Select country:", country_manual_keyboard())
            return

    if text.startswith("/"):
        cmd = text.split()[0].split("@")[0].lower()
        if cmd == "/start":
            clear_session(uid)
            prefs = get_user_prefs(uid)
            arg = text.split(None, 1)[1].strip() if len(text.split(None, 1)) > 1 else ""
            if arg.lower() == "vip":
                await show_vip_screen(cid, uid)
            elif arg.startswith("btn_") and arg[4:].isdigit():
                await show_ticket_button_checkout(cid, uid, int(arg[4:]))
            elif not prefs["confirmed"]:
                await show_country_welcome(cid)
            else:
                await show_main_menu(cid, uid)
            return
        if cmd == "/cancel":
            clear_session(uid)
            await send_message(cid, "🛑 Cancelled.", {"remove_keyboard": True})
            await show_main_menu(cid, uid)
            return
        if cmd in {"/admin", "/done", "/reply", "/addmatch", "/setprice", "/grant", "/revoke", "/export", "/setsetting"}:
            if not is_admin(uid):
                await report_unauthorized_admin_attempt(uid, f"CMD:{cmd}")
                await send_message(cid, "⛔ Access Denied.")
                return
            if not is_admin_private_chat(uid, cid, ctype):
                await send_message(cid, "🔒 Admin commands work in private DM only.")
                return
            if cmd == "/admin":
                await send_message(cid, f"⚡ <b>{PROJECT_NAME} ADMIN</b> ({BRAND_TAG})", admin_keyboard(uid))
            elif cmd == "/done":
                sess = get_session(uid)
                if sess and sess["state"] == "WAIT_BTN_MEDIA":
                    bid = int(sess["payload"])
                    clear_session(uid)
                    await show_admin_button_media(cid, bid)
                else:
                    clear_session(uid)
                    await send_message(cid, "✅ Done.", admin_keyboard(uid))
            elif cmd == "/reply":
                parts = text.split(None, 2)
                if len(parts) >= 3 and parts[1].isdigit():
                    await send_message(int(parts[1]), f"💬 <b>ADMIN ({BRAND_TAG})</b>\n\n{html_escape(parts[2])}")
                    await send_message(cid, "✅ Sent.")
            elif cmd == "/addmatch":
                raw = text.split(None, 1)[1] if len(text.split(None, 1)) > 1 else ""
                p = [x.strip() for x in raw.split("|")]
                if len(p) >= 7 and p[0].upper() in ("FREE", "VIP"):
                    with connect_db() as db:
                        db.execute("INSERT INTO matches(tier,match_name,market,tip,odds,match_date,kickoff_time,publish_status,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (p[0].upper(), p[1], p[2], p[3], p[4], p[5], p[6], "QUEUED", utcnow().isoformat()))
                    await send_message(cid, "✅ Match queued.")
                else:
                    await send_message(cid, "Usage: <code>/addmatch FREE | Team A vs Team B | Market | Tip | 1.85 | 2026-11-16 | 19:45 UTC</code>")
            elif cmd == "/setprice":
                p = text.split()
                if len(p) == 3 and p[2].isdigit() and get_plan(p[1].lower()):
                    with connect_db() as db:
                        db.execute("UPDATE plans SET price=? WHERE name=?", (int(p[2]), p[1].lower()))
                    await send_message(cid, f"✅ Updated {p[1]} to KES {p[2]}.")
            elif cmd == "/grant":
                p = text.split()
                if len(p) == 3 and p[1].isdigit() and p[2].isdigit():
                    await give_vip_access(int(p[1]), f"{p[2]}-day", days_override=int(p[2]))
                    await send_message(cid, "✅ VIP granted.")
            elif cmd == "/revoke":
                p = text.split()
                if len(p) == 2 and p[1].isdigit():
                    tuid = int(p[1])
                    try:
                        await telegram("banChatMember", {"chat_id": get_setting("vip_channel_id", VIP_CHANNEL_ID), "user_id": tuid})
                        await telegram("unbanChatMember", {"chat_id": get_setting("vip_channel_id", VIP_CHANNEL_ID), "user_id": tuid, "only_if_banned": True})
                    except Exception:
                        pass
                    with connect_db() as db:
                        db.execute("UPDATE subscriptions SET status='EXPIRED' WHERE user_id=?", (tuid,))
                    await send_message(cid, "🚫 Revoked.")
            elif cmd == "/export":
                await export_csv_backups(cid)
            elif cmd == "/setsetting":
                p = text.split(None, 2)
                al = {"main_channel_id", "vip_channel_id", "main_channel_url", "payhero_channel_id", "mpesa_till_number", "airtel_money_number", "referral_reward_days", "vip_odds_info", "admin_contact_url", "website_url", "cryptapi_confirmations", "cryptapi_callback_base", "intl_payment_note"}
                if len(p) == 3 and p[1] in al:
                    set_setting(p[1], p[2].strip())
                    await send_message(cid, f"✅ Saved <code>{p[1]}</code>.")
            return
        await send_message(cid, "❓ Unknown command.", main_menu_keyboard(uid))
        return

    sess = get_session(uid)
    if sess:
        st, pl = sess["state"], sess["payload"]
        if st == "WAIT_MANUAL_ANALYSIS":
            clear_session(uid)
            await send_message(cid, f"🤖 <b>Manual Analysis: {html_escape(text)}</b>\n\nℹ️ Live Football/AI APIs are scheduled for Stage 2.", main_menu_keyboard(uid))
            return
        if st == "WAIT_MPESA_PHONE":
            ph = normalize_phone(text)
            if not ph:
                await send_message(cid, "⚠️ Enter valid Safaricom number (<code>0712345678</code>) or /cancel.")
                return
            clear_session(uid)
            await start_mpesa_payment(uid, pl, ph, notify_telegram=True)
            return
        if st == "WAIT_PAYMENT_PROOF":
            clear_session(uid)
            await handle_payment_proof_submission(uid, cid, pl, text, pfid, doc_fid=dfid)
            return
        if st == "WAIT_SUPPORT_MSG":
            clear_session(uid)
            kb = {"inline_keyboard": [[{"text": "💬 Reply", "callback_data": f"admin:reply:{uid}", "style": "primary"}]]}
            if pfid:
                await telegram("sendPhoto", {"chat_id": ADMIN_USER_ID, "photo": pfid, "caption": f"📩 From <code>{uid}</code>\n{html_escape(text)}"[:1020], "parse_mode": "HTML", "reply_markup": kb})
            elif dfid:
                await telegram("sendDocument", {"chat_id": ADMIN_USER_ID, "document": dfid, "caption": f"📩 From <code>{uid}</code>\n{html_escape(text)}"[:1020], "parse_mode": "HTML", "reply_markup": kb})
            else:
                await send_message(ADMIN_USER_ID, f"📩 <b>SUPPORT</b> From <code>{uid}</code>\n\n{html_escape(text)}", kb)
            await send_message(cid, f"✅ Sent to admin ({BRAND_TAG}).", main_menu_keyboard(uid))
            return

        admin_states = {"WAIT_ADMIN_REPLY", "WAIT_CHANNEL_POST", "WAIT_VIP_CHANNEL_POST", "WAIT_BTN_MEDIA", "WAIT_BTN_ADD_TICKET", "WAIT_BTN_ADD_PRICE", "WAIT_BTN_ADD_URL", "WAIT_BTN_SETPRICE", "WAIT_BTN_SETINTLPRICE", "WAIT_BTN_SETDAYS", "WAIT_BTN_RENAME", "WAIT_BTN_SETDESC", "WAIT_BROADCAST", "WAIT_PLAN_PRICE", "WAIT_INTL_PLAN_PRICE", "WAIT_ADD_INTL_PLAN", "WAIT_INTL_NOTE", "WAIT_CRY_WALLET", "WAIT_CRY_CUSTOM_WALLET"}
        if st in admin_states:
            if not is_admin_private_chat(uid, cid, ctype):
                clear_session(uid)
                await report_unauthorized_admin_attempt(uid, f"SESSION:{st}")
                return
            if st == "WAIT_CRY_WALLET":
                clear_session(uid)
                save_cryptapi_wallet(pl, "" if text.lower() in ("clear", "none") else text.strip())
                await show_admin_cryptapi_panel(cid)
            elif st == "WAIT_CRY_CUSTOM_WALLET":
                parts = [p.strip() for p in text.split("|", 1)]
                if len(parts) != 2:
                    await send_message(cid, "Format: <code>ticker | address</code>")
                    return
                clear_session(uid)
                save_cryptapi_wallet(parts[0].lower(), "" if parts[1].lower() in ("clear", "none") else parts[1])
                await show_admin_cryptapi_panel(cid)
            elif st == "WAIT_INTL_PLAN_PRICE":
                try:
                    uv = float(text.replace("$", "").strip())
                    assert uv > 0
                except Exception:
                    await send_message(cid, "⚠️ Enter valid USD price (e.g. <code>25</code> or <code>19.99</code>).")
                    return
                clear_session(uid)
                with connect_db() as db:
                    db.execute("UPDATE intl_plans SET price_usd=? WHERE name=?", (round(uv, 2), pl))
                await show_admin_intl_packages(cid)
            elif st == "WAIT_ADD_INTL_PLAN":
                parts = [p.strip() for p in text.split("|")]
                if len(parts) < 3:
                    await send_message(cid, "Format: <code>Title | USD Price | Days | Benefits</code>")
                    return
                try:
                    uv, dv = float(parts[1].replace("$", "").strip()), int(parts[2])
                except Exception:
                    await send_message(cid, "⚠️ Invalid price or days.")
                    return
                clear_session(uid)
                slug = "intl_" + re.sub(r"[^a-z0-9]+", "_", parts[0].lower()).strip("_")[:24] + "_" + secrets.token_hex(2)
                is_lt = 1 if dv <= 0 else 0
                ed = 36500 if is_lt else dv
                ben = parts[3] if len(parts) >= 4 else f"{ed} Days Global VIP Access"
                with connect_db() as db:
                    db.execute("INSERT INTO intl_plans(name,title,price_usd,days,benefits,is_lifetime,is_active) VALUES(?,?,?,?,?,?,1)", (slug, parts[0][:64], round(uv, 2), ed, ben[:200], is_lt))
                await show_admin_intl_packages(cid)
            elif st == "WAIT_INTL_NOTE":
                clear_session(uid)
                set_setting("intl_payment_note", text[:400])
                await show_admin_intl_packages(cid)
            elif st == "WAIT_BTN_SETINTLPRICE":
                try:
                    uv = float(text.replace("$", "").strip())
                    assert uv >= 0
                except Exception:
                    await send_message(cid, "⚠️ Enter valid USD price (e.g. <code>15</code> or <code>0</code>).")
                    return
                bid = int(pl)
                clear_session(uid)
                with connect_db() as db:
                    db.execute("UPDATE custom_buttons SET intl_price_usd=? WHERE id=?", (round(uv, 2), bid))
                await show_admin_button_detail(cid, bid)
            elif st == "WAIT_ADMIN_REPLY":
                clear_session(uid)
                await send_message(int(pl), f"💬 <b>ADMIN ({BRAND_TAG})</b>\n\n{html_escape(text)}")
                await send_message(cid, "✅ Sent.", admin_keyboard(uid))
            elif st == "WAIT_CHANNEL_POST":
                clear_session(uid)
                pk = json.loads(pl)
                await publish_custom_main_post(text, photo_file_id=pfid, selected_btn_ids=pk.get("btn_ids", []), include_footer=pk.get("footer", True), include_reactions=pk.get("react", True))
                await send_message(cid, "✅ Published to Main Channel!", admin_keyboard(uid))
            elif st == "WAIT_VIP_CHANNEL_POST":
                if not text and not pfid:
                    await send_message(cid, "⚠️ Send text or photo.")
                    return
                await publish_vip_channel_post(text, photo_file_id=pfid)
                await send_message(cid, "✅ Published to VIP Channel!", {"inline_keyboard": [[{"text": "✅ Done", "callback_data": "admin:home", "style": "success"}]]})
            elif st == "WAIT_BTN_MEDIA":
                bid = int(pl)
                fid, mt = (pfid, "photo") if pfid else ((dfid, "document") if dfid else (None, None))
                if not fid:
                    await send_message(cid, "⚠️ Send a photo/file or /done.")
                    return
                okey = "btn:" + str(bid)
                with connect_db() as db:
                    db.execute("INSERT INTO ticket_media(offer_key,file_id,media_type,caption,created_at) VALUES(?,?,?,?,?)", (okey, fid, mt, text[:400], utcnow().isoformat()))
                img_n = count_offer_media(okey)
                await send_message(cid, f"✅ Saved image #{img_n}!", {"inline_keyboard": [[{"text": "✅ Done Uploading", "callback_data": f"admin:btn_media:{bid}", "style": "success"}]]})
            elif st == "WAIT_BTN_ADD_TICKET":
                parts = [p.strip() for p in text.split("|")]
                if len(parts) >= 2 and parts[1].isdigit():
                    pr, ipr, dy = int(parts[1]), float(parts[2]) if len(parts) >= 3 else 0.0, int(parts[3]) if (len(parts) >= 4 and parts[3].isdigit()) else 1
                    clear_session(uid)
                    with connect_db() as db:
                        nid = insert_returning_id(db, "INSERT INTO custom_buttons(label,url,button_type,price,intl_price_usd,days,description,style,is_active) VALUES(?,?,?,?,?,?,?,?,1)", (parts[0][:64], "", "TICKET", pr, ipr, dy, "", "primary"))
                    await show_admin_button_detail(cid, nid)
                elif text:
                    set_session(uid, "WAIT_BTN_ADD_PRICE", text[:64])
                    await send_message(cid, f"💰 Enter <b>Local KES price</b> for <b>{html_escape(text[:64])}</b>:")
            elif st == "WAIT_BTN_ADD_PRICE" and text.isdigit():
                clear_session(uid)
                with connect_db() as db:
                    nid = insert_returning_id(db, "INSERT INTO custom_buttons(label,url,button_type,price,intl_price_usd,days,description,style,is_active) VALUES(?,?,?,?,?,?,?,?,1)", (pl[:64], "", "TICKET", int(text), 0.0, 1, "", "primary"))
                await show_admin_button_detail(cid, nid)
            elif st == "WAIT_BTN_ADD_URL":
                parts = [p.strip() for p in text.split("|", 1)]
                if len(parts) != 2 or not parts[1].startswith(("http://", "https://")):
                    await send_message(cid, "Format: <code>Label | https://...</code>")
                    return
                clear_session(uid)
                with connect_db() as db:
                    nid = insert_returning_id(db, "INSERT INTO custom_buttons(label,url,button_type,price,intl_price_usd,days,description,style,is_active) VALUES(?,?,?,?,?,?,?,?,1)", (parts[0][:64], parts[1][:300], "URL", 0, 0.0, 0, "", "primary"))
                await show_admin_button_detail(cid, nid)
            elif st == "WAIT_BTN_SETPRICE" and text.isdigit():
                bid = int(pl)
                clear_session(uid)
                with connect_db() as db:
                    db.execute("UPDATE custom_buttons SET price=? WHERE id=?", (int(text), bid))
                await show_admin_button_detail(cid, bid)
            elif st == "WAIT_BTN_SETDAYS" and text.isdigit():
                bid = int(pl)
                clear_session(uid)
                with connect_db() as db:
                    db.execute("UPDATE custom_buttons SET days=? WHERE id=?", (int(text), bid))
                await show_admin_button_detail(cid, bid)
            elif st == "WAIT_BTN_RENAME" and text:
                bid = int(pl)
                clear_session(uid)
                with connect_db() as db:
                    db.execute("UPDATE custom_buttons SET label=? WHERE id=?", (text[:64], bid))
                await show_admin_button_detail(cid, bid)
            elif st == "WAIT_BTN_SETDESC":
                bid = int(pl)
                clear_session(uid)
                with connect_db() as db:
                    db.execute("UPDATE custom_buttons SET description=? WHERE id=?", (text[:300], bid))
                await show_admin_button_detail(cid, bid)
            elif st == "WAIT_BROADCAST":
                clear_session(uid)
                with connect_db() as db:
                    targets = db.execute("SELECT user_id FROM users WHERE is_blocked=0 AND notify_news=1").fetchall()
                sent = 0
                for t in targets:
                    try:
                        if pfid:
                            await telegram("sendPhoto", {"chat_id": int(t["user_id"]), "photo": pfid, "caption": f"📣 <b>{PROJECT_NAME}</b>\n\n{html_escape(text)}"[:1020], "parse_mode": "HTML"})
                        else:
                            await send_message(int(t["user_id"]), f"📣 <b>{PROJECT_NAME}</b>\n\n{html_escape(text)}")
                        sent += 1
                    except Exception:
                        pass
                await send_message(cid, f"✅ Sent to {sent} users.", admin_keyboard(uid))
            elif st == "WAIT_PLAN_PRICE" and text.isdigit():
                clear_session(uid)
                with connect_db() as db:
                    db.execute("UPDATE plans SET price=? WHERE name=?", (int(text), pl))
                await show_admin_packages(cid)
            return
    await show_main_menu(cid, uid)


async def handle_callback(query):
    q_id, user = query["id"], query["from"]
    if not user or user.get("is_bot"):
        asyncio.create_task(answer_callback(q_id))
        return
    uid = user["id"]
    data = query.get("data", "")
    mo = query.get("message") or {}
    co = mo.get("chat") or {}
    cid, ctype, mid = co.get("id") or uid, co.get("type", "private"), mo.get("message_id")

    if data.startswith("react:"):
        idx = int(data.split(":", 1)[1])
        if 0 <= idx < len(REACTION_EMOJIS) and mid:
            em = REACTION_EMOJIS[idx]
            with connect_db() as db:
                if db.execute("SELECT 1 FROM post_reactions WHERE channel_msg_id=? AND user_id=? AND emoji=?", (mid, uid, em)).fetchone():
                    db.execute("DELETE FROM post_reactions WHERE channel_msg_id=? AND user_id=? AND emoji=?", (mid, uid, em))
                    fb = f"Removed {em}"
                else:
                    db.execute("INSERT INTO post_reactions(channel_msg_id,user_id,emoji) VALUES(?,?,?)", (mid, uid, em))
                    fb = f"Reacted {em}"
            asyncio.create_task(answer_callback(q_id, fb))
            try:
                await ensure_bot_username()
                await telegram("editMessageReplyMarkup", {"chat_id": mo["chat"]["id"], "message_id": mid, "reply_markup": channel_post_keyboard(channel_msg_id=mid)})
            except Exception:
                pass
        else:
            asyncio.create_task(answer_callback(q_id))
        return

    asyncio.create_task(answer_callback(q_id))
    register_user(user)
    du = get_user(uid)
    if du and du["is_blocked"] and not is_admin(uid):
        return

    if data == "menu:home":
        clear_session(uid)
        await show_main_menu(cid, uid)
    elif data == "menu:today":
        with connect_db() as db:
            rows = db.execute("SELECT * FROM matches WHERE tier='FREE' AND publish_status='PUBLISHED' ORDER BY id DESC LIMIT 10").fetchall()
        txt = "\n".join(format_match_block(m, i + 1) for i, m in enumerate(rows)) if rows else "<i>No free predictions yet.</i>"
        await send_message(cid, f"⚽ <b>TODAY'S PREDICTIONS</b>\n━━━━━━━━━━━━━━━━━━━━\n\n{txt}", {"inline_keyboard": [[{"text": "🏠 Main Menu", "callback_data": "menu:home", "style": "primary"}]]})
    elif data == "menu:ai_analysis":
        set_session(uid, "WAIT_MANUAL_ANALYSIS")
        await send_message(cid, "🤖 Send a fixture name (e.g. <code>Arsenal vs Chelsea</code>) or /cancel:")
    elif data == "menu:accum":
        await show_accumulator_builder(cid, uid)
    elif data.startswith("accum:"):
        parts = data.split(":", 2)
        sub = parts[1]
        sess = get_session(uid)
        draft = json.loads(sess["payload"]) if (sess and sess["state"] == "ACCUM_BUILD") else {"category": "3-5 Odds", "match_ids": []}
        if sub == "cat" and len(parts) == 3:
            draft["category"] = parts[2]
            set_session(uid, "ACCUM_BUILD", json.dumps(draft))
            await show_accumulator_builder(cid, uid)
        elif sub == "toggle" and len(parts) == 3:
            m_id = int(parts[2])
            if m_id in draft["match_ids"]:
                draft["match_ids"].remove(m_id)
            else:
                draft["match_ids"].append(m_id)
            set_session(uid, "ACCUM_BUILD", json.dumps(draft))
            await show_accumulator_builder(cid, uid)
        elif sub == "clear":
            draft["match_ids"] = []
            set_session(uid, "ACCUM_BUILD", json.dumps(draft))
            await show_accumulator_builder(cid, uid)
        elif sub == "save" and draft["match_ids"]:
            with connect_db() as db:
                ph = ",".join(["?"] * len(draft["match_ids"]))
                rows = db.execute(f"SELECT * FROM matches WHERE id IN ({ph})", tuple(draft["match_ids"])).fetchall()
                to, sels = 1.0, []
                for r in rows:
                    sels.append({"id": r["id"], "match": r["match_name"], "tip": r["tip"], "odds": r["odds"]})
                    try:
                        to *= float(r["odds"])
                    except ValueError:
                        pass
                db.execute("INSERT INTO accumulators(category,selections_json,total_odds,risk_note,status,created_by,created_at) VALUES(?,?,?,?,?,?,?)", (draft["category"], json.dumps(sels), f"{to:.2f}", "Accumulators are not guaranteed.", "PENDING_ADMIN", uid, utcnow().isoformat()))
            clear_session(uid)
            await send_message(cid, f"🎟️ <b>Accumulator Submitted!</b> ({to:.2f}x)", main_menu_keyboard(uid))
    elif data == "menu:stats":
        await show_stats_and_results(cid, uid, only_settled=False)
    elif data == "menu:results":
        await show_stats_and_results(cid, uid, only_settled=True)
    elif data == "menu:vip":
        await show_vip_screen(cid, uid)
    elif data == "menu:vip_intl":
        await show_vip_screen(cid, uid, force_scope="INTL")
    elif data == "menu:vip_local":
        await show_vip_screen(cid, uid, force_scope="LOCAL")
    elif data == "menu:subscription":
        await ensure_bot_username()
        prefs = get_user_prefs(uid)
        with connect_db() as db:
            sub = db.execute("SELECT * FROM subscriptions WHERE user_id=?", (uid,)).fetchone()
            orders = db.execute("SELECT * FROM orders WHERE user_id=? ORDER BY created_at DESC LIMIT 6", (uid,)).fetchall()
            rcnt = db.execute("SELECT COUNT(*) AS n FROM users WHERE referred_by=? AND referral_rewarded=1", (uid,)).fetchone()["n"]
        stxt = f"✅ Active ({sub['plan']}) — Expires: {'Lifetime' if sub['is_lifetime'] else format_local_time(sub['expires_at'], prefs['tz'])}" if (sub and sub["status"] == "ACTIVE") else "❌ Inactive"
        otxt = "\n".join(f"• {html_escape(format_order_item_name(o['plan']))} — {o['local_amount'] or o['amount']} [{o['status']}]" for o in orders) or "No orders yet."
        rlink = f"https://t.me/{bot_username}?start=ref_{uid}" if bot_username else ""
        await send_message(cid, f"💳 <b>MY SUBSCRIPTION</b>\nStatus: <b>{stxt}</b>\n\n<b>Recent Orders:</b>\n{otxt}" + (f"\n\n🎁 Referral Link (Rewarded: {rcnt}): <code>{rlink}</code>" if rlink else ""), {"inline_keyboard": [[{"text": "👑 VIP Membership", "callback_data": "menu:vip", "style": "danger"}], [{"text": "🏠 Main Menu", "callback_data": "menu:home"}]]})
    elif data == "menu:country":
        await show_country_welcome(cid)
    elif data == "country:detect_ask":
        set_session(uid, "WAIT_LOCATION_PERM")
        await send_message(cid, "📍 Share approximate location:", {"keyboard": [[{"text": "📍 Share My Location", "request_location": True}], [{"text": "/cancel"}]], "resize_keyboard": True, "one_time_keyboard": True})
    elif data == "country:manual":
        await send_message(cid, "🗺️ <b>Select country:</b>", country_manual_keyboard())
    elif data.startswith("country:pick:"):
        code = data.split(":")[2]
        c = COUNTRIES.get(code)
        if c:
            await send_message(cid, f"📍 {c['flag']} <b>{c['name']}</b> ({c['currency']})\nConfirm?", {"inline_keyboard": [[{"text": "✅ Confirm", "callback_data": f"country:confirm:{code}", "style": "success"}], [{"text": "🔄 Change", "callback_data": "country:manual"}]]})
    elif data.startswith("country:confirm:"):
        code = data.split(":")[2]
        c = COUNTRIES.get(code)
        if c:
            with connect_db() as db:
                db.execute("UPDATE users SET country=?, country_confirmed=1, currency=?, timezone_name=? WHERE user_id=?", (code, c["currency"], c["tz"], uid))
            await show_main_menu(cid, uid)
    elif data == "currency:menu":
        await send_message(cid, "💱 <b>Choose display currency:</b>", currency_manual_keyboard())
    elif data.startswith("currency:set:"):
        curr = data.split(":")[2]
        if curr in CURRENCIES:
            with connect_db() as db:
                db.execute("UPDATE users SET currency=?, country_confirmed=1 WHERE user_id=?", (curr, uid))
            await show_vip_screen(cid, uid)
    elif data == "menu:contact":
        set_session(uid, "WAIT_SUPPORT_MSG")
        await send_message(cid, f"💬 Send your message to admin ({BRAND_TAG}) or /cancel:")
    elif data.startswith("openbtn:") and data.split(":", 1)[1].isdigit():
        await show_ticket_button_checkout(cid, uid, int(data.split(":", 1)[1]), force_scope="LOCAL")
    elif data.startswith("openibtn:") and data.split(":", 1)[1].isdigit():
        await show_ticket_button_checkout(cid, uid, int(data.split(":", 1)[1]), force_scope="INTL")
    elif data.startswith("plan:"):
        pn = data.split(":", 1)[1]
        pl = get_plan(pn)
        if not pl:
            return
        prefs = get_user_prefs(uid)
        c = COUNTRIES.get(prefs["country"] if prefs["country"] in LOCAL_COUNTRIES else "KE", COUNTRIES["KE"])
        disp, _ = format_converted_price(pl["price"], prefs["currency"])
        till, airtel = get_setting("mpesa_till_number", DEFAULT_MPESA_TILL), get_setting("airtel_money_number", DEFAULT_AIRTEL_NUMBER)
        rows = []
        for m in c["methods"]:
            lbl, sty = METHOD_LABELS[m]
            if m == "mpesa_till":
                lbl = f"🏪 Manual M-Pesa Till ({till})"
            elif m == "airtel":
                lbl = f"🔴 Airtel Money ({airtel})"
            rows.append([{"text": lbl, "callback_data": f"pay:{pn}:{m}", "style": sty}])
        rows.append([{"text": "⬅️ Back", "callback_data": "menu:vip_local"}])
        await send_message(cid, f"💳 <b>{html_escape(pl['title'])}</b> — {disp}\n\n{c['how']}", {"inline_keyboard": rows})
    elif data.startswith("iplan:"):
        pn = data.split(":", 1)[1]
        ip = get_intl_plan(pn)
        if not ip:
            return
        prefs = get_user_prefs(uid)
        disp, _ = format_usd_price(ip["price_usd"], prefs["currency"] if prefs["is_intl"] else "USD")
        rows = [
            [{"text": "🪙 Crypto (CryptAPI — 100+ Coins)", "callback_data": f"ipay:{pn}:crypto", "style": "success"}],
            [{"text": "💲 Card / PayPal / Bank Transfer", "callback_data": f"ipay:{pn}:intl_transfer", "style": "primary"}],
            [{"text": "⬅️ Back", "callback_data": "menu:vip_intl"}],
        ]
        await send_message(cid, f"🌍 <b>{html_escape(ip['title'])}</b> — <b>{html_escape(disp)}</b>\n\n{html_escape(get_setting('intl_payment_note'))}", {"inline_keyboard": rows})
    elif data.startswith("pay:"):
        _, pn, prov = data.split(":", 2)
        if prov == "mpesa":
            set_session(uid, "WAIT_MPESA_PHONE", pn)
            await send_message(cid, "📱 Enter Kenyan Safaricom number (<code>0712345678</code>) or /cancel:")
        else:
            await create_manual_order_and_prompt(uid, pn, prov)
    elif data.startswith("ipay:"):
        _, pn, prov = data.split(":", 2)
        ok = f"intl:{pn}"
        if prov == "crypto":
            await show_crypto_checkout_menu(cid, uid, ok, view="home")
        else:
            await create_manual_order_and_prompt(uid, ok, prov)
    elif data.startswith("buybtn:"):
        _, rid, meth = data.split(":", 2)
        if rid.isdigit():
            ok = f"btn:{rid}"
            if meth == "mpesa":
                set_session(uid, "WAIT_MPESA_PHONE", ok)
                await send_message(cid, "📲 Enter Kenyan Safaricom number (<code>0712345678</code>) or /cancel:")
            else:
                await create_manual_order_and_prompt(uid, ok, meth)
    elif data.startswith("buyibtn:"):
        _, rid, meth = data.split(":", 2)
        if rid.isdigit():
            ok = f"ibtn:{rid}"
            if meth == "crypto":
                await show_crypto_checkout_menu(cid, uid, ok, view="home")
            else:
                await create_manual_order_and_prompt(uid, ok, meth)
    elif data.startswith("cryptolist|"):
        p = data.split("|")
        if len(p) == 4:
            await show_crypto_checkout_menu(cid, uid, p[1], view=p[2], page=int(p[3]) if p[3].isdigit() else 0, message_id=mid)
    elif data.startswith("cryptopay|"):
        p = data.split("|", 2)
        if len(p) == 3:
            await start_cryptapi_payment(uid, cid, p[1], p[2])
    elif data.startswith("cryptocheck:"):
        er = data.split(":", 1)[1]
        with connect_db() as db:
            order = db.execute("SELECT * FROM orders WHERE external_ref=? AND user_id=?", (er, uid)).fetchone()
        if order and order["status"] != "PAID":
            try:
                if await verify_cryptapi_order_via_logs(order) == "NO_TX":
                    await send_message(cid, f"⏳ No transaction detected yet for <code>{er}</code>.")
            except Exception:
                await send_message(cid, "⚠️ Could not reach CryptAPI right now.")
    elif data.startswith("manualtill:"):
        er = data.split(":", 1)[1]
        with connect_db() as db:
            order = db.execute("SELECT * FROM orders WHERE external_ref=? AND user_id=?", (er, uid)).fetchone()
        if order:
            await send_manual_till_fallback(uid, er, format_order_item_name(order["plan"]), order["amount"])
    elif data.startswith("proof:"):
        er = data.split(":", 1)[1]
        set_session(uid, "WAIT_PAYMENT_PROOF", er)
        await send_message(cid, f"📸 Send payment screenshot/PDF for Ref <code>{er}</code>:")
    elif data.startswith("verify:"):
        er = data.split(":", 1)[1]
        with connect_db() as db:
            order = db.execute("SELECT * FROM orders WHERE external_ref=? AND user_id=?", (er, uid)).fetchone()
        if order and order["payhero_reference"]:
            try:
                if await finalize_paid_order(order, verified=await verify_payhero_transaction(order["payhero_reference"])) == "PENDING":
                    await send_message(cid, f"⏳ Still pending. Use Till <code>{get_setting('mpesa_till_number', DEFAULT_MPESA_TILL)}</code> if needed.")
            except Exception:
                await send_message(cid, "⚠️ PayHero status unavailable right now.")
    elif data.startswith("postpick:"):
        if not is_admin_private_chat(uid, cid, ctype):
            await report_unauthorized_admin_attempt(uid, f"CB:{data}")
            return
        parts = data.split(":")
        sub = parts[1]
        sess = get_session(uid)
        pk = json.loads(sess["payload"]) if (sess and sess["state"] == "POST_PICKER") else {"mode": "custom", "btn_ids": [], "footer": True, "react": True}
        if sub == "toggle" and len(parts) == 3 and parts[2].isdigit():
            bid = int(parts[2])
            if bid in pk["btn_ids"]:
                pk["btn_ids"].remove(bid)
            else:
                pk["btn_ids"].append(bid)
            await show_post_button_picker(cid, uid, pk, message_id=mid)
        elif sub == "all":
            pk["btn_ids"] = [int(b["id"]) for b in get_all_custom_buttons(only_active=True)]
            await show_post_button_picker(cid, uid, pk, message_id=mid)
        elif sub == "none":
            pk["btn_ids"] = []
            await show_post_button_picker(cid, uid, pk, message_id=mid)
        elif sub == "footer":
            pk["footer"] = not pk.get("footer", True)
            await show_post_button_picker(cid, uid, pk, message_id=mid)
        elif sub == "react":
            pk["react"] = not pk.get("react", True)
            await show_post_button_picker(cid, uid, pk, message_id=mid)
        elif sub == "next":
            m = pk.get("mode", "custom")
            if m == "custom":
                set_session(uid, "WAIT_CHANNEL_POST", json.dumps(pk))
                await send_message(cid, "📝 Send text or photo+caption for Main Channel (or /cancel):")
            elif m == "vip_promo":
                clear_session(uid)
                await publish_vip_post(selected_btn_ids=pk.get("btn_ids", []), include_footer=pk.get("footer", True), include_reactions=pk.get("react", True))
                await send_message(cid, "✅ VIP Promo published!", admin_keyboard(uid))
            elif m == "free_matches":
                clear_session(uid)
                res = await publish_match_batch("FREE", selected_btn_ids=pk.get("btn_ids", []), include_footer=pk.get("footer", True), include_reactions=pk.get("react", True))
                await send_message(cid, "✅ Published FREE matches!" if res else "ℹ️ No queued FREE matches.", admin_keyboard(uid))
    elif data.startswith("admin:"):
        if not is_admin_private_chat(uid, cid, ctype):
            await report_unauthorized_admin_attempt(uid, f"CB:{data}")
            return
        parts = data.split(":")
        act = parts[1]
        if act == "home":
            clear_session(uid)
            await send_message(cid, f"⚡ <b>{PROJECT_NAME} ADMIN</b>", admin_keyboard(uid))
        elif act == "stats":
            await show_admin_stats(cid, uid)
        elif act == "matches":
            await show_admin_matches(cid, uid)
        elif act == "pub_free":
            await show_post_button_picker(cid, uid, {"mode": "free_matches", "btn_ids": [], "footer": True, "react": True})
        elif act == "pub_vip":
            res = await publish_match_batch("VIP", selected_btn_ids=[], include_footer=True, include_reactions=False)
            await send_message(cid, "✅ Published VIP matches!" if res else "ℹ️ No queued VIP matches.", admin_keyboard(uid))
        elif act == "res" and len(parts) == 4:
            with connect_db() as db:
                db.execute("UPDATE matches SET result=? WHERE id=?", (parts[3], int(parts[2])))
            await sync_channel_message_for_match(int(parts[2]))
            await show_admin_matches(cid, uid)
        elif act == "accums":
            await show_admin_accumulators(cid, uid, message_id=mid)
        elif act == "accum_pub" and len(parts) == 4 and parts[2].isdigit():
            await publish_accumulator_to_channel(int(parts[2]), target=parts[3].upper())
            await show_admin_accumulators(cid, uid, message_id=mid)
        elif act == "accum_rej" and len(parts) == 3 and parts[2].isdigit():
            with connect_db() as db:
                db.execute("UPDATE accumulators SET status='REJECTED' WHERE id=?", (int(parts[2]),))
            await show_admin_accumulators(cid, uid, message_id=mid)
        elif act == "transactions":
            await show_admin_transactions(cid, uid, message_id=mid)
        elif act == "viewproof" and len(parts) == 3:
            with connect_db() as db:
                o = db.execute("SELECT * FROM orders WHERE external_ref=?", (parts[2],)).fetchone()
            if o and o["proof_file_id"]:
                kb = {"inline_keyboard": [[{"text": "✅ Approve", "callback_data": f"admin:approve:{parts[2]}", "style": "success"}, {"text": "❌ Reject", "callback_data": f"admin:reject:{parts[2]}", "style": "danger"}]]}
                await telegram("sendDocument" if o["proof_type"] == "document" else "sendPhoto", {"chat_id": cid, ("document" if o["proof_type"] == "document" else "photo"): o["proof_file_id"], "caption": f"Proof {parts[2]}", "reply_markup": kb})
        elif act == "approve" and len(parts) == 3:
            with connect_db() as db:
                order = db.execute("SELECT * FROM orders WHERE external_ref=?", (parts[2],)).fetchone()
            if order:
                res = await finalize_paid_order(order, manual_admin=True)
                await send_message(cid, f"✅ Approved <code>{parts[2]}</code> ({res}).", {"inline_keyboard": [[{"text": "🧾 Transactions", "callback_data": "admin:transactions"}]]})
        elif act == "reject" and len(parts) == 3:
            with connect_db() as db:
                order = db.execute("SELECT * FROM orders WHERE external_ref=?", (parts[2],)).fetchone()
                db.execute("UPDATE orders SET status='FAILED' WHERE external_ref=?", (parts[2],))
            if order:
                asyncio.create_task(send_message(int(order["user_id"]), f"❌ Payment <code>{parts[2]}</code> rejected. Contact {BRAND_TAG}."))
            await send_message(cid, f"🚫 Rejected <code>{parts[2]}</code>.")
        elif act == "users":
            await show_admin_users(cid, uid)
        elif act == "buttons":
            await show_admin_buttons(cid, message_id=mid)
        elif act == "btn_view" and len(parts) == 3 and parts[2].isdigit():
            await show_admin_button_detail(cid, int(parts[2]), message_id=mid)
        elif act == "btn_media" and len(parts) == 3 and parts[2].isdigit():
            clear_session(uid)
            await show_admin_button_media(cid, int(parts[2]), message_id=mid)
        elif act == "btn_media_add" and len(parts) == 3 and parts[2].isdigit():
            set_session(uid, "WAIT_BTN_MEDIA", parts[2])
            await send_message(cid, "🖼️ Send ticket photo(s) now, then /done:")
        elif act == "btn_media_prev" and len(parts) == 3 and parts[2].isdigit():
            b = get_custom_button(int(parts[2]))
            if b:
                await send_offer_ticket_media(cid, f"btn:{b['id']}", b["label"])
        elif act == "btn_media_clr" and len(parts) == 3 and parts[2].isdigit():
            with connect_db() as db:
                db.execute("DELETE FROM ticket_media WHERE offer_key=?", ("btn:" + parts[2],))
            await show_admin_button_media(cid, int(parts[2]), message_id=mid)
        elif act == "btn_add_ticket":
            set_session(uid, "WAIT_BTN_ADD_TICKET")
            await send_message(cid, "➕ Send: <code>Label | Local_KES | Intl_USD | Days</code>\nExample: <code>💎 Diamond | 1500 | 20 | 0</code>")
        elif act == "btn_add_url":
            set_session(uid, "WAIT_BTN_ADD_URL")
            await send_message(cid, "🔗 Send: <code>Label | https://...</code>")
        elif act == "btn_setprice" and len(parts) == 3:
            set_session(uid, "WAIT_BTN_SETPRICE", parts[2])
            await send_message(cid, "🇰🇪 Enter <b>Local KES price</b>:")
        elif act == "btn_setintlprice" and len(parts) == 3:
            set_session(uid, "WAIT_BTN_SETINTLPRICE", parts[2])
            await send_message(cid, "🌍 Enter <b>International USD price</b> (e.g. <code>15</code> or <code>19.99</code>):")
        elif act == "btn_setdays" and len(parts) == 3:
            set_session(uid, "WAIT_BTN_SETDAYS", parts[2])
            await send_message(cid, "📅 Enter VIP days (0 = ticket image only):")
        elif act == "btn_rename" and len(parts) == 3:
            set_session(uid, "WAIT_BTN_RENAME", parts[2])
            await send_message(cid, "✏️ Send new label:")
        elif act == "btn_setdesc" and len(parts) == 3:
            set_session(uid, "WAIT_BTN_SETDESC", parts[2])
            await send_message(cid, "📝 Send description:")
        elif act == "btn_style" and len(parts) == 3 and parts[2].isdigit():
            b = get_custom_button(int(parts[2]))
            if b:
                cur = b["style"] if b["style"] in BUTTON_STYLES else "primary"
                nxt = BUTTON_STYLES[(BUTTON_STYLES.index(cur) + 1) % len(BUTTON_STYLES)]
                with connect_db() as db:
                    db.execute("UPDATE custom_buttons SET style=? WHERE id=?", (nxt, int(parts[2])))
                await show_admin_button_detail(cid, int(parts[2]), message_id=mid)
        elif act == "btn_del" and len(parts) == 3:
            await edit_or_send(cid, mid, "🗑️ Remove button?", {"inline_keyboard": [[{"text": "✅ Yes", "callback_data": f"admin:btn_del_yes:{parts[2]}", "style": "danger"}, {"text": "❌ Cancel", "callback_data": f"admin:btn_view:{parts[2]}"}]]})
        elif act == "btn_del_yes" and len(parts) == 3:
            with connect_db() as db:
                db.execute("DELETE FROM custom_buttons WHERE id=?", (int(parts[2]),))
            await show_admin_buttons(cid, message_id=mid)
        elif act == "post_channel":
            await show_post_button_picker(cid, uid, {"mode": "custom", "btn_ids": [], "footer": True, "react": True})
        elif act == "post_vip_channel":
            set_session(uid, "WAIT_VIP_CHANNEL_POST")
            await send_message(cid, "💎 Send VIP post text or photo+caption (or /cancel):")
        elif act == "post":
            await show_post_button_picker(cid, uid, {"mode": "vip_promo", "btn_ids": [], "footer": True, "react": True})
        elif act == "broadcast":
            set_session(uid, "WAIT_BROADCAST")
            await send_message(cid, "📣 Send broadcast text or photo:")
        elif act == "reply" and len(parts) == 3:
            set_session(uid, "WAIT_ADMIN_REPLY", parts[2])
            await send_message(cid, f"💬 Reply to <code>{parts[2]}</code>:")
        elif act == "packages":
            await show_admin_packages(cid, message_id=mid)
        elif act == "editprice" and len(parts) == 3:
            set_session(uid, "WAIT_PLAN_PRICE", parts[2])
            await send_message(cid, f"🇰🇪 Enter new KES price for <b>{parts[2]}</b>:")
        elif act == "intl_packages":
            await show_admin_intl_packages(cid, message_id=mid)
        elif act == "edit_intl_price" and len(parts) == 3:
            set_session(uid, "WAIT_INTL_PLAN_PRICE", parts[2])
            await send_message(cid, f"💵 Enter new <b>USD price</b> for <b>{html_escape(parts[2])}</b>:")
        elif act == "add_intl_plan":
            set_session(uid, "WAIT_ADD_INTL_PLAN")
            await send_message(cid, "➕ Send: <code>Title | USD Price | Days | Benefits</code>")
        elif act == "del_intl_plan" and len(parts) == 3:
            with connect_db() as db:
                db.execute("DELETE FROM intl_plans WHERE name=?", (parts[2],))
            await show_admin_intl_packages(cid, message_id=mid)
        elif act == "edit_intl_note":
            set_session(uid, "WAIT_INTL_NOTE")
            await send_message(cid, "📝 Send international transfer instructions:")
        elif act == "channels":
            await show_admin_settings(cid, message_id=mid)
        elif act == "payhero_diag":
            await show_admin_payhero_diag(cid, message_id=mid)
        elif act == "ph_use_ch" and len(parts) == 3 and parts[2].isdigit():
            set_setting("payhero_channel_id", parts[2])
            await show_admin_payhero_diag(cid, message_id=mid)
        elif act == "cryptapi":
            await show_admin_cryptapi_panel(cid, message_id=mid)
        elif act == "cry_refresh":
            await fetch_cryptapi_catalog(force=True)
            await show_admin_cryptapi_panel(cid, message_id=mid)
        elif act == "cry_set" and len(parts) == 3:
            set_session(uid, "WAIT_CRY_WALLET", parts[2].lower())
            await send_message(cid, f"🪙 Paste <b>{html_escape(parts[2].upper())}</b> wallet address (or <code>clear</code>):")
        elif act == "cry_set_custom":
            set_session(uid, "WAIT_CRY_CUSTOM_WALLET")
            await send_message(cid, "🎯 Send: <code>ticker | wallet_address</code>")
        elif act == "toggle_autoshot":
            set_setting("auto_confirm_screenshot", "0" if get_setting("auto_confirm_screenshot", "0") == "1" else "1")
            await show_admin_settings(cid, message_id=mid)
        elif act == "export":
            await export_csv_backups(cid)
        elif act == "audit":
            with connect_db() as db:
                logs = db.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 15").fetchall()
            await send_message(cid, "🛡️ <b>AUDIT LOGS</b>\n\n" + ("\n".join(f"• {l['action']}: {html_escape(l['details'])}" for l in logs) or "None."), admin_keyboard(uid))


async def show_admin_settings(chat_id, message_id=None):
    if str(chat_id) != str(ADMIN_USER_ID):
        return
    ashot = get_setting("auto_confirm_screenshot", "0") == "1"
    txt = f"⚙️ <b>CHANNELS & PAYMENT SETTINGS</b>\n📢 Main: <code>{html_escape(get_setting('main_channel_id'))}</code> | 💎 VIP: <code>{html_escape(get_setting('vip_channel_id'))}</code>\n📲 PayHero Ch: <code>{html_escape(str(get_setting('payhero_channel_id', PAYHERO_CHANNEL_ID)))}</code> | 🏪 Till: <code>{html_escape(get_setting('mpesa_till_number'))}</code>\n🔴 Airtel: <code>{html_escape(get_setting('airtel_money_number'))}</code> | 🪙 Crypto Wallets: <b>{len(get_cryptapi_wallets_map())}</b>"
    kb = {"inline_keyboard": [[{"text": "🩺 Test PayHero & Channels", "callback_data": "admin:payhero_diag", "style": "primary"}], [{"text": "🪙 CryptAPI Crypto Wallets", "callback_data": "admin:cryptapi", "style": "success"}], [{"text": "🌍 International Plans & Prices ($)", "callback_data": "admin:intl_packages", "style": "danger"}], [{"text": f"📸 Screenshot Auto-Deliver: {'ON' if ashot else 'OFF'}", "callback_data": "admin:toggle_autoshot", "style": "success" if ashot else "primary"}], [{"text": "⬅️ Admin", "callback_data": "admin:home"}]]}
    await edit_or_send(chat_id, message_id, txt, kb)


async def show_admin_stats(chat_id, user_id):
    if not is_admin(user_id) or str(chat_id) != str(ADMIN_USER_ID):
        return
    with connect_db() as db:
        uc = db.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        vc = db.execute("SELECT COUNT(*) AS n FROM subscriptions WHERE status='ACTIVE'").fetchone()["n"]
        pc = db.execute("SELECT COUNT(*) AS n FROM orders WHERE status='PAID'").fetchone()["n"]
        rk = db.execute("SELECT COALESCE(SUM(amount),0) AS n FROM orders WHERE status='PAID' AND market_scope='LOCAL'").fetchone()["n"]
        ru = db.execute("SELECT COALESCE(SUM(amount_usd),0) AS n FROM orders WHERE status='PAID' AND market_scope='INTL'").fetchone()["n"]
    db_lbl = "PostgreSQL ✅ (Pooled)" if USE_POSTGRES else "SQLite"
    await send_message(chat_id, f"📊 <b>{PROJECT_NAME} OVERVIEW</b>\n🗄 DB: {db_lbl}\n👤 Users: <b>{uc}</b> | 👑 VIPs: <b>{vc}</b> | ✅ Paid: <b>{pc}</b>\n🇰🇪 Local: <b>KES {rk:,}</b> | 🌍 Intl: <b>${float(ru):,.2f} USD</b>", admin_keyboard(user_id))


async def show_admin_matches(chat_id, user_id):
    if not is_admin(user_id) or str(chat_id) != str(ADMIN_USER_ID):
        return
    with connect_db() as db:
        qf = db.execute("SELECT COUNT(*) AS n FROM matches WHERE tier='FREE' AND publish_status='QUEUED'").fetchone()["n"]
        qv = db.execute("SELECT COUNT(*) AS n FROM matches WHERE tier='VIP' AND publish_status='QUEUED'").fetchone()["n"]
        rec = db.execute("SELECT * FROM matches ORDER BY id DESC LIMIT 6").fetchall()
    rows = [[{"text": f"📢 Post FREE ({qf})", "callback_data": "admin:pub_free", "style": "success"}, {"text": f"💎 Post VIP ({qv})", "callback_data": "admin:pub_vip", "style": "danger"}]]
    txt = "⚽ <b>FIXTURES</b>\n\n"
    for m in rec:
        txt += f"• #{m['id']} [{m['tier']}] {html_escape(m['match_name'])} — {m['result']}\n"
        rows.append([{"text": f"#{m['id']} ✅", "callback_data": f"admin:res:{m['id']}:WON", "style": "success"}, {"text": "❌", "callback_data": f"admin:res:{m['id']}:LOST", "style": "danger"}, {"text": "⚪", "callback_data": f"admin:res:{m['id']}:VOID", "style": "primary"}])
    rows.append([{"text": "⬅️ Admin", "callback_data": "admin:home"}])
    await send_message(chat_id, txt, {"inline_keyboard": rows})


async def show_admin_accumulators(chat_id, user_id, message_id=None):
    if not is_admin(user_id) or str(chat_id) != str(ADMIN_USER_ID):
        return
    with connect_db() as db:
        accs = db.execute("SELECT * FROM accumulators ORDER BY id DESC LIMIT 6").fetchall()
    lines, rows = [], []
    for a in accs:
        lines.append(f"• <b>#{a['id']} [{html_escape(a['category'])}] {html_escape(str(a['total_odds']))}x</b> — {html_escape(a['status'])}")
        r = [{"text": f"📢 Main #{a['id']}", "callback_data": f"admin:accum_pub:{a['id']}:MAIN", "style": "success"}, {"text": f"💎 VIP #{a['id']}", "callback_data": f"admin:accum_pub:{a['id']}:VIP", "style": "primary"}]
        if a["status"] == "PENDING_ADMIN":
            r.append({"text": "❌", "callback_data": f"admin:accum_rej:{a['id']}", "style": "danger"})
        rows.append(r)
    rows.append([{"text": "⬅️ Admin", "callback_data": "admin:home"}])
    await edit_or_send(chat_id, message_id, "🎯 <b>ACCUMULATORS</b>\n\n" + ("\n".join(lines) if lines else "None."), {"inline_keyboard": rows})


async def show_admin_transactions(chat_id, user_id, message_id=None):
    if not is_admin(user_id) or str(chat_id) != str(ADMIN_USER_ID):
        return
    with connect_db() as db:
        pend = db.execute("SELECT * FROM orders WHERE status IN ('PENDING_REVIEW','PENDING_PROOF') ORDER BY created_at DESC LIMIT 6").fetchall()
        ords = db.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT 10").fetchall()
    rows, plines = [], []
    for o in pend:
        plines.append(f"• [{o['market_scope']}] <code>{o['external_ref']}</code> 👤<code>{o['user_id']}</code> | {html_escape(format_order_item_name(o['plan']))}")
        r = [{"text": f"✅ {o['external_ref']}", "callback_data": f"admin:approve:{o['external_ref']}", "style": "success"}, {"text": "❌", "callback_data": f"admin:reject:{o['external_ref']}", "style": "danger"}]
        if o["proof_file_id"]:
            r.append({"text": "👁️", "callback_data": f"admin:viewproof:{o['external_ref']}", "style": "primary"})
        rows.append(r)
    rlines = [f"• [{o['market_scope']}] <code>{o['external_ref']}</code> {html_escape(format_order_item_name(o['plan']))} [{o['status']}]" for o in ords]
    rows.append([{"text": "🔄 Refresh", "callback_data": "admin:transactions"}, {"text": "⬅️ Admin", "callback_data": "admin:home"}])
    pre = ("⏳ <b>PENDING</b>\n" + "\n".join(plines) + "\n\n") if plines else ""
    await edit_or_send(chat_id, message_id, pre + "🧾 <b>RECENT ORDERS</b>\n" + ("\n".join(rlines) if rlines else "None."), {"inline_keyboard": rows})


async def show_admin_users(chat_id, user_id):
    if not is_admin(user_id) or str(chat_id) != str(ADMIN_USER_ID):
        return
    with connect_db() as db:
        us = db.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT 10").fetchall()
    await send_message(chat_id, "👥 <b>USERS</b>\n\n" + "\n".join(f"• <code>{u['user_id']}</code> @{u['username'] or u['first_name']} [{u['country'] or 'Unset'}/{u['currency']}]" for u in us), admin_keyboard(user_id))


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
        ords = db.execute("SELECT * FROM orders ORDER BY created_at DESC").fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["external_ref", "scope", "user_id", "plan", "amount_kes", "amount_usd", "currency", "local_amount", "provider", "status", "receipt", "created_at"])
    for o in ords:
        w.writerow([o["external_ref"], o["market_scope"], o["user_id"], format_order_item_name(o["plan"]), o["amount"], o["amount_usd"], o["currency"], o["local_amount"], o["provider"], o["status"], o["mpesa_receipt"], o["created_at"]])
    await telegram_send_document(chat_id, "prime_tips_empire_orders.csv", buf.getvalue().encode("utf-8"), "📦 Orders CSV")


async def _process_update_safe(update):
    try:
        if "message" in update:
            await handle_message(update["message"])
        elif "callback_query" in update:
            await handle_callback(update["callback_query"])
        elif "chat_join_request" in update:
            await handle_join_request(update["chat_join_request"])
    except Exception:
        log.exception("Update error")


async def background_worker():
    global bot_username
    for _ in range(5):
        try:
            bot_username = (await telegram("getMe", {}))["username"]
            await telegram("setWebhook", {"url": PUBLIC_URL.rstrip("/") + "/telegram", "secret_token": WEBHOOK_SECRET, "allowed_updates": ["message", "callback_query", "chat_join_request"]})
            break
        except Exception:
            await asyncio.sleep(5)
    while True:
        try:
            await asyncio.gather(refresh_exchange_rates(force=False), fetch_cryptapi_catalog(force=False), return_exceptions=True)
            c_iso, cry_iso = (utcnow() - timedelta(minutes=30)).isoformat(), (utcnow() - timedelta(hours=3)).isoformat()
            with connect_db() as db:
                pstk = db.execute("SELECT * FROM orders WHERE status='PENDING' AND provider='mpesa' AND payhero_reference IS NOT NULL AND created_at>=?", (c_iso,)).fetchall()
                pcry = db.execute("SELECT * FROM orders WHERE status='PENDING' AND provider='crypto' AND crypto_ticker IS NOT NULL AND created_at>=?", (cry_iso,)).fetchall()
            for o in pstk:
                try:
                    await finalize_paid_order(o, verified=await verify_payhero_transaction(o["payhero_reference"]))
                except Exception:
                    pass
            for co in pcry:
                try:
                    await verify_cryptapi_order_via_logs(co)
                except Exception:
                    pass
            n_iso, r_iso = utcnow().isoformat(), (utcnow() + timedelta(hours=12)).isoformat()
            with connect_db() as db:
                rems = db.execute("SELECT * FROM subscriptions WHERE status='ACTIVE' AND is_lifetime=0 AND reminded=0 AND expires_at>? AND expires_at<=?", (n_iso, r_iso)).fetchall()
                exps = db.execute("SELECT * FROM subscriptions WHERE status='ACTIVE' AND is_lifetime=0 AND expires_at<=?", (n_iso,)).fetchall()
            for r in rems:
                with connect_db() as db:
                    db.execute("UPDATE subscriptions SET reminded=1 WHERE user_id=?", (r["user_id"],))
                try:
                    await send_message(r["user_id"], f"⏳ Your {PROJECT_NAME} VIP expires soon. Use /start to renew.")
                except Exception:
                    pass
            vc = get_setting("vip_channel_id", VIP_CHANNEL_ID)
            for e in exps:
                try:
                    await telegram("banChatMember", {"chat_id": vc, "user_id": e["user_id"]})
                    await telegram("unbanChatMember", {"chat_id": vc, "user_id": e["user_id"], "only_if_banned": True})
                except Exception:
                    pass
                with connect_db() as db:
                    db.execute("UPDATE subscriptions SET status='EXPIRED' WHERE user_id=?", (e["user_id"],))
        except Exception:
            log.exception("Worker error")
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
cors = [o.strip() for o in CORS_ORIGINS_RAW.split(",") if o.strip()]
if PUBLIC_URL and PUBLIC_URL.rstrip("/") not in cors:
    cors.append(PUBLIC_URL.rstrip("/"))
app.add_middleware(CORSMiddleware, allow_origins=cors, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


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
        dc = curr if curr != "KES" else "USD"
        return {"scope": "INTL", "currency": dc, "packages": [{**dict(p), "display_price": format_usd_price(p["price_usd"], dc)[0]} for p in plans]}
    with connect_db() as db:
        plans = db.execute("SELECT name,title,price,days,benefits,is_lifetime FROM plans ORDER BY price").fetchall()
    return {"scope": "LOCAL", "currency": curr, "packages": [{**dict(p), "display_price": format_converted_price(p["price"], curr)[0]} for p in plans]}


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
    blink = f"https://t.me/{bot_username}?start=vip" if bot_username else "#"
    opts = "".join(f'<option value="{c}">{c}</option>' for c in CURRENCIES)
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{PROJECT_NAME}</title><style>body{{margin:0;font-family:system-ui,sans-serif;background:#06070d;color:#f8fafc;padding:16px}}.hero{{background:#1e1b4b;border:1px solid #f59e0b;border-radius:16px;padding:18px}}.card{{background:#0d142b;border:1px solid #1e293b;border-radius:12px;padding:12px;margin-top:10px}}select{{width:100%;margin-top:8px;padding:9px;border-radius:8px;background:#0f172a;color:#fff;border:1px solid #334155}}.btn{{display:block;text-align:center;margin-top:14px;padding:12px;border-radius:10px;background:#7c3aed;color:#fff;font-weight:700;text-decoration:none}}</style></head><body><div class="hero"><h2>👑 {PROJECT_NAME}</h2><select id="sc" onchange="ld()"><option value="LOCAL">🇰🇪 East Africa Local (KES)</option><option value="INTL">🌍 International ($ USD / Crypto)</option></select><select id="cu" onchange="ld()">{opts}</select></div><div id="bd" class="card">Loading...</div><a class="btn" href="{blink}">⚡ Join VIP in Telegram</a><script>const esc=s=>String(s??'').replace(/[&<>"']/g,m=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[m]));function ld(){{const s=document.getElementById('sc').value,c=document.getElementById('cu').value;Promise.all([fetch('/api/predictions').then(r=>r.json()),fetch('/api/packages?scope='+s+'&currency='+c).then(r=>r.json())]).then(([p,k])=>{{let h=`<b>Win Rate:</b> ${{esc(p.win_rate??'Tracking')}}%<h3>⚽ Free Picks</h3>`;p.free_predictions.forEach(m=>{{h+=`<div class="card"><b>${{esc(m.match_name)}}</b><br>${{esc(m.tip)}} (${{esc(m.odds)}}) • ${{esc(m.result)}}</div>`;}});h+=`<h3>👑 VIP (${{esc(k.scope)}})</h3>`;k.packages.forEach(x=>{{h+=`<div class="card"><b>${{esc(x.title)}}</b> — ${{esc(x.display_price)}}<br><small>${{esc(x.benefits)}}</small></div>`;}});document.getElementById('bd').innerHTML=h;}});}}ld();</script></body></html>"""


@app.post("/telegram")
async def telegram_webhook(request: Request):
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not secrets.compare_digest(secret, WEBHOOK_SECRET):
        raise HTTPException(status_code=403, detail="Unauthorized")
    asyncio.create_task(_process_update_safe(await request.json()))
    return {"ok": True}


@app.post("/payhero/callback")
async def payhero_callback(request: Request):
    try:
        await process_payhero_callback(await request.json())
    except Exception:
        log.exception("PayHero callback failed")
        raise HTTPException(status_code=503, detail="Verification failed")
    return {"ok": True}


@app.api_route("/cryptapi/callback", methods=["GET", "POST"])
async def cryptapi_callback(request: Request):
    qp = dict(request.query_params)
    er, nonce = qp.get("order_ref", ""), qp.get("nonce", "")
    if not er or not nonce:
        return PlainTextResponse("missing params", status_code=400)
    with connect_db() as db:
        order = db.execute("SELECT * FROM orders WHERE external_ref=?", (er,)).fetchone()
    if not order or not order["callback_nonce"] or not secrets.compare_digest(nonce, order["callback_nonce"]):
        return PlainTextResponse("unauthorized", status_code=403)
    if order["status"] == "PAID":
        return PlainTextResponse("*ok*")
    bd = dict(qp)
    if request.method == "POST":
        try:
            if "application/json" in request.headers.get("content-type", ""):
                j = await request.json()
                if isinstance(j, dict):
                    bd.update(j)
            else:
                bd.update(dict(await request.form()))
        except Exception:
            pass
    try:
        if await verify_cryptapi_order_via_logs(order) == "NO_TX" and bd.get("txid_in"):
            await process_cryptapi_event(order, bd)
    except Exception:
        return PlainTextResponse("retry", status_code=503)
    return PlainTextResponse("*ok*")


@app.api_route("/.well-known/cryptapi-verification.txt", methods=["GET", "HEAD"])
async def cryptapi_wellknown_verify():
    return PlainTextResponse(CRYPTAPI_VERIFY_FILE_CONTENT or "cryptapi-verification=00bdc4cdec3ec5d8bcc7577811bc1fb6", status_code=200)


if CRYPTAPI_VERIFY_FILE_NAME and CRYPTAPI_VERIFY_FILE_CONTENT:
    ep = "/" + CRYPTAPI_VERIFY_FILE_NAME.lstrip("/")
    if ep != "/.well-known/cryptapi-verification.txt":
        @app.api_route(ep, methods=["GET", "HEAD"])
        async def cryptapi_domain_verify_file():
            return PlainTextResponse(CRYPTAPI_VERIFY_FILE_CONTENT, status_code=200)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
```

Make sure the bot is an **Administrator** (with the **Invite Users / Add Members** permission) in every channel/group where you want it to auto-approve join requests!

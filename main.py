import os
import io
import re
import csv
import json
import hmac
import hashlib
import asyncio
import secrets
import sqlite3
import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from contextlib import asynccontextmanager, contextmanager
from html import escape as html_escape
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import FastAPI, Request, HTTPException, Header, Query
from fastapi.responses import HTMLResponse
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


def env_str(name, default=""):
    return (os.getenv(name) or default).strip()


BOT_TOKEN = env_str("TELEGRAM_BOT_TOKEN")
ADMIN_USER_ID = env_str("ADMIN_USER_ID")
STAFF_USER_IDS = {x.strip() for x in env_str("STAFF_USER_IDS").split(",") if x.strip().isdigit()}
CHANNEL_ID = env_str("CHANNEL_ID")
VIP_CHANNEL_ID = env_str("VIP_CHANNEL_ID")
MAIN_CHANNEL_URL = env_str("MAIN_CHANNEL_URL")
WEBHOOK_SECRET = env_str("WEBHOOK_SECRET")

PAYHERO_AUTH_TOKEN = env_str("PAYHERO_AUTH_TOKEN")
PAYHERO_CHANNEL_ID = env_str("PAYHERO_CHANNEL_ID")
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
STK_COOLDOWN_SECONDS = 45
FX_CACHE_HOURS = 6
REACTION_EMOJIS = ["🔥", "❤️", "💯", "🙏", "👍", "😍"]

bot_username = None
http_client: httpx.AsyncClient | None = None
seen_media_groups = []

# --------------------------------------------------
# INTERNATIONAL COUNTRIES & 12 SUPPORTED CURRENCIES
# --------------------------------------------------

COUNTRIES = {
    "KE": {"flag": "🇰🇪", "name": "Kenya", "currency": "KES", "tz": "Africa/Nairobi", "methods": ["mpesa", "airtel"], "how": "1️⃣ Select M-Pesa STK Push (Instant).\n2️⃣ Enter your Safaricom number (07/01).\n3️⃣ Confirm PIN on your phone.\n4️⃣ VIP activates automatically after verified callback."},
    "UG": {"flag": "🇺🇬", "name": "Uganda", "currency": "UGX", "tz": "Africa/Kampala", "methods": ["mtn", "airtel"], "how": "1️⃣ Select MTN MoMo or Airtel Money.\n2️⃣ Contact @isla_olivia0 for mobile money payment details.\n3️⃣ Send payment & submit Phone | TransactionID for admin verification."},
    "TZ": {"flag": "🇹🇿", "name": "Tanzania", "currency": "TZS", "tz": "Africa/Dar_es_Salaam", "methods": ["mpesa_tz", "airtel", "tigo"], "how": "1️⃣ Select M-Pesa TZ, Airtel, or Mixx/Tigo Pesa.\n2️⃣ Request payment number via @isla_olivia0.\n3️⃣ Submit Phone | TransactionID for verification."},
    "NG": {"flag": "🇳🇬", "name": "Nigeria", "currency": "NGN", "tz": "Africa/Lagos", "methods": ["ng_bank"], "how": "1️⃣ Select Nigeria Bank / OPay / PalmPay.\n2️⃣ Contact @isla_olivia0 for Naira transfer details.\n3️⃣ Submit Sender/Phone | Reference for verification."},
    "GH": {"flag": "🇬🇭", "name": "Ghana", "currency": "GHS", "tz": "Africa/Accra", "methods": ["mtn", "airtel"], "how": "1️⃣ Select MTN MoMo Ghana or AirtelTigo.\n2️⃣ Contact @isla_olivia0 for payment details.\n3️⃣ Submit Phone | TransactionID for verification."},
    "ZA": {"flag": "🇿🇦", "name": "South Africa", "currency": "ZAR", "tz": "Africa/Johannesburg", "methods": ["za_eft"], "how": "1️⃣ Select Instant EFT / Capitec.\n2️⃣ Contact @isla_olivia0 for EFT details.\n3️⃣ Submit Contact | Reference for verification."},
    "US": {"flag": "🇺🇸", "name": "United States", "currency": "USD", "tz": "America/New_York", "methods": ["intl_transfer"], "how": "1️⃣ Select International Card / PayPal / USDT.\n2️⃣ Contact @isla_olivia0 for checkout details.\n3️⃣ Submit Email/Wallet | Transaction Reference."},
    "GB": {"flag": "🇬🇧", "name": "United Kingdom", "currency": "GBP", "tz": "Europe/London", "methods": ["intl_transfer"], "how": "1️⃣ Select UK Bank / PayPal / USDT.\n2️⃣ Contact @isla_olivia0 for payment details.\n3️⃣ Submit Contact | Reference for verification."},
    "EU": {"flag": "🇪🇺", "name": "Europe (Eurozone)", "currency": "EUR", "tz": "Europe/Berlin", "methods": ["intl_transfer"], "how": "1️⃣ Select SEPA / PayPal / USDT.\n2️⃣ Contact @isla_olivia0 for payment details.\n3️⃣ Submit Contact | Reference for verification."},
    "IN": {"flag": "🇮🇳", "name": "India", "currency": "INR", "tz": "Asia/Kolkata", "methods": ["intl_transfer"], "how": "1️⃣ Select UPI / International Transfer / USDT.\n2️⃣ Contact @isla_olivia0 for payment details.\n3️⃣ Submit Contact | Reference for verification."},
    "CA": {"flag": "🇨🇦", "name": "Canada", "currency": "CAD", "tz": "America/Toronto", "methods": ["intl_transfer"], "how": "1️⃣ Select Interac / PayPal / USDT.\n2️⃣ Contact @isla_olivia0 for payment details.\n3️⃣ Submit Contact | Reference for verification."},
    "AU": {"flag": "🇦🇺", "name": "Australia", "currency": "AUD", "tz": "Australia/Sydney", "methods": ["intl_transfer"], "how": "1️⃣ Select PayID / PayPal / USDT.\n2️⃣ Contact @isla_olivia0 for payment details.\n3️⃣ Submit Contact | Reference for verification."},
}

CURRENCIES = {
    "KES": {"symbol": "KES ", "decimals": 0},
    "UGX": {"symbol": "UGX ", "decimals": 0},
    "TZS": {"symbol": "TZS ", "decimals": 0},
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
    "mpesa": ("🇰🇪 M-Pesa STK Push (Instant)", "success"),
    "airtel": ("🔴 Airtel Money", "danger"),
    "mtn": ("🟡 MTN Mobile Money", "primary"),
    "mpesa_tz": ("🇹🇿 Vodacom M-Pesa TZ", "danger"),
    "tigo": ("🔵 Mixx / Tigo Pesa", "primary"),
    "ng_bank": ("🇳🇬 Bank / OPay / PalmPay", "success"),
    "za_eft": ("🇿🇦 Instant EFT / Capitec", "primary"),
    "intl_transfer": ("💲 International / PayPal / USDT", "success"),
}

ACCUM_CATEGORIES = ["3-5 Odds", "5-10 Odds", "10-15 Odds", "15-20 Odds", "20+ Odds"]


def check_settings():
    settings = {
        "TELEGRAM_BOT_TOKEN": BOT_TOKEN,
        "ADMIN_USER_ID": ADMIN_USER_ID,
        "CHANNEL_ID": CHANNEL_ID,
        "VIP_CHANNEL_ID": VIP_CHANNEL_ID,
        "WEBHOOK_SECRET": WEBHOOK_SECRET,
        "PAYHERO_AUTH_TOKEN": PAYHERO_AUTH_TOKEN,
        "PAYHERO_CHANNEL_ID": PAYHERO_CHANNEL_ID,
        "PUBLIC_URL / RENDER_EXTERNAL_URL": PUBLIC_URL,
    }
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
                url TEXT NOT NULL
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
        ensure_column(db, "orders", "currency", "TEXT NOT NULL DEFAULT 'KES'")
        ensure_column(db, "orders", "local_amount", "TEXT NOT NULL DEFAULT ''")
        ensure_column(db, "subscriptions", "is_lifetime", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(db, "subscriptions", "reminded", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(db, "matches", "market", "TEXT NOT NULL DEFAULT '1X2'")

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
            "referral_reward_days": "2",
            "vip_odds_info": "🔥 Daily VIP Matches: Curated Football Picks & Multi-Bet Tickets",
            "admin_contact_url": f"https://t.me/{BRAND_TAG.lstrip('@')}",
            "website_url": WEBSITE_URL_ENV or DEFAULT_WEBSITE_URL,
            "auto_channel_buttons": "0",
        }
        for k, v in default_settings.items():
            db.execute("INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO NOTHING", (k, v))

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
    return row["value"] if row else fallback


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


def is_super_admin(user_id):
    return str(user_id) == str(ADMIN_USER_ID)


def is_staff(user_id):
    return is_super_admin(user_id) or str(user_id) in STAFF_USER_IDS


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
            # Never auto-assign a country without explicit user confirmation
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


async def answer_callback(callback_id, text=""):
    try:
        await telegram("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})
    except Exception:
        log.exception("Could not answer callback")


# --------------------------------------------------
# KEYBOARDS (10-BUTTON MENU + GENUINE REACTIONS)
# --------------------------------------------------

def main_menu_keyboard(user_id=None):
    ch_url = get_setting("main_channel_url") or get_website_url()
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
        [
            {"text": "🌐 Open Empire Web Portal", "web_app": {"url": PUBLIC_URL.rstrip("/") + "/app"}, "style": "primary"},
        ],
    ]
    if user_id and is_staff(user_id):
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


def channel_post_keyboard(channel_msg_id=None, include_custom=False, include_reactions=True):
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

    rows.append([
        {"text": "👑 JOIN VIP NOW", "url": join_url, "style": "danger"},
        {"text": "💬 Contact Admin", "url": contact_url, "style": "success"},
    ])
    if website_url:
        short_domain = re.sub(r"^https?://", "", website_url).rstrip("/")
        rows.append([{"text": f"🌐 {short_domain}", "url": website_url, "style": "primary"}])

    if include_custom:
        with connect_db() as db:
            buttons = db.execute("SELECT * FROM custom_buttons ORDER BY id").fetchall()
        for b in buttons:
            rows.append([{"text": b["label"], "url": b["url"], "style": "primary"}])

    return {"inline_keyboard": rows}


def admin_keyboard(user_id):
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
            {"text": "📝 Post to Channel", "callback_data": "admin:post_channel", "style": "success"},
        ],
        [
            {"text": "📢 VIP Promo Post", "callback_data": "admin:post", "style": "danger"},
            {"text": "📣 Broadcast", "callback_data": "admin:broadcast", "style": "danger"},
        ],
    ]
    if is_super_admin(user_id):
        rows.append([
            {"text": "💰 Manage VIP Plans", "callback_data": "admin:packages", "style": "success"},
            {"text": "⚙️ Channels & Settings", "callback_data": "admin:channels", "style": "primary"},
        ])
        rows.append([
            {"text": "📥 Export CSV", "callback_data": "admin:export", "style": "success"},
            {"text": "🛡️ Audit Logs", "callback_data": "admin:audit", "style": "primary"},
        ])
    rows.append([{"text": "🏠 Main Menu", "callback_data": "menu:home"}])
    return {"inline_keyboard": rows}


# --------------------------------------------------
# PAYMENTS (PayHero M-Pesa STK + Verified International Review)
# --------------------------------------------------

def payhero_headers():
    token = PAYHERO_AUTH_TOKEN.strip()
    authorization = token if token.lower().startswith("basic ") else f"Basic {token}"
    return {"Authorization": authorization, "Content-Type": "application/json"}


def normalize_phone(phone):
    phone = re.sub(r"[\s+\-]", "", phone)
    if phone.startswith("254") and len(phone) == 12:
        phone = "0" + phone[3:]
    if (phone.startswith("7") or phone.startswith("1")) and len(phone) == 9:
        phone = "0" + phone
    return phone if re.fullmatch(r"0[17]\d{8}", phone) else None


def get_plan(plan_name):
    with connect_db() as db:
        return db.execute("SELECT * FROM plans WHERE name=?", (plan_name,)).fetchone()


def mark_order_failed(external_ref):
    with connect_db() as db:
        db.execute("UPDATE orders SET status='FAILED' WHERE external_ref=? AND status='PENDING'", (external_ref,))


def recent_stk_cooldown_remaining(user_id):
    with connect_db() as db:
        last = db.execute(
            "SELECT created_at FROM orders WHERE user_id=? AND provider='mpesa' ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    if not last:
        return 0
    return max(0, int(STK_COOLDOWN_SECONDS - (utcnow() - parse_utc(last["created_at"])).total_seconds()))


async def start_mpesa_payment(user_id, plan_name, phone, notify_telegram=True):
    plan = get_plan(plan_name)
    if not plan:
        if notify_telegram:
            await send_message(user_id, "⚠️ VIP plan unavailable.")
        return {"ok": False, "error": "Plan unavailable"}

    wait_sec = recent_stk_cooldown_remaining(user_id)
    if wait_sec > 0:
        msg = f"Please wait {wait_sec}s before requesting another M-Pesa prompt."
        if notify_telegram:
            await send_message(user_id, f"⏳ {msg}")
        return {"ok": False, "error": msg}

    external_ref = "PTE" + secrets.token_hex(7).upper()
    amount = int(plan["price"])
    ph_channel = int(get_setting("payhero_channel_id", PAYHERO_CHANNEL_ID))
    plan_title = plan["title"] or f"{plan_name.title()} VIP"

    with connect_db() as db:
        db.execute(
            """
            INSERT INTO orders(external_ref,user_id,plan,amount,currency,local_amount,phone,provider,status,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (external_ref, user_id, plan_name, amount, "KES", f"KES {amount:,}", phone, "mpesa", "PENDING", utcnow().isoformat()),
        )

    payload = {
        "amount": amount,
        "phone_number": phone,
        "channel_id": ph_channel,
        "provider": "m-pesa",
        "external_reference": external_ref,
        "callback_url": PUBLIC_URL.rstrip("/") + "/payhero/callback",
    }

    try:
        client = await get_http()
        response = await client.post(PAYHERO_URL + "/payments", headers=payhero_headers(), json=payload)
        result = response.json()

        if response.status_code not in (200, 201) or not result.get("success"):
            log.error("PayHero error (%s): %s", response.status_code, result)
            mark_order_failed(external_ref)
            if notify_telegram:
                await send_message(user_id, f"❌ <b>M-Pesa request could not be started.</b>\nContact {BRAND_TAG}")
            return {"ok": False, "error": "PayHero rejected payment"}

        reference = result.get("reference")
        checkout_id = result.get("CheckoutRequestID")
        if not reference:
            mark_order_failed(external_ref)
            raise RuntimeError("Missing PayHero reference")

        with connect_db() as db:
            db.execute("UPDATE orders SET payhero_reference=?, checkout_id=? WHERE external_ref=?", (reference, checkout_id, external_ref))

        if notify_telegram:
            await send_message(
                user_id,
                "📲 <b>M-PESA STK PUSH SENT!</b>\n\n"
                f"👑 Plan: <b>{html_escape(plan_title)}</b>\n"
                f"💳 Transaction Amount: <b>KES {amount:,}</b>\n"
                f"📞 Phone: <code>{html_escape(phone)}</code>\n"
                f"🔗 Ref: <code>{external_ref}</code>\n\n"
                "🔐 Enter your M-Pesa PIN. VIP activates automatically after verified callback.",
                {
                    "inline_keyboard": [
                        [{"text": "🔄 Verify Payment Status", "callback_data": f"verify:{external_ref}", "style": "success"}],
                        [{"text": "🏠 Main Menu", "callback_data": "menu:home", "style": "primary"}],
                    ]
                },
            )
        return {"ok": True, "external_ref": external_ref, "status": "PENDING"}
    except Exception:
        log.exception("Payment initiation failed")
        mark_order_failed(external_ref)
        if notify_telegram:
            await send_message(user_id, f"⚠️ Payment could not start. Contact {BRAND_TAG}")
        return {"ok": False, "error": "Payment initiation failed"}


async def submit_international_reference(user_id, plan_name, provider, text_input):
    plan = get_plan(plan_name)
    if not plan:
        await send_message(user_id, "⚠️ VIP plan unavailable.")
        return

    parts = [p.strip() for p in text_input.split("|")]
    if len(parts) != 2 or not parts[0] or not parts[1]:
        await send_message(user_id, "⚠️ Format: <code>PhoneOrEmail | TransactionID</code>")
        return

    contact_val, txn_ref = parts[0][:50], parts[1][:60]
    prefs = get_user_prefs(user_id)
    local_str, _ = format_converted_price(plan["price"], prefs["currency"])
    external_ref = "INT" + secrets.token_hex(6).upper()
    amount = int(plan["price"])

    with connect_db() as db:
        db.execute(
            """
            INSERT INTO orders(external_ref,user_id,plan,amount,currency,local_amount,phone,provider,status,mpesa_receipt,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (external_ref, user_id, plan_name, amount, prefs["currency"], local_str, contact_val, provider, "PENDING_REVIEW", txn_ref, utcnow().isoformat()),
        )

    await send_message(
        user_id,
        "⏳ <b>PAYMENT SUBMITTED FOR VERIFICATION</b>\n\n"
        f"🔗 Order Ref: <code>{external_ref}</code>\n"
        f"🧾 Txn ID: <code>{html_escape(txn_ref)}</code>\n\n"
        "🔒 Subscriptions are activated only after transaction verification (never from screenshots alone).",
        {"inline_keyboard": [[{"text": "🏠 Main Menu", "callback_data": "menu:home", "style": "primary"}]]},
    )

    await send_message(
        ADMIN_USER_ID,
        f"🔔 <b>PAYMENT REVIEW ({html_escape(provider.upper())})</b>\n\n"
        f"🆔 User: <code>{user_id}</code>\n"
        f"👑 Plan: <b>{html_escape(plan['title'])}</b> — {html_escape(local_str)}\n"
        f"📞 Contact: <code>{html_escape(contact_val)}</code>\n"
        f"🧾 Txn ID: <code>{html_escape(txn_ref)}</code>\n"
        f"🔗 Ref: <code>{external_ref}</code>",
        {
            "inline_keyboard": [[
                {"text": "✅ Verify & Activate VIP", "callback_data": f"admin:approve:{external_ref}", "style": "success"},
                {"text": "❌ Reject", "callback_data": f"admin:reject:{external_ref}", "style": "danger"},
            ]]
        },
    )


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
# VIP ACCESS & ORDER FINALIZATION
# --------------------------------------------------

async def give_vip_access(user_id, plan_name, days_override=None, receipt=None, is_lifetime_override=False):
    if days_override is not None:
        days = int(days_override)
        display_title = f"{days}-Day VIP"
        is_lifetime = 1 if is_lifetime_override else 0
    else:
        plan = get_plan(plan_name)
        if not plan:
            return
        days = int(plan["days"])
        display_title = plan["title"] or f"{plan_name.title()} VIP"
        is_lifetime = int(plan["is_lifetime"])

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
        receipt_line = f"🧾 Receipt: <code>{html_escape(receipt)}</code>\n" if receipt else ""

        await send_message(
            user_id,
            f"🎉 <b>VIP MEMBERSHIP ACTIVATED!</b>\n\n"
            f"👑 <b>{PROJECT_NAME} ({BRAND_TAG})</b>\n"
            f"💎 Plan: <b>{html_escape(display_title)}</b>\n"
            f"{receipt_line}"
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


async def finalize_paid_order(order, verified=None, callback_data=None, manual_admin=False):
    external_ref = order["external_ref"]
    if not manual_admin:
        status = str((verified or {}).get("status", "")).upper()
        if status == "FAILED":
            with connect_db() as db:
                cur = db.execute("UPDATE orders SET status='FAILED' WHERE external_ref=? AND status='PENDING'", (external_ref,))
            if cur.rowcount > 0:
                try:
                    await send_message(int(order["user_id"]), "❌ <b>Payment failed or was cancelled.</b>", main_menu_keyboard(int(order["user_id"])))
                except Exception:
                    pass
            return "FAILED"
        if status != "SUCCESS":
            return "PENDING"
        if callback_data and callback_data.get("Amount") is not None:
            try:
                if int(float(callback_data["Amount"])) != int(order["amount"]):
                    return "PENDING"
            except (ValueError, TypeError):
                return "PENDING"

    receipt = (callback_data or {}).get("MpesaReceiptNumber") or (verified or {}).get("provider_reference") or order["mpesa_receipt"]
    with connect_db() as db:
        cur = db.execute(
            "UPDATE orders SET status='PAID', mpesa_receipt=COALESCE(?, mpesa_receipt) WHERE external_ref=? AND status IN ('PENDING','PENDING_REVIEW')",
            (receipt, external_ref),
        )
        updated = cur.rowcount
    if updated == 0:
        return "ALREADY_PAID"

    await give_vip_access(int(order["user_id"]), order["plan"], receipt=receipt)
    amt_display = order["local_amount"] or f"KES {order['amount']}"
    await send_message(
        ADMIN_USER_ID,
        f"✅ <b>{PROJECT_NAME} PAYMENT CONFIRMED</b>\n\n"
        f"🆔 User: <code>{order['user_id']}</code>\n"
        f"💼 Plan: <b>{html_escape(order['plan'].title())}</b> — {html_escape(amt_display)}\n"
        f"🧾 Receipt: <code>{html_escape(receipt or 'N/A')}</code>",
    )
    return "PAID"


async def process_payhero_callback(payload):
    data = payload.get("response", payload)
    external_ref = data.get("ExternalReference")
    if not external_ref:
        return
    with connect_db() as db:
        order = db.execute("SELECT * FROM orders WHERE external_ref=?", (external_ref,)).fetchone()
    if not order or order["status"] == "PAID" or not order["payhero_reference"]:
        return
    verified = await verify_payhero_transaction(order["payhero_reference"])
    await finalize_paid_order(order, verified=verified, callback_data=data)


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


async def publish_match_batch(tier, match_ids=None):
    global bot_username
    with connect_db() as db:
        if match_ids:
            ph = ",".join(["?"] * len(match_ids))
            rows = db.execute(f"SELECT * FROM matches WHERE id IN ({ph}) ORDER BY id", tuple(match_ids)).fetchall()
        else:
            rows = db.execute("SELECT * FROM matches WHERE tier=? AND publish_status='QUEUED' ORDER BY id", (tier,)).fetchall()
    if not rows:
        return None
    if not bot_username:
        bot_username = (await telegram("getMe", {}))["username"]

    target_chat = get_setting("vip_channel_id", VIP_CHANNEL_ID) if tier == "VIP" else get_setting("main_channel_id", CHANNEL_ID)
    full_text, _ = render_batch_text(tier, rows)
    msg = await telegram("sendMessage", {
        "chat_id": target_chat,
        "parse_mode": "HTML",
        "text": full_text[:4090],
        "reply_markup": channel_post_keyboard(include_reactions=False),
        "disable_web_page_preview": True,
    })
    msg_id = msg["message_id"]
    with connect_db() as db:
        for r in rows:
            db.execute("UPDATE matches SET publish_status='PUBLISHED', channel_msg_id=? WHERE id=?", (msg_id, r["id"]))

    if tier == "FREE":
        try:
            await telegram("editMessageReplyMarkup", {
                "chat_id": target_chat,
                "message_id": msg_id,
                "reply_markup": channel_post_keyboard(channel_msg_id=msg_id, include_reactions=True),
            })
        except Exception:
            pass
    return msg


async def publish_vip_post():
    global bot_username
    if not bot_username:
        bot_username = (await telegram("getMe", {}))["username"]
    main_chat = get_setting("main_channel_id", CHANNEL_ID)
    msg = await telegram("sendMessage", {
        "chat_id": main_chat,
        "parse_mode": "HTML",
        "text": (
            f"👑 <b>{PROJECT_NAME}</b> ({BRAND_TAG})\n\n"
            f"🎯 {html_escape(get_setting('vip_odds_info'))}\n\n"
            "⚡ Data-driven football analysis, VIP picks & accumulator tickets.\n"
            "🌍 Supports international currencies & verified payments.\n\n"
            "👇 <b>TAP BELOW TO JOIN VIP OR CONTACT ADMIN</b>"
        ),
        "reply_markup": channel_post_keyboard(include_custom=True, include_reactions=False),
    })
    try:
        await telegram("editMessageReplyMarkup", {
            "chat_id": main_chat,
            "message_id": msg["message_id"],
            "reply_markup": channel_post_keyboard(channel_msg_id=msg["message_id"], include_custom=True, include_reactions=True),
        })
    except Exception:
        pass
    return msg


async def publish_custom_main_post(text_body="", photo_file_id=None):
    global bot_username
    if not bot_username:
        bot_username = (await telegram("getMe", {}))["username"]
    main_chat = get_setting("main_channel_id", CHANNEL_ID)
    formatted = f"{html_escape(text_body)}\n\n🔥 <b>{PROJECT_NAME}</b> ({BRAND_TAG})" if text_body else f"🔥 <b>{PROJECT_NAME}</b> ({BRAND_TAG})"

    if photo_file_id:
        msg = await telegram("sendPhoto", {
            "chat_id": main_chat,
            "photo": photo_file_id,
            "caption": formatted[:1020],
            "parse_mode": "HTML",
            "reply_markup": channel_post_keyboard(include_reactions=False),
        })
    else:
        msg = await telegram("sendMessage", {
            "chat_id": main_chat,
            "parse_mode": "HTML",
            "text": formatted[:4090],
            "reply_markup": channel_post_keyboard(include_reactions=False),
            "disable_web_page_preview": True,
        })
    try:
        await telegram("editMessageReplyMarkup", {
            "chat_id": main_chat,
            "message_id": msg["message_id"],
            "reply_markup": channel_post_keyboard(channel_msg_id=msg["message_id"], include_reactions=True),
        })
    except Exception:
        pass
    return msg


# --------------------------------------------------
# USER VIEWS (WELCOME, COUNTRY, ACCUMULATOR, VIP, STATS)
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
    prefs = get_user_prefs(user_id)
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
# COMMAND & MESSAGE HANDLER
# --------------------------------------------------

async def handle_message(message):
    user = message.get("from", {})
    user_id = user.get("id")
    chat_id = message.get("chat", {}).get("id")
    photo_list = message.get("photo") or []
    photo_file_id = photo_list[-1]["file_id"] if photo_list else None
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
    if db_user and db_user["is_blocked"] and not is_super_admin(user_id):
        await send_message(chat_id, "🚫 Your account has been suspended.")
        return

    # Handle shared GPS location ONLY when user explicitly opted in
    if message.get("location"):
        sess = get_session(user_id)
        if sess and sess["state"] == "WAIT_LOCATION_PERM":
            clear_session(user_id)
            lat = message["location"]["latitude"]
            lon = message["location"]["longitude"]
            detected_code = await reverse_geocode_country(lat, lon)
            # Remove reply keyboard cleanly
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
            if " vip" in text.lower():
                await show_vip_screen(chat_id, user_id)
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

        if cmd == "/admin":
            if not is_staff(user_id):
                await send_message(chat_id, "⛔ Unauthorized.")
                return
            await send_message(chat_id, f"⚡ <b>{PROJECT_NAME} ADMIN</b> ({BRAND_TAG})", admin_keyboard(user_id))
            return

        if cmd == "/reply" and is_staff(user_id):
            parts = text.split(None, 2)
            if len(parts) < 3 or not parts[1].isdigit():
                await send_message(chat_id, "Usage: <code>/reply &lt;user_id&gt; &lt;message&gt;</code>")
                return
            await send_message(int(parts[1]), f"💬 <b>ADMIN REPLY ({BRAND_TAG})</b>\n\n{html_escape(parts[2])}")
            await send_message(chat_id, "✅ Reply sent.")
            return

        if cmd == "/addmatch" and is_staff(user_id):
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

        if cmd == "/setprice" and is_super_admin(user_id):
            parts = text.split()
            if len(parts) != 3 or not parts[2].isdigit() or not get_plan(parts[1].lower()):
                await send_message(chat_id, "Usage: <code>/setprice monthly 1000</code>")
                return
            with connect_db() as db:
                db.execute("UPDATE plans SET price=? WHERE name=?", (int(parts[2]), parts[1].lower()))
            log_audit(user_id, "SET_PRICE", f"{parts[1]}={parts[2]}")
            await send_message(chat_id, f"✅ Updated {parts[1]} to KES {parts[2]}.")
            return

        if cmd == "/grant" and is_super_admin(user_id):
            parts = text.split()
            if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
                await send_message(chat_id, "Usage: <code>/grant &lt;user_id&gt; &lt;days&gt;</code>")
                return
            await give_vip_access(int(parts[1]), f"{parts[2]}-day", days_override=int(parts[2]))
            log_audit(user_id, "GRANT_VIP", f"{parts[1]} for {parts[2]}d")
            await send_message(chat_id, "✅ VIP granted.")
            return

        if cmd == "/revoke" and is_super_admin(user_id):
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

        if cmd == "/export" and is_super_admin(user_id):
            await export_csv_backups(chat_id)
            return

        if cmd == "/setsetting" and is_super_admin(user_id):
            parts = text.split(None, 2)
            allowed = {"main_channel_id", "vip_channel_id", "main_channel_url", "payhero_channel_id", "vip_odds_info", "admin_contact_url", "website_url"}
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

        if state.startswith("WAIT_INTL_"):
            provider = state.replace("WAIT_INTL_", "").lower()
            clear_session(user_id)
            await submit_international_reference(user_id, payload, provider, text)
            return

        if state == "WAIT_SUPPORT_MSG":
            clear_session(user_id)
            kb = {"inline_keyboard": [[{"text": "💬 Reply", "callback_data": f"admin:reply:{user_id}", "style": "primary"}]]}
            if photo_file_id:
                await telegram("sendPhoto", {"chat_id": ADMIN_USER_ID, "photo": photo_file_id, "caption": f"📩 Support photo from <code>{user_id}</code>\n{html_escape(text)}"[:1020], "parse_mode": "HTML", "reply_markup": kb})
            else:
                await send_message(ADMIN_USER_ID, f"📩 <b>SUPPORT MESSAGE</b>\nFrom: <code>{user_id}</code>\n\n{html_escape(text)}", kb)
            await send_message(chat_id, f"✅ Sent to admin ({BRAND_TAG}).", main_menu_keyboard(user_id))
            return

        if state == "WAIT_ADMIN_REPLY" and is_staff(user_id):
            clear_session(user_id)
            await send_message(int(payload), f"💬 <b>ADMIN REPLY ({BRAND_TAG})</b>\n\n{html_escape(text)}")
            await send_message(chat_id, "✅ Reply sent.", admin_keyboard(user_id))
            return

        if state == "WAIT_CHANNEL_POST" and is_staff(user_id):
            clear_session(user_id)
            await publish_custom_main_post(text, photo_file_id=photo_file_id)
            log_audit(user_id, "CHANNEL_POST", text[:80])
            await send_message(chat_id, "✅ Published to channel with genuine reaction buttons!", admin_keyboard(user_id))
            return

        if state == "WAIT_BROADCAST" and is_staff(user_id):
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

        if state == "WAIT_PLAN_PRICE" and is_super_admin(user_id):
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
# CALLBACK HANDLER (MENU, REACTIONS, COUNTRY, ACCUM, ADMIN)
# --------------------------------------------------

async def handle_callback(query):
    cid = query["id"]
    user = query["from"]
    user_id = user["id"]
    data = query.get("data", "")
    msg_obj = query.get("message") or {}
    chat_id = msg_obj.get("chat", {}).get("id") or user_id

    # Genuine channel post reaction button tap
    if data.startswith("react:"):
        idx = int(data.split(":", 1)[1])
        if 0 <= idx < len(REACTION_EMOJIS) and msg_obj.get("message_id"):
            c_msg_id = msg_obj["message_id"]
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
                await telegram("editMessageReplyMarkup", {
                    "chat_id": msg_obj["chat"]["id"],
                    "message_id": c_msg_id,
                    "reply_markup": channel_post_keyboard(channel_msg_id=c_msg_id, include_reactions=True),
                })
            except Exception:
                pass
        else:
            await answer_callback(cid)
        return

    register_user(user)
    db_user = get_user(user_id)
    if db_user and db_user["is_blocked"] and not is_super_admin(user_id):
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
        prefs = get_user_prefs(user_id)
        with connect_db() as db:
            sub = db.execute("SELECT * FROM subscriptions WHERE user_id=?", (user_id,)).fetchone()
            orders = db.execute("SELECT * FROM orders WHERE user_id=? ORDER BY created_at DESC LIMIT 6", (user_id,)).fetchall()
        sub_txt = f"✅ Active ({sub['plan'].title()}) — Expires: {'Lifetime' if sub['is_lifetime'] else format_local_time(sub['expires_at'], prefs['tz'])}" if (sub and sub["status"] == "ACTIVE") else "❌ Inactive"
        ord_txt = "\n".join(f"• {o['plan'].title()} — {o['local_amount'] or o['amount']} [{o['status']}]" for o in orders) or "No orders yet."
        await send_message(chat_id, f"💳 <b>MY SUBSCRIPTION</b>\n\nStatus: <b>{sub_txt}</b>\n\n<b>Recent Payments:</b>\n{ord_txt}", {"inline_keyboard": [[{"text": "👑 VIP Membership", "callback_data": "menu:vip", "style": "danger"}], [{"text": "🏠 Main Menu", "callback_data": "menu:home"}]]})
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
        await send_message(chat_id, f"💬 <b>CONTACT ADMIN ({BRAND_TAG})</b>\n\nSend your message (or photo) below, or /cancel:")
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
        rows = [[{"text": METHOD_LABELS[m][0], "callback_data": f"pay:{plan_name}:{m}", "style": METHOD_LABELS[m][1]}] for m in c["methods"]]
        rows.append([{"text": "⬅️ Back", "callback_data": "menu:vip"}])
        await send_message(chat_id, f"💳 <b>{html_escape(plan['title'])}</b> — {disp}\n\n<b>Payment Guide ({c['name']}):</b>\n{c['how']}", {"inline_keyboard": rows})
        return

    if data.startswith("pay:"):
        _, plan_name, provider = data.split(":", 2)
        await answer_callback(cid)
        if provider == "mpesa":
            set_session(user_id, "WAIT_MPESA_PHONE", plan_name)
            await send_message(chat_id, "📱 Enter your Kenyan Safaricom number (<code>0712345678</code>) or /cancel:")
        else:
            set_session(user_id, f"WAIT_INTL_{provider.upper()}", plan_name)
            await send_message(chat_id, f"💳 Contact {BRAND_TAG} for {provider.upper()} details, then reply with:\n<code>YourPhoneOrEmail | TransactionID</code>")
        return

    if data.startswith("verify:"):
        ext_ref = data.split(":", 1)[1]
        with connect_db() as db:
            order = db.execute("SELECT * FROM orders WHERE external_ref=? AND user_id=?", (ext_ref, user_id)).fetchone()
        if not order or not order["payhero_reference"]:
            await answer_callback(cid, "Processing...")
            return
        await answer_callback(cid, "Checking...")
        verified = await verify_payhero_transaction(order["payhero_reference"])
        res = await finalize_paid_order(order, verified=verified)
        if res == "PENDING":
            await send_message(user_id, "⏳ Still pending on M-Pesa. Tap Verify again shortly.")
        return

    if data.startswith("admin:"):
        if not is_staff(user_id):
            await answer_callback(cid, "Unauthorized")
            return
        parts = data.split(":")
        action = parts[1]
        await answer_callback(cid)

        if action == "home":
            await send_message(chat_id, f"⚡ <b>{PROJECT_NAME} ADMIN</b>", admin_keyboard(user_id))
        elif action == "stats":
            await show_admin_stats(chat_id, user_id)
        elif action == "matches":
            await show_admin_matches(chat_id, user_id)
        elif action == "pub_free":
            await publish_match_batch("FREE")
            await send_message(chat_id, "✅ Published queued FREE matches!", admin_keyboard(user_id))
        elif action == "pub_vip":
            await publish_match_batch("VIP")
            await send_message(chat_id, "✅ Published queued VIP matches!", admin_keyboard(user_id))
        elif action == "res" and len(parts) == 4:
            mid, new_res = int(parts[2]), parts[3]
            with connect_db() as db:
                db.execute("UPDATE matches SET result=? WHERE id=?", (new_res, mid))
            log_audit(user_id, "SET_MATCH_RESULT", f"{mid}={new_res}")
            await sync_channel_message_for_match(mid)
            await show_admin_matches(chat_id, user_id)
        elif action == "accums":
            with connect_db() as db:
                accums = db.execute("SELECT * FROM accumulators ORDER BY id DESC LIMIT 8").fetchall()
            txt = "🎯 <b>ACCUMULATOR TICKETS</b>\n\n" + ("\n".join(f"• #{a['id']} [{a['category']}] {a['total_odds']}x — {a['status']}" for a in accums) or "No tickets yet.")
            await send_message(chat_id, txt, admin_keyboard(user_id))
        elif action == "transactions":
            await show_admin_transactions(chat_id, user_id)
        elif action == "approve" and len(parts) == 3 and is_super_admin(user_id):
            with connect_db() as db:
                order = db.execute("SELECT * FROM orders WHERE external_ref=?", (parts[2],)).fetchone()
            if order:
                await finalize_paid_order(order, manual_admin=True)
                log_audit(user_id, "APPROVE_ORDER", parts[2])
                await send_message(chat_id, f"✅ Approved {parts[2]}.")
        elif action == "reject" and len(parts) == 3 and is_super_admin(user_id):
            with connect_db() as db:
                db.execute("UPDATE orders SET status='FAILED' WHERE external_ref=?", (parts[2],))
            log_audit(user_id, "REJECT_ORDER", parts[2])
            await send_message(chat_id, f"🚫 Rejected {parts[2]}.")
        elif action == "users":
            await show_admin_users(chat_id, user_id)
        elif action == "post_channel":
            set_session(user_id, "WAIT_CHANNEL_POST")
            await send_message(chat_id, "📝 Send text or photo+caption to publish to the Main Channel:")
        elif action == "post":
            await publish_vip_post()
            await send_message(chat_id, "✅ VIP Promo published.", admin_keyboard(user_id))
        elif action == "broadcast":
            set_session(user_id, "WAIT_BROADCAST")
            await send_message(chat_id, "📣 Send broadcast text or photo+caption:")
        elif action == "reply" and len(parts) == 3:
            set_session(user_id, "WAIT_ADMIN_REPLY", parts[2])
            await send_message(chat_id, f"💬 Type reply to user <code>{parts[2]}</code>:")
        elif action == "packages" and is_super_admin(user_id):
            await show_admin_packages(chat_id)
        elif action == "editprice" and len(parts) == 3 and is_super_admin(user_id):
            set_session(user_id, "WAIT_PLAN_PRICE", parts[2])
            await send_message(chat_id, f"💰 Enter new KES price for <b>{parts[2]}</b>:")
        elif action == "channels" and is_super_admin(user_id):
            await send_message(
                chat_id,
                f"⚙️ <b>SETTINGS</b>\nMain: <code>{get_setting('main_channel_id')}</code>\nVIP: <code>{get_setting('vip_channel_id')}</code>\nWebsite: <code>{get_website_url()}</code>\n\nUse <code>/setsetting &lt;key&gt; &lt;value&gt;</code>",
                admin_keyboard(user_id),
            )
        elif action == "export" and is_super_admin(user_id):
            await export_csv_backups(chat_id)
        elif action == "audit" and is_super_admin(user_id):
            with connect_db() as db:
                logs = db.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 15").fetchall()
            txt = "🛡️ <b>RECENT AUDIT LOGS</b>\n\n" + ("\n".join(f"• {l['action']} by <code>{l['actor_id']}</code>: {html_escape(l['details'])}" for l in logs) or "No logs yet.")
            await send_message(chat_id, txt, admin_keyboard(user_id))
        return


# --------------------------------------------------
# ADMIN HELPERS & CSV EXPORT
# --------------------------------------------------

async def show_admin_stats(chat_id, user_id):
    with connect_db() as db:
        users_cnt = db.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        vip_cnt = db.execute("SELECT COUNT(*) AS n FROM subscriptions WHERE status='ACTIVE'").fetchone()["n"]
        paid_cnt = db.execute("SELECT COUNT(*) AS n FROM orders WHERE status='PAID'").fetchone()["n"]
        rev = db.execute("SELECT COALESCE(SUM(amount),0) AS n FROM orders WHERE status='PAID'").fetchone()["n"]
    await send_message(
        chat_id,
        f"📊 <b>{PROJECT_NAME} OVERVIEW</b>\n"
        f"🗄 DB: {'PostgreSQL ✅' if USE_POSTGRES else 'SQLite'}\n"
        f"👤 Users: <b>{users_cnt}</b> | 👑 Active VIPs: <b>{vip_cnt}</b>\n"
        f"✅ Paid Orders: <b>{paid_cnt}</b> | 💰 Revenue: <b>KES {rev:,}</b>",
        admin_keyboard(user_id),
    )


async def show_admin_matches(chat_id, user_id):
    with connect_db() as db:
        qf = db.execute("SELECT COUNT(*) AS n FROM matches WHERE tier='FREE' AND publish_status='QUEUED'").fetchone()["n"]
        qv = db.execute("SELECT COUNT(*) AS n FROM matches WHERE tier='VIP' AND publish_status='QUEUED'").fetchone()["n"]
        recent = db.execute("SELECT * FROM matches ORDER BY id DESC LIMIT 6").fetchall()
    rows = [
        [
            {"text": f"📢 Post FREE ({qf})", "callback_data": "admin:pub_free", "style": "success"},
            {"text": f"💎 Post VIP ({qv})", "callback_data": "admin:pub_vip", "style": "danger"},
        ]
    ]
    txt = "⚽ <b>FIXTURES & MATCHES</b>\nAdd: <code>/addmatch FREE | Team A vs Team B | 1X2 | Home Win | 1.85 | 2026-11-15 | 19:45 UTC</code>\n\n"
    for m in recent:
        txt += f"• #{m['id']} [{m['tier']}] {html_escape(m['match_name'])} — {m['result']}\n"
        rows.append([
            {"text": f"#{m['id']} ✅Won", "callback_data": f"admin:res:{m['id']}:WON", "style": "success"},
            {"text": "❌Lost", "callback_data": f"admin:res:{m['id']}:LOST", "style": "danger"},
            {"text": "⚪Void", "callback_data": f"admin:res:{m['id']}:VOID", "style": "primary"},
        ])
    rows.append([{"text": "⬅️ Admin", "callback_data": "admin:home"}])
    await send_message(chat_id, txt, {"inline_keyboard": rows})


async def show_admin_transactions(chat_id, user_id):
    with connect_db() as db:
        orders = db.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT 10").fetchall()
    txt = "🧾 <b>TRANSACTIONS</b>\n\n" + ("\n".join(f"• <code>{o['external_ref']}</code> {o['plan']} {o['local_amount']} [{o['status']}]" for o in orders) or "No transactions yet.")
    await send_message(chat_id, txt, admin_keyboard(user_id))


async def show_admin_users(chat_id, user_id):
    with connect_db() as db:
        users = db.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT 10").fetchall()
    txt = "👥 <b>RECENT USERS</b>\n\n" + "\n".join(f"• <code>{u['user_id']}</code> @{u['username'] or u['first_name']} [{u['country'] or 'Unconfirmed'}/{u['currency']}]" for u in users)
    await send_message(chat_id, txt, admin_keyboard(user_id))


async def show_admin_packages(chat_id):
    with connect_db() as db:
        plans = db.execute("SELECT * FROM plans ORDER BY price").fetchall()
    rows = [[{"text": f"✏️ {p['title']} (KES {p['price']})", "callback_data": f"admin:editprice:{p['name']}", "style": "primary"}] for p in plans]
    rows.append([{"text": "⬅️ Admin", "callback_data": "admin:home"}])
    await send_message(chat_id, "💰 <b>MANAGE VIP PLANS</b>", {"inline_keyboard": rows})


async def export_csv_backups(chat_id):
    with connect_db() as db:
        orders = db.execute("SELECT * FROM orders ORDER BY created_at DESC").fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["external_ref", "user_id", "plan", "amount_kes", "currency", "local_amount", "provider", "status", "receipt", "created_at"])
    for o in orders:
        w.writerow([o["external_ref"], o["user_id"], o["plan"], o["amount"], o["currency"], o["local_amount"], o["provider"], o["status"], o["mpesa_receipt"], o["created_at"]])
    await telegram_send_document(chat_id, "prime_tips_empire_orders.csv", buf.getvalue().encode("utf-8"), "📦 Orders CSV Backup")


# --------------------------------------------------
# BACKGROUND WORKER & FASTAPI WEBHOOKS / PORTAL
# --------------------------------------------------

async def background_worker():
    global bot_username
    for attempt in range(5):
        try:
            me = await telegram("getMe", {})
            bot_username = me["username"]
            await telegram("setWebhook", {
                "url": PUBLIC_URL.rstrip("/") + "/telegram",
                "secret_token": WEBHOOK_SECRET,
                "allowed_updates": ["message", "callback_query"],
            })
            log.info("%s started as @%s", PROJECT_NAME, bot_username)
            break
        except Exception:
            log.exception("Webhook setup failed (%s/5)", attempt + 1)
            await asyncio.sleep(5)

    while True:
        try:
            await refresh_exchange_rates(force=False)
            now_iso = utcnow().isoformat()
            remind_iso = (utcnow() + timedelta(hours=12)).isoformat()
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
    http_client = httpx.AsyncClient(timeout=35)
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


app = FastAPI(lifespan=lifespan)
cors_origins = [o.strip() for o in CORS_ORIGINS_RAW.split(",") if o.strip()]
if PUBLIC_URL and PUBLIC_URL.rstrip("/") not in cors_origins:
    cors_origins.append(PUBLIC_URL.rstrip("/"))
app.add_middleware(CORSMiddleware, allow_origins=cors_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.get("/")
async def home():
    return {"status": f"{PROJECT_NAME} running", "brand": BRAND_TAG, "storage": "postgresql" if USE_POSTGRES else "sqlite"}


@app.get("/health")
async def health():
    return {"status": "healthy", "storage": "postgresql" if USE_POSTGRES else "sqlite"}


@app.get("/api/packages")
async def api_packages(currency: str = Query(default="KES")):
    curr = currency.upper() if currency.upper() in CURRENCIES else "KES"
    with connect_db() as db:
        plans = db.execute("SELECT name,title,price,days,benefits,is_lifetime FROM plans ORDER BY price").fetchall()
    out = []
    for p in plans:
        d = dict(p)
        d["display_price"], d["converted"] = format_converted_price(p["price"], curr)
        out.append(d)
    return {"currency": curr, "packages": out}


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
    <select id="currSel" onchange="loadPortal(this.value)">
      {''.join(f'<option value="{c}">{c}</option>' for c in CURRENCIES)}
    </select>
  </div>
  <div id="portalBody" class="card">Loading predictions & currency prices...</div>
  <a class="btn" href="{bot_link}">⚡ Join VIP in Telegram</a>
<script>
const esc=s=>String(s??'').replace(/[&<>"']/g,m=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[m]));
function loadPortal(curr){{
  Promise.all([fetch('/api/predictions').then(r=>r.json()),fetch('/api/packages?currency='+curr).then(r=>r.json())])
  .then(([p,pkg])=>{{
    let h=`<div><b>Verified Win Rate:</b> ${{esc(p.win_rate ?? 'Tracking')}}%</div><h3>⚽ Today's Free Predictions</h3>`;
    if(!p.free_predictions.length) h+=`<p style="color:#94a3b8">No free picks published yet.</p>`;
    p.free_predictions.forEach(m=>{{h+=`<div class="card"><b>${{esc(m.match_name)}}</b><br>🎯 ${{esc(m.market)}}: ${{esc(m.tip)}} | 📈 ${{esc(m.odds)}} | ${{esc(m.result)}}</div>`;}});
    h+=`<h3 style="margin-top:16px">👑 VIP Plans (${{esc(pkg.currency)}})</h3>`;
    pkg.packages.forEach(x=>{{h+=`<div class="card"><b>${{esc(x.title)}}</b> — <span style="color:#f59e0b">${{esc(x.display_price)}}</span><br><small style="color:#94a3b8">${{esc(x.benefits)}}</small></div>`;}});
    document.getElementById('portalBody').innerHTML=h;
  }});
}}
loadPortal('KES');
</script>
</body>
</html>"""


@app.post("/telegram")
async def telegram_webhook(request: Request):
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not secrets.compare_digest(secret, WEBHOOK_SECRET):
        raise HTTPException(status_code=403, detail="Unauthorized")
    update = await request.json()
    try:
        if "message" in update:
            await handle_message(update["message"])
        elif "callback_query" in update:
            await handle_callback(update["callback_query"])
    except Exception:
        log.exception("Telegram update processing failed")
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "10000")))

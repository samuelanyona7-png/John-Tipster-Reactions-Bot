import os
import io
import re
import csv
import hmac
import hashlib
import asyncio
import secrets
import sqlite3
import logging
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
# PRIME TIPS VIP PLATFORM (@isla_olivia0)
# Telegram Bot + PayHero M-Pesa + Multi-Country + Website API
# --------------------------------------------------

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("prime-tips-vip")

PROJECT_NAME = "Prime Tips VIP"
BRAND_TAG = "@isla_olivia0"
DEFAULT_WEBSITE_URL = "https://primetips.site"


def env_str(name, default=""):
    return (os.getenv(name) or default).strip()


BOT_TOKEN = env_str("TELEGRAM_BOT_TOKEN")
ADMIN_USER_ID = env_str("ADMIN_USER_ID")
CHANNEL_ID = env_str("CHANNEL_ID")
VIP_CHANNEL_ID = env_str("VIP_CHANNEL_ID")
WEBHOOK_SECRET = env_str("WEBHOOK_SECRET")

PAYHERO_AUTH_TOKEN = env_str("PAYHERO_AUTH_TOKEN")
PAYHERO_CHANNEL_ID = env_str("PAYHERO_CHANNEL_ID")

PUBLIC_URL = env_str("RENDER_EXTERNAL_URL") or env_str("PUBLIC_URL")
DATABASE_URL = env_str("DATABASE_URL")
DB_PATH = env_str("DB_PATH", "john_tipster.db")
WEBSITE_API_KEY = env_str("WEBSITE_API_KEY")
CORS_ORIGINS_RAW = env_str(
    "CORS_ORIGINS",
    "https://primetips.site,https://www.primetips.site",
)

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://"):]

USE_POSTGRES = bool(DATABASE_URL)
PAYHERO_URL = "https://backend.payhero.co.ke/api/v2"
EAT = timezone(timedelta(hours=3))
STK_COOLDOWN_SECONDS = 45

bot_username = None
http_client: httpx.AsyncClient | None = None
seen_media_groups = []

# --------------------------------------------------
# MULTI-COUNTRY CURRENCY & PAYMENT CATALOG
# Base package prices in DB are stored in KES.
# --------------------------------------------------

COUNTRIES = {
    "KE": {
        "flag": "🇰🇪",
        "name": "Kenya",
        "currency": "KES",
        "symbol": "KES",
        "rate_from_kes": 1.0,
        "round_to": 1,
        "methods": ["mpesa", "airtel"],
        "how_to_pay": (
            "1️⃣ Tap <b>🇰🇪 M-Pesa STK Push (Instant)</b>.\n"
            "2️⃣ Enter your Safaricom number (<code>07...</code> or <code>01...</code>).\n"
            "3️⃣ Enter your M-Pesa PIN on your phone.\n"
            "4️⃣ VIP activates automatically in seconds!"
        ),
    },
    "UG": {
        "flag": "🇺🇬",
        "name": "Uganda",
        "currency": "UGX",
        "symbol": "UGX",
        "rate_from_kes": 28.5,
        "round_to": 100,
        "methods": ["mtn", "airtel"],
        "how_to_pay": (
            "1️⃣ Select <b>MTN MoMo</b> or <b>Airtel Money</b>.\n"
            "2️⃣ Request our Uganda MoMo/Airtel details via <b>@isla_olivia0</b> if not shown.\n"
            "3️⃣ Send the exact UGX amount.\n"
            "4️⃣ Reply with <code>YourPhone | TransactionID</code> for fast activation."
        ),
    },
    "TZ": {
        "flag": "🇹🇿",
        "name": "Tanzania",
        "currency": "TZS",
        "symbol": "TZS",
        "rate_from_kes": 20.0,
        "round_to": 100,
        "methods": ["mpesa_tz", "airtel", "tigo"],
        "how_to_pay": (
            "1️⃣ Select <b>Vodacom M-Pesa TZ</b>, <b>Airtel Money</b>, or <b>Mixx/Tigo Pesa</b>.\n"
            "2️⃣ Contact <b>@isla_olivia0</b> for the Tanzania payment number.\n"
            "3️⃣ Send the exact TZS amount.\n"
            "4️⃣ Reply with <code>YourPhone | TransactionID</code> to activate VIP."
        ),
    },
    "RW": {
        "flag": "🇷🇼",
        "name": "Rwanda",
        "currency": "RWF",
        "symbol": "RWF",
        "rate_from_kes": 10.5,
        "round_to": 50,
        "methods": ["mtn", "airtel"],
        "how_to_pay": (
            "1️⃣ Select <b>MTN MoMo Rwanda</b> or <b>Airtel Money</b>.\n"
            "2️⃣ Contact <b>@isla_olivia0</b> for the Rwanda MoMo number.\n"
            "3️⃣ Send the exact RWF amount and submit <code>YourPhone | TransactionID</code>."
        ),
    },
    "NG": {
        "flag": "🇳🇬",
        "name": "Nigeria",
        "currency": "NGN",
        "symbol": "₦",
        "rate_from_kes": 12.5,
        "round_to": 50,
        "methods": ["ng_bank"],
        "how_to_pay": (
            "1️⃣ Select <b>Nigeria Bank / OPay / PalmPay</b>.\n"
            "2️⃣ Contact <b>@isla_olivia0</b> for the Naira transfer account.\n"
            "3️⃣ Transfer the NGN amount and submit <code>SenderName/Phone | Reference</code>."
        ),
    },
    "GH": {
        "flag": "🇬🇭",
        "name": "Ghana",
        "currency": "GHS",
        "symbol": "GHS",
        "rate_from_kes": 0.12,
        "round_to": 0,
        "methods": ["mtn", "airtel"],
        "how_to_pay": (
            "1️⃣ Select <b>MTN MoMo Ghana</b> or <b>AirtelTigo / Telecel</b>.\n"
            "2️⃣ Contact <b>@isla_olivia0</b> for the Ghana MoMo number.\n"
            "3️⃣ Send the GHS amount and submit <code>YourPhone | TransactionID</code>."
        ),
    },
    "ZA": {
        "flag": "🇿🇦",
        "name": "South Africa",
        "currency": "ZAR",
        "symbol": "R",
        "rate_from_kes": 0.14,
        "round_to": 0,
        "methods": ["za_eft"],
        "how_to_pay": (
            "1️⃣ Select <b>South Africa EFT / Capitec</b>.\n"
            "2️⃣ Contact <b>@isla_olivia0</b> for EFT banking details.\n"
            "3️⃣ Send the ZAR amount and submit <code>Phone/Name | Reference</code>."
        ),
    },
    "INTL": {
        "flag": "🌍",
        "name": "International",
        "currency": "USD",
        "symbol": "$",
        "rate_from_kes": 0.0078,
        "round_to": 0,
        "methods": ["intl_crypto"],
        "how_to_pay": (
            "1️⃣ Select <b>USDT / PayPal / International Transfer</b>.\n"
            "2️⃣ Contact <b>@isla_olivia0</b> for our USDT (TRC20/BEP20) or PayPal address.\n"
            "3️⃣ Send the USD amount and submit <code>Email/Wallet | TxHash</code>."
        ),
    },
}

METHOD_LABELS = {
    "mpesa": ("🇰🇪 M-Pesa STK Push (Instant)", "success"),
    "airtel": ("🔴 Airtel Money", "danger"),
    "mtn": ("🟡 MTN Mobile Money", "primary"),
    "mpesa_tz": ("🇹🇿 Vodacom M-Pesa TZ", "danger"),
    "tigo": ("🔵 Mixx / Tigo Pesa", "primary"),
    "ng_bank": ("🇳🇬 Bank / OPay / PalmPay", "success"),
    "za_eft": ("🇿🇦 Instant EFT / Capitec", "primary"),
    "intl_crypto": ("💲 USDT / PayPal / Card Transfer", "success"),
}


def get_country_info(code):
    return COUNTRIES.get((code or "KE").upper(), COUNTRIES["KE"])


def get_country_rate(code):
    code = (code or "KE").upper()
    c = get_country_info(code)
    if code == "KE":
        return 1.0
    raw = get_setting(f"rate_{code}", "")
    if raw:
        try:
            val = float(raw)
            if val > 0:
                return val
        except ValueError:
            pass
    return float(c["rate_from_kes"])


def convert_price(kes_amount, country_code):
    c = get_country_info(country_code)
    rate = get_country_rate(country_code)
    raw = float(kes_amount) * rate
    r = c["round_to"]
    if c["currency"] == "KES":
        val = int(round(raw))
        return val, f"KES {val:,}"
    if r >= 1:
        val = max(r, int(round(raw / r) * r))
        display = f"{c['symbol']} {val:,}" if c["symbol"] == c["currency"] else f"{c['symbol']}{val:,} ({c['currency']})"
        return val, display
    val = max(1.0, round(raw, 2))
    return val, f"{c['symbol']}{val:,.2f} {c['currency']}"


def detect_country_from_phone(phone):
    cleaned = re.sub(r"[\s\-()]", "", phone or "")
    prefixes = [
        ("+254", "KE"), ("254", "KE"),
        ("+256", "UG"), ("256", "UG"),
        ("+255", "TZ"), ("255", "TZ"),
        ("+250", "RW"), ("250", "RW"),
        ("+234", "NG"), ("234", "NG"),
        ("+233", "GH"), ("233", "GH"),
        ("+27", "ZA"),
        ("+44", "INTL"), ("+1", "INTL"),
    ]
    for pref, code in prefixes:
        if cleaned.startswith(pref):
            return code
    return None


def detect_country_from_telegram(user_obj):
    lang = (user_obj.get("language_code") or "").lower()
    if lang.startswith("sw"):
        return "KE"
    if lang.startswith("rw"):
        return "RW"
    if lang in ("en-gb", "en-us", "fr", "es", "de", "ar", "ru", "zh"):
        return "INTL"
    return "KE"


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
        raise RuntimeError("ADMIN_USER_ID must be a numeric Telegram user ID")
    if not PAYHERO_CHANNEL_ID.isdigit():
        raise RuntimeError("PAYHERO_CHANNEL_ID must be numeric")
    if USE_POSTGRES and psycopg2 is None:
        raise RuntimeError("DATABASE_URL is set; add psycopg2-binary to requirements.txt")


def utcnow():
    return datetime.now(timezone.utc)


def parse_utc(dt_or_iso):
    if isinstance(dt_or_iso, str):
        dt = datetime.fromisoformat(dt_or_iso)
    else:
        dt = dt_or_iso
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def format_eat(dt_or_iso):
    return parse_utc(dt_or_iso).astimezone(EAT).strftime("%d %b %Y, %I:%M %p EAT")


def storage_label():
    if USE_POSTGRES:
        return "PostgreSQL ✅ (Real-Time Persistent)"
    return "SQLite ⚠️ (Local file — set DATABASE_URL for permanent storage)"


async def get_http():
    global http_client
    if http_client is None or http_client.is_closed:
        http_client = httpx.AsyncClient(timeout=35)
    return http_client


# --------------------------------------------------
# REAL-TIME DUAL DATABASE LAYER (PostgreSQL + SQLite)
# --------------------------------------------------

class DBConn:
    def __init__(self, raw_con):
        self.raw = raw_con

    def _adapt(self, sql):
        return sql.replace("?", "%s") if USE_POSTGRES else sql

    def execute(self, sql, params=()):
        adapted = self._adapt(sql)
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
                country TEXT NOT NULL DEFAULT 'KE',
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
            db.execute("""
                CREATE TABLE sessions (
                    user_id BIGINT PRIMARY KEY,
                    state TEXT NOT NULL,
                    payload TEXT NOT NULL DEFAULT '{}'
                )
            """)

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

        ensure_column(db, "users", "country", "TEXT NOT NULL DEFAULT 'KE'")
        ensure_column(db, "plans", "title", "TEXT NOT NULL DEFAULT ''")
        ensure_column(db, "plans", "benefits", "TEXT NOT NULL DEFAULT ''")
        ensure_column(db, "plans", "is_lifetime", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(db, "orders", "provider", "TEXT NOT NULL DEFAULT 'mpesa'")
        ensure_column(db, "orders", "mpesa_receipt", "TEXT")
        ensure_column(db, "orders", "currency", "TEXT NOT NULL DEFAULT 'KES'")
        ensure_column(db, "orders", "local_amount", "TEXT NOT NULL DEFAULT ''")
        ensure_column(db, "subscriptions", "is_lifetime", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(db, "subscriptions", "reminded", "INTEGER NOT NULL DEFAULT 0")

        defaults_plans = [
            ("daily", "Daily VIP", 100, 1, "24 Hours full VIP access • High-confidence daily picks", 0),
            ("weekly", "Weekly VIP", 300, 7, "7 Days VIP access • Daily singles & multi-bets • Priority alerts", 0),
            ("monthly", "Monthly VIP", 1000, 30, "30 Days VIP access • Full ticket bank • Best monthly value", 0),
            ("premium", "Premium VIP", 2500, 90, "90 Days VIP access • All accumulator slips • Priority support", 0),
            ("lifetime", "Lifetime VIP", 10000, 36500, "Permanent VIP access • Never expires • Every future VIP pick", 1),
        ]
        for name, title, price, days, benefits, is_lt in defaults_plans:
            db.execute(
                """
                INSERT INTO plans(name,title,price,days,benefits,is_lifetime)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(name) DO NOTHING
                """,
                (name, title, price, days, benefits, is_lt),
            )
            db.execute(
                "UPDATE plans SET title=?, benefits=? WHERE name=? AND (title IS NULL OR title='')",
                (title, benefits, name),
            )

        default_settings = {
            "main_channel_id": CHANNEL_ID,
            "vip_channel_id": VIP_CHANNEL_ID,
            "payhero_channel_id": PAYHERO_CHANNEL_ID,
            "referral_reward_days": "2",
            "vip_odds_info": "🔥 10+ Verified VIP Odds Daily (Singles & Accumulator Tickets)",
            "admin_contact_url": f"https://t.me/{BRAND_TAG.lstrip('@')}",
            "website_url": DEFAULT_WEBSITE_URL,
            "auto_channel_buttons": "0",
        }
        for k, v in default_settings.items():
            db.execute(
                "INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO NOTHING",
                (k, v),
            )


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


def is_admin(user_id):
    return str(user_id) == str(ADMIN_USER_ID)


def get_user(user_id):
    with connect_db() as db:
        return db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def get_user_country(user_id):
    u = get_user(user_id)
    if u and u["country"] in COUNTRIES:
        return u["country"]
    return "KE"


def set_user_country(user_id, country_code):
    code = country_code.upper()
    if code not in COUNTRIES:
        code = "KE"
    with connect_db() as db:
        db.execute("UPDATE users SET country=? WHERE user_id=?", (code, user_id))
    return code


def register_user(user, referred_by=None):
    user_id = user.get("id")
    if not user_id:
        return
    username = user.get("username") or ""
    first_name = user.get("first_name") or "Member"

    if referred_by == user_id:
        referred_by = None
    if referred_by and not get_user(referred_by):
        referred_by = None

    guessed_country = detect_country_from_telegram(user)

    with connect_db() as db:
        existing = db.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,)).fetchone()
        if existing:
            db.execute(
                "UPDATE users SET username=?, first_name=? WHERE user_id=?",
                (username, first_name, user_id),
            )
        else:
            db.execute(
                """
                INSERT INTO users(user_id,username,first_name,country,referred_by,created_at)
                VALUES(?,?,?,?,?,?)
                """,
                (user_id, username, first_name, guessed_country, referred_by, utcnow().isoformat()),
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
# TELEGRAM API (with 4096-char safe splitting & files)
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
    chunks = []
    remaining = text
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
# COLORFUL KEYBOARDS & LUXURY UI
# --------------------------------------------------

def main_menu_keyboard(user_id=None):
    website_url = get_website_url()
    country = get_country_info(get_user_country(user_id) if user_id else "KE")
    rows = [
        [
            {"text": "👑 JOIN VIP NOW", "callback_data": "menu:join_vip", "style": "danger"},
            {"text": "🌐 Open VIP App", "web_app": {"url": PUBLIC_URL.rstrip("/") + "/app"}, "style": "primary"},
        ],
        [
            {"text": "⚽ Today's Free Predictions", "callback_data": "menu:free_tips", "style": "success"},
            {"text": "💎 VIP Predictions", "callback_data": "menu:vip_tips", "style": "primary"},
        ],
        [
            {"text": "📊 Previous Results", "callback_data": "menu:results", "style": "primary"},
            {"text": "🏆 Winning Tickets", "callback_data": "menu:tickets", "style": "success"},
        ],
        [
            {"text": "📅 Prediction History", "callback_data": "menu:history", "style": "primary"},
            {"text": "💳 Payment & Subscription", "callback_data": "menu:payments", "style": "success"},
        ],
        [
            {"text": "👤 My Account", "callback_data": "menu:account", "style": "primary"},
            {"text": f"{country['flag']} Country ({country['currency']})", "callback_data": "menu:country", "style": "success"},
        ],
        [
            {"text": "📞 Contact Admin", "callback_data": "menu:contact", "style": "danger"},
            {"text": "ℹ️ How It Works", "callback_data": "menu:how", "style": "primary"},
        ],
        [
            {"text": "🌐 Visit Website • primetips.site", "url": website_url, "style": "success"},
        ],
    ]
    if user_id and is_admin(user_id):
        rows.append([{"text": "⚡ ADMIN DASHBOARD", "callback_data": "admin:home", "style": "danger"}])
    return {"inline_keyboard": rows}


def country_keyboard(back_cb="menu:home"):
    items = list(COUNTRIES.items())
    rows = []
    for i in range(0, len(items), 2):
        pair = []
        for code, info in items[i:i + 2]:
            pair.append({
                "text": f"{info['flag']} {info['name']} ({info['currency']})",
                "callback_data": f"setcountry:{code}",
                "style": "success" if code == "KE" else "primary",
            })
        rows.append(pair)
    rows.append([{"text": "⬅️ Back", "callback_data": back_cb}])
    return {"inline_keyboard": rows}


def plans_keyboard(country_code="KE"):
    with connect_db() as db:
        plans = db.execute("SELECT * FROM plans ORDER BY price").fetchall()

    c = get_country_info(country_code)
    style_map = {
        "daily": "success",
        "weekly": "primary",
        "monthly": "primary",
        "premium": "danger",
        "lifetime": "danger",
    }

    rows = []
    for p in plans:
        duration = "Lifetime" if p["is_lifetime"] else f"{p['days']}d"
        title = p["title"] or f"{p['name'].title()} VIP"
        _, local_str = convert_price(p["price"], country_code)
        rows.append([{
            "text": f"👑 {title} ({duration}) — {local_str}",
            "callback_data": f"plan:{p['name']}",
            "style": style_map.get(p["name"], "primary"),
        }])
    rows.append([
        {"text": f"{c['flag']} Change Country ({c['currency']})", "callback_data": "menu:country", "style": "primary"},
        {"text": "📞 Contact Admin", "url": get_setting("admin_contact_url", f"https://t.me/{BRAND_TAG.lstrip('@')}"), "style": "success"},
    ])
    rows.append([{"text": "⬅️ Back to Main Menu", "callback_data": "menu:home"}])
    return {"inline_keyboard": rows}


def channel_post_keyboard(include_custom=False):
    contact_url = get_setting("admin_contact_url", f"https://t.me/{BRAND_TAG.lstrip('@')}")
    join_url = f"https://t.me/{bot_username}?start=vip" if bot_username else contact_url
    website_url = get_website_url()

    rows = [[
        {"text": "👑 JOIN VIP NOW", "url": join_url, "style": "danger"},
        {"text": "📞 Contact Admin", "url": contact_url, "style": "success"},
    ]]
    if website_url:
        short_domain = re.sub(r"^https?://", "", website_url).rstrip("/")
        rows.append([{"text": f"🌐 Visit Website • {short_domain}", "url": website_url, "style": "primary"}])

    if include_custom:
        with connect_db() as db:
            buttons = db.execute("SELECT * FROM custom_buttons ORDER BY id").fetchall()
        for b in buttons:
            rows.append([{"text": b["label"], "url": b["url"], "style": "primary"}])
    return {"inline_keyboard": rows}


def admin_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "📊 Live Analytics", "callback_data": "admin:stats", "style": "primary"},
                {"text": "⚽ Match Studio", "callback_data": "admin:matches", "style": "success"},
            ],
            [
                {"text": "💰 VIP Packages", "callback_data": "admin:packages", "style": "success"},
                {"text": "🎯 Set VIP Odds Info", "callback_data": "admin:vip_odds", "style": "primary"},
            ],
            [
                {"text": "🧾 Transactions", "callback_data": "admin:transactions", "style": "primary"},
                {"text": "👥 VIP & Users", "callback_data": "admin:users", "style": "success"},
            ],
            [
                {"text": "📝 Post to Main Channel", "callback_data": "admin:post_channel", "style": "success"},
                {"text": "📎 Add Buttons to Post", "callback_data": "admin:attach_buttons", "style": "danger"},
            ],
            [
                {"text": "📢 Publish VIP Advert", "callback_data": "admin:post", "style": "primary"},
                {"text": "📣 Broadcast", "callback_data": "admin:broadcast", "style": "danger"},
            ],
            [
                {"text": "⚙️ Channels & Settings", "callback_data": "admin:channels", "style": "primary"},
                {"text": "📥 Export CSV", "callback_data": "admin:export", "style": "success"},
            ],
            [
                {"text": "🏠 Main Menu", "callback_data": "menu:home"},
            ],
        ]
    }


# --------------------------------------------------
# PAYMENT HELPERS (PayHero M-Pesa + Multi-Country Flow)
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
    if not re.fullmatch(r"0[17]\d{8}", phone):
        return None
    return phone


def get_plan(plan_name):
    with connect_db() as db:
        return db.execute("SELECT * FROM plans WHERE name=?", (plan_name,)).fetchone()


def mark_order_failed(external_ref):
    with connect_db() as db:
        db.execute(
            "UPDATE orders SET status='FAILED' WHERE external_ref=? AND status='PENDING'",
            (external_ref,),
        )


def recent_stk_cooldown_remaining(user_id):
    with connect_db() as db:
        last = db.execute(
            "SELECT created_at FROM orders WHERE user_id=? AND provider='mpesa' ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    if not last:
        return 0
    elapsed = (utcnow() - parse_utc(last["created_at"])).total_seconds()
    return max(0, int(STK_COOLDOWN_SECONDS - elapsed))


async def start_mpesa_payment(user_id, plan_name, phone, notify_telegram=True):
    plan = get_plan(plan_name)
    if not plan:
        if notify_telegram:
            await send_message(user_id, "⚠️ That VIP package is unavailable.")
        return {"ok": False, "error": "Package unavailable"}

    wait_sec = recent_stk_cooldown_remaining(user_id)
    if wait_sec > 0:
        msg = f"Please wait {wait_sec}s before requesting another M-Pesa STK prompt."
        if notify_telegram:
            await send_message(user_id, f"⏳ {msg}")
        return {"ok": False, "error": msg}

    external_ref = "PT" + secrets.token_hex(8).upper()
    amount = int(plan["price"])
    ph_channel = int(get_setting("payhero_channel_id", PAYHERO_CHANNEL_ID))
    plan_title = plan["title"] or f"{plan_name.title()} VIP"

    with connect_db() as db:
        db.execute(
            """
            INSERT INTO orders(external_ref,user_id,plan,amount,currency,local_amount,phone,provider,status,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (external_ref, user_id, plan_name, amount, "KES", f"KES {amount}", phone, "mpesa", "PENDING", utcnow().isoformat()),
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
        response = await client.post(
            PAYHERO_URL + "/payments",
            headers=payhero_headers(),
            json=payload,
        )
        result = response.json()

        if response.status_code not in (200, 201) or not result.get("success"):
            log.error("PayHero payment error (%s): %s", response.status_code, result)
            mark_order_failed(external_ref)
            if notify_telegram:
                await send_message(
                    user_id,
                    "❌ <b>M-Pesa request could not be started.</b>\n\n"
                    f"Please double-check your number or contact admin {BRAND_TAG}",
                )
            return {"ok": False, "error": "PayHero rejected payment initiation"}

        reference = result.get("reference")
        checkout_id = result.get("CheckoutRequestID")
        if not reference:
            mark_order_failed(external_ref)
            raise RuntimeError("Missing PayHero reference")

        with connect_db() as db:
            db.execute(
                "UPDATE orders SET payhero_reference=?, checkout_id=? WHERE external_ref=?",
                (reference, checkout_id, external_ref),
            )

        if notify_telegram:
            await send_message(
                user_id,
                "📲 <b>M-PESA STK PUSH SENT!</b>\n\n"
                f"👑 Package: <b>{html_escape(plan_title)}</b>\n"
                f"💰 Amount: <b>KES {amount}</b>\n"
                f"📞 Phone: <code>{html_escape(phone)}</code>\n\n"
                "🔐 Enter your <b>M-Pesa PIN</b> on your phone.\n"
                "⚡ VIP activates automatically—or tap <b>Verify Payment</b> once you receive the SMS.",
                {
                    "inline_keyboard": [
                        [{"text": "🔄 I Have Paid — Verify Payment", "callback_data": f"verify:{external_ref}", "style": "success"}],
                        [{"text": "🏠 Main Menu", "callback_data": "menu:home", "style": "primary"}],
                    ]
                },
            )
        return {"ok": True, "external_ref": external_ref, "status": "PENDING"}
    except Exception:
        log.exception("Payment initiation failed")
        mark_order_failed(external_ref)
        if notify_telegram:
            await send_message(
                user_id,
                f"⚠️ Could not start your payment right now. Please try again or contact {BRAND_TAG}",
            )
        return {"ok": False, "error": "Payment initiation failed"}


async def create_manual_order(user_id, plan_name, provider, contact_str, txn_ref, notify_telegram=True):
    plan = get_plan(plan_name)
    if not plan:
        return {"ok": False, "error": "VIP package not found"}

    detected = detect_country_from_phone(contact_str)
    if detected:
        set_user_country(user_id, detected)
    country_code = get_user_country(user_id)
    country = get_country_info(country_code)
    _, local_str = convert_price(plan["price"], country_code)

    external_ref = "MM" + secrets.token_hex(6).upper()
    amount = int(plan["price"])
    plan_title = plan["title"] or f"{plan_name.title()} VIP"

    with connect_db() as db:
        db.execute(
            """
            INSERT INTO orders(external_ref,user_id,plan,amount,currency,local_amount,phone,provider,status,mpesa_receipt,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (external_ref, user_id, plan_name, amount, country["currency"], local_str, contact_str[:40], provider, "PENDING_REVIEW", txn_ref[:60], utcnow().isoformat()),
        )

    if notify_telegram:
        await send_message(
            user_id,
            "⏳ <b>PAYMENT SUBMITTED FOR VERIFICATION</b>\n\n"
            f"{country['flag']} Country: <b>{country['name']}</b>\n"
            f"🏦 Method: <b>{html_escape(provider.upper())}</b>\n"
            f"👑 Package: <b>{html_escape(plan_title)}</b> ({html_escape(local_str)})\n"
            f"🧾 Reference: <code>{html_escape(txn_ref)}</code>\n\n"
            "🔒 Our admin will verify this transaction ID directly and activate your VIP immediately.",
            {"inline_keyboard": [[{"text": "🏠 Main Menu", "callback_data": "menu:home", "style": "primary"}]]},
        )

    await send_message(
        ADMIN_USER_ID,
        f"🔔 <b>NEW {html_escape(provider.upper())} PAYMENT REVIEW</b>\n\n"
        f"{country['flag']} Country: <b>{country['name']}</b>\n"
        f"🆔 User: <code>{user_id}</code>\n"
        f"👑 Plan: <b>{html_escape(plan_title)}</b> — {html_escape(local_str)} (Base KES {amount})\n"
        f"📞 Contact: <code>{html_escape(contact_str)}</code>\n"
        f"🧾 Txn Ref: <code>{html_escape(txn_ref)}</code>\n"
        f"🔗 Order: <code>{external_ref}</code>",
        {
            "inline_keyboard": [[
                {"text": "✅ Approve & Activate VIP", "callback_data": f"admin:approve:{external_ref}", "style": "success"},
                {"text": "❌ Reject", "callback_data": f"admin:reject:{external_ref}", "style": "danger"},
            ]]
        },
    )
    return {"ok": True, "external_ref": external_ref, "status": "PENDING_REVIEW"}


async def submit_manual_mobile_money(user_id, plan_name, provider, text_input):
    parts = [p.strip() for p in text_input.split("|")]
    if len(parts) != 2 or not parts[0] or not parts[1]:
        await send_message(
            user_id,
            "⚠️ Use the exact format:\n<code>PhoneNumberOrEmail | TransactionID</code>\n"
            "Example: <code>+256771234567 | TXN998811</code>",
        )
        return
    await create_manual_order(user_id, plan_name, provider, parts[0], parts[1], notify_telegram=True)


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
# VIP ACCESS & REFERRAL REWARDS
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
        if existing and existing["status"] == "ACTIVE" and not existing["is_lifetime"]:
            start = max(now, parse_utc(existing["expires_at"]))
        else:
            start = now
        expiry = start + timedelta(days=days)

    with connect_db() as db:
        db.execute(
            """
            INSERT INTO subscriptions(user_id,plan,expires_at,status,is_lifetime,reminded)
            VALUES(?,?,?,'ACTIVE',?,0)
            ON CONFLICT(user_id) DO UPDATE SET
                plan=excluded.plan,
                expires_at=excluded.expires_at,
                status='ACTIVE',
                is_lifetime=excluded.is_lifetime,
                reminded=0
            """,
            (user_id, plan_name, expiry.isoformat(), is_lifetime),
        )

    vip_chat = get_setting("vip_channel_id", VIP_CHANNEL_ID)
    try:
        await telegram("unbanChatMember", {"chat_id": vip_chat, "user_id": user_id, "only_if_banned": True})
    except Exception:
        log.warning("Pre-invite unban skipped for %s", user_id)

    try:
        invite_payload = {"chat_id": vip_chat, "member_limit": 1, "name": f"Prime Tips VIP {user_id}"}
        if not is_lifetime:
            invite_payload["expire_date"] = int(expiry.timestamp())

        invite = await telegram("createChatInviteLink", invite_payload)
        exp_text = "Never (Lifetime)" if is_lifetime else format_eat(expiry)
        receipt_line = f"🧾 Receipt: <code>{html_escape(receipt)}</code>\n" if receipt else ""

        await send_message(
            user_id,
            "🎉 <b>VIP MEMBERSHIP ACTIVATED!</b>\n\n"
            f"👑 <b>{PROJECT_NAME.upper()} ({BRAND_TAG})</b>\n"
            f"💎 Package: <b>{html_escape(display_title)}</b>\n"
            f"{receipt_line}"
            f"📅 Expires: <b>{exp_text}</b>\n\n"
            "👇 Tap your private single-use link to enter the VIP channel:",
            {
                "inline_keyboard": [
                    [{"text": "👑 ENTER VIP CHANNEL", "url": invite["invite_link"], "style": "danger"}],
                    [{"text": "💎 View VIP Predictions", "callback_data": "menu:vip_tips", "style": "success"}],
                ]
            },
        )
    except Exception:
        log.exception("Could not send VIP invite to %s", user_id)
        await send_message(
            ADMIN_USER_ID,
            f"⚠️ VIP activated for <code>{user_id}</code>, but invite creation failed. Check bot admin rights in VIP channel.",
        )


async def reward_referrer_if_eligible(paid_user_id):
    user = get_user(paid_user_id)
    if not user or not user["referred_by"] or user["referral_rewarded"]:
        return

    referrer_id = int(user["referred_by"])
    if referrer_id == int(paid_user_id):
        return

    reward_days = int(get_setting("referral_reward_days", "2"))
    with connect_db() as db:
        cur = db.execute(
            "UPDATE users SET referral_rewarded=1 WHERE user_id=? AND referral_rewarded=0",
            (paid_user_id,),
        )
        if cur.rowcount == 0:
            return

    if reward_days > 0:
        await give_vip_access(referrer_id, f"Referral Bonus ({reward_days}d)", days_override=reward_days)
        try:
            await send_message(
                referrer_id,
                f"🎁 <b>REFERRAL REWARD UNLOCKED!</b>\n\n"
                f"Your invited friend subscribed to {PROJECT_NAME}. You received <b>+{reward_days} free VIP day(s)</b>! {BRAND_TAG}",
            )
        except Exception:
            pass


async def finalize_paid_order(order, verified=None, callback_data=None, manual_admin=False):
    external_ref = order["external_ref"]

    if not manual_admin:
        status = str((verified or {}).get("status", "")).upper()
        if status == "FAILED":
            with connect_db() as db:
                cur = db.execute(
                    "UPDATE orders SET status='FAILED' WHERE external_ref=? AND status='PENDING'",
                    (external_ref,),
                )
                changed = cur.rowcount
            if changed > 0:
                try:
                    uid = int(order["user_id"])
                    await send_message(
                        uid,
                        "❌ <b>M-Pesa payment failed or was cancelled.</b>\n\nTap below to try again.",
                        plans_keyboard(get_user_country(uid)),
                    )
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

    receipt = (
        (callback_data or {}).get("MpesaReceiptNumber")
        or (verified or {}).get("provider_reference")
        or order["mpesa_receipt"]
    )

    with connect_db() as db:
        cur = db.execute(
            """
            UPDATE orders
            SET status='PAID', mpesa_receipt=COALESCE(?, mpesa_receipt)
            WHERE external_ref=? AND status IN ('PENDING', 'PENDING_REVIEW')
            """,
            (receipt, external_ref),
        )
        updated = cur.rowcount

    if updated == 0:
        return "ALREADY_PAID"

    await give_vip_access(int(order["user_id"]), order["plan"], receipt=receipt)
    await reward_referrer_if_eligible(int(order["user_id"]))

    amt_display = order["local_amount"] or ("KES " + str(order["amount"]))
    await send_message(
        ADMIN_USER_ID,
        f"✅ <b>{PROJECT_NAME.upper()} PAYMENT CONFIRMED</b>\n\n"
        f"🆔 User: <code>{order['user_id']}</code>\n"
        f"💼 Plan: <b>{html_escape(order['plan'].title())}</b> — {html_escape(amt_display)}\n"
        f"🏦 Method: <b>{html_escape(order['provider'].upper())}</b>\n"
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
# MATCHES, LIVE RESULT SYNC & CHANNEL POSTING
# --------------------------------------------------

def format_match_block(m, index=None):
    badge_map = {
        "PENDING": "⏳ PENDING",
        "WON": "✅ WON",
        "LOST": "❌ LOST",
        "POSTPONED": "⏸ POSTPONED",
        "CANCELLED": "🚫 CANCELLED",
    }
    res_badge = badge_map.get(m["result"], m["result"])
    prefix = f"<b>#{index}</b> " if index else ""
    return (
        f"{prefix}⚽ <b>{html_escape(m['match_name'])}</b>\n"
        f"🎯 Tip: <b>{html_escape(m['tip'])}</b>  |  📈 Odds: <b>{html_escape(m['odds'])}</b>\n"
        f"🗓 {html_escape(m['match_date'])} ⏰ {html_escape(m['kickoff_time'])}  •  {res_badge}\n"
    )


def render_batch_text(tier, rows):
    vip_odds_info = get_setting("vip_odds_info")
    header = (
        f"💎 <b>{PROJECT_NAME.upper()} — VIP PICKS</b> ({BRAND_TAG})\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        if tier == "VIP"
        else f"⚽ <b>{PROJECT_NAME.upper()} — TODAY'S FREE PICKS</b> ({BRAND_TAG})\n━━━━━━━━━━━━━━━━━━━━\n\n"
    )
    body = "\n".join(format_match_block(m, i + 1) for i, m in enumerate(rows))
    footer = (
        f"\n━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 <b>VIP Status:</b> {html_escape(vip_odds_info)}\n"
        f"🔥 Powered by <b>{PROJECT_NAME} ({BRAND_TAG})</b>"
    )
    return header + body + footer, body


async def sync_channel_message_for_match(match_id):
    with connect_db() as db:
        m = db.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
        if not m or not m["channel_msg_id"]:
            return
        msg_id = m["channel_msg_id"]
        tier = m["tier"]
        rows = db.execute(
            "SELECT * FROM matches WHERE channel_msg_id=? ORDER BY id",
            (msg_id,),
        ).fetchall()

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
            "reply_markup": channel_post_keyboard(include_custom=False),
            "disable_web_page_preview": True,
        })
    except Exception as e:
        log.warning("Could not edit channel message %s: %s", msg_id, e)


def extract_channel_msg_id(message, text):
    fwd_origin = message.get("forward_origin") or {}
    if fwd_origin.get("type") == "channel" and fwd_origin.get("message_id"):
        return int(fwd_origin["message_id"])
    if message.get("forward_from_message_id"):
        return int(message["forward_from_message_id"])
    t = (text or "").strip()
    if t.isdigit():
        return int(t)
    m = re.search(r"t\.me/(?:c/\d+/|[^/]+/)(\d+)", t)
    if m:
        return int(m.group(1))
    return None


async def attach_buttons_to_main_post(msg_id):
    global bot_username
    if not bot_username:
        me = await telegram("getMe", {})
        bot_username = me["username"]
    main_chat = get_setting("main_channel_id", CHANNEL_ID)
    return await telegram("editMessageReplyMarkup", {
        "chat_id": main_chat,
        "message_id": int(msg_id),
        "reply_markup": channel_post_keyboard(include_custom=False),
    })


async def broadcast_new_tips(tier, text_body):
    col = "notify_vip" if tier == "VIP" else "notify_free"
    with connect_db() as db:
        users = db.execute(f"SELECT user_id FROM users WHERE is_blocked=0 AND {col}=1").fetchall()

    for row in users:
        uid = int(row["user_id"])
        if tier == "VIP" and not has_active_vip(uid):
            continue
        try:
            await send_message(uid, f"🔔 <b>NEW {tier} PREDICTIONS POSTED! ({PROJECT_NAME})</b>\n\n{text_body}")
        except Exception:
            pass


async def publish_match_batch(tier, match_ids=None):
    global bot_username
    with connect_db() as db:
        if match_ids:
            placeholders = ",".join(["?"] * len(match_ids))
            rows = db.execute(
                f"SELECT * FROM matches WHERE id IN ({placeholders}) ORDER BY id",
                tuple(match_ids),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM matches WHERE tier=? AND publish_status='QUEUED' ORDER BY id",
                (tier,),
            ).fetchall()

    if not rows:
        return None

    target_chat = get_setting("vip_channel_id", VIP_CHANNEL_ID) if tier == "VIP" else get_setting("main_channel_id", CHANNEL_ID)
    full_text, body = render_batch_text(tier, rows)

    if not bot_username:
        me = await telegram("getMe", {})
        bot_username = me["username"]

    msg = await telegram("sendMessage", {
        "chat_id": target_chat,
        "parse_mode": "HTML",
        "text": full_text[:4090],
        "reply_markup": channel_post_keyboard(include_custom=False),
        "disable_web_page_preview": True,
    })

    ids = [r["id"] for r in rows]
    with connect_db() as db:
        for mid in ids:
            db.execute(
                "UPDATE matches SET publish_status='PUBLISHED', channel_msg_id=? WHERE id=?",
                (msg["message_id"], mid),
            )

    asyncio.create_task(broadcast_new_tips(tier, body))
    return msg


async def publish_vip_post():
    global bot_username
    if not bot_username:
        me = await telegram("getMe", {})
        bot_username = me["username"]

    vip_odds_info = get_setting("vip_odds_info")
    main_chat = get_setting("main_channel_id", CHANNEL_ID)
    msg = await telegram("sendMessage", {
        "chat_id": main_chat,
        "parse_mode": "HTML",
        "text": (
            f"👑 <b>{PROJECT_NAME.upper()}</b> ({BRAND_TAG})\n\n"
            f"🎯 <b>VIP Odds Available:</b>\n{html_escape(vip_odds_info)}\n\n"
            "⚡ Unlock <b>premium football predictions</b>, daily singles & winning slips.\n"
            "💳 Supports <b>M-Pesa, Airtel Money, MTN MoMo & International</b>.\n"
            "🚀 Automatic VIP channel access right after confirmation.\n\n"
            "👇 <b>TAP BELOW TO JOIN VIP, VISIT OUR WEBSITE OR CONTACT ADMIN</b>"
        ),
        "reply_markup": channel_post_keyboard(include_custom=True),
    })
    try:
        await telegram("pinChatMessage", {
            "chat_id": main_chat,
            "message_id": msg["message_id"],
            "disable_notification": True,
        })
    except Exception:
        pass
    return msg


async def publish_custom_main_post(text_body="", photo_file_id=None):
    global bot_username
    if not bot_username:
        me = await telegram("getMe", {})
        bot_username = me["username"]

    main_chat = get_setting("main_channel_id", CHANNEL_ID)
    formatted = f"{html_escape(text_body)}\n\n🔥 <b>{PROJECT_NAME}</b> ({BRAND_TAG})" if text_body else f"🔥 <b>{PROJECT_NAME}</b> ({BRAND_TAG})"

    if photo_file_id:
        return await telegram("sendPhoto", {
            "chat_id": main_chat,
            "photo": photo_file_id,
            "caption": formatted[:1020],
            "parse_mode": "HTML",
            "reply_markup": channel_post_keyboard(include_custom=False),
        })

    return await telegram("sendMessage", {
        "chat_id": main_chat,
        "parse_mode": "HTML",
        "text": formatted[:4090],
        "reply_markup": channel_post_keyboard(include_custom=False),
        "disable_web_page_preview": True,
    })


async def handle_channel_post(post):
    if get_setting("auto_channel_buttons", "0") != "1":
        return
    if post.get("reply_markup"):
        return
    if any(k in post for k in ("pinned_message", "new_chat_title", "new_chat_photo", "delete_chat_photo")):
        return

    chat = post.get("chat", {})
    chat_id = str(chat.get("id", ""))
    chat_username = f"@{chat['username']}" if chat.get("username") else ""
    main_chat = get_setting("main_channel_id", CHANNEL_ID).strip()

    if chat_id != main_chat and (not chat_username or chat_username.lower() != main_chat.lower()):
        return

    mgid = post.get("media_group_id")
    if mgid:
        if mgid in seen_media_groups:
            return
        seen_media_groups.append(mgid)
        if len(seen_media_groups) > 100:
            seen_media_groups.pop(0)

    try:
        await telegram("editMessageReplyMarkup", {
            "chat_id": chat["id"],
            "message_id": post["message_id"],
            "reply_markup": channel_post_keyboard(include_custom=False),
        })
    except Exception as e:
        log.warning("Could not attach buttons to channel post %s: %s", post.get("message_id"), e)


# --------------------------------------------------
# USER VIEWS
# --------------------------------------------------

async def show_main_menu(chat_id, user_id):
    sub_badge = "💎 <b>VIP ACTIVE</b>" if has_active_vip(user_id) else "🆓 <b>FREE MEMBER</b>"
    vip_odds_info = get_setting("vip_odds_info")
    country = get_country_info(get_user_country(user_id))
    await send_message(
        chat_id,
        f"👑 <b>{PROJECT_NAME.upper()}</b>  •  {BRAND_TAG}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Status: {sub_badge}  |  {country['flag']} <b>{country['name']} ({country['currency']})</b>\n"
        f"🎯 <b>VIP Odds:</b> {html_escape(vip_odds_info)}\n"
        f"🌐 <b>Website:</b> {html_escape(get_website_url())}\n\n"
        "⚽ <i>Luxury Football Predictions & Automated VIP Platform</i>\n"
        "Tap <b>👑 JOIN VIP NOW</b> for instant local currency pricing & payment 👇",
        main_menu_keyboard(user_id),
    )


async def show_join_vip_screen(chat_id, user_id):
    country_code = get_user_country(user_id)
    country = get_country_info(country_code)
    vip_odds_info = get_setting("vip_odds_info")
    with connect_db() as db:
        plans = db.execute("SELECT * FROM plans ORDER BY price").fetchall()

    details = "\n".join(
        f"👑 <b>{html_escape(p['title'] or p['name'].title())}</b> — <b>{convert_price(p['price'], country_code)[1]}</b>\n"
        f"<i>   {html_escape(p['benefits'])}</i>"
        for p in plans
    )
    await send_message(
        chat_id,
        f"👑 <b>{PROJECT_NAME.upper()} — VIP MEMBERSHIP</b> ({BRAND_TAG})\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{country['flag']} Country: <b>{country['name']}</b>  •  Currency: <b>{country['currency']}</b>\n"
        f"🎯 <b>What's on VIP:</b>\n{html_escape(vip_odds_info)}\n\n"
        f"💳 <b>How to pay in {country['name']}:</b>\n{country['how_to_pay']}\n\n"
        f"{details}\n\n"
        "👇 <b>Select a package below (or change your country):</b>",
        plans_keyboard(country_code),
    )


async def show_predictions_list(chat_id, user_id, tier):
    with connect_db() as db:
        rows = db.execute(
            "SELECT * FROM matches WHERE tier=? AND publish_status='PUBLISHED' ORDER BY id DESC LIMIT 10",
            (tier,),
        ).fetchall()

    if not rows:
        await send_message(
            chat_id,
            f"ℹ️ No published <b>{tier}</b> predictions yet.\nCheck back soon! {BRAND_TAG}",
            {"inline_keyboard": [[{"text": "🏠 Main Menu", "callback_data": "menu:home", "style": "primary"}]]},
        )
        return

    title = f"💎 <b>{PROJECT_NAME.upper()} — VIP PICKS</b>" if tier == "VIP" else f"⚽ <b>{PROJECT_NAME.upper()} — FREE PICKS</b>"
    body = "\n".join(format_match_block(m, i + 1) for i, m in enumerate(rows))
    await send_message(
        chat_id,
        f"{title} ({BRAND_TAG})\n━━━━━━━━━━━━━━━━━━━━\n\n{body}",
        {
            "inline_keyboard": [
                [{"text": "👑 JOIN VIP NOW", "callback_data": "menu:join_vip", "style": "danger"}],
                [{"text": "🏠 Main Menu", "callback_data": "menu:home", "style": "primary"}],
            ]
        },
    )


async def show_results_or_tickets(chat_id, only_won=False):
    with connect_db() as db:
        if only_won:
            rows = db.execute("SELECT * FROM matches WHERE result='WON' ORDER BY id DESC LIMIT 12").fetchall()
        else:
            rows = db.execute("SELECT * FROM matches WHERE result!='PENDING' ORDER BY id DESC LIMIT 12").fetchall()

        won_count = db.execute("SELECT COUNT(*) AS n FROM matches WHERE result='WON'").fetchone()["n"]
        lost_count = db.execute("SELECT COUNT(*) AS n FROM matches WHERE result='LOST'").fetchone()["n"]

    decided = won_count + lost_count
    win_rate = f"{(won_count / decided * 100):.1f}%" if decided > 0 else "N/A"
    header = "🏆 <b>WINNING TICKETS</b>" if only_won else "📊 <b>PREVIOUS RESULTS</b>"

    if not rows:
        await send_message(
            chat_id,
            f"{header}\n\nℹ️ No settled results recorded yet.",
            {"inline_keyboard": [[{"text": "🏠 Main Menu", "callback_data": "menu:home", "style": "primary"}]]},
        )
        return

    body = "\n".join(format_match_block(m, i + 1) for i, m in enumerate(rows))
    await send_message(
        chat_id,
        f"{header} ({PROJECT_NAME} • {BRAND_TAG})\n"
        f"📈 Verified Win Rate: <b>{win_rate}</b> ({won_count}W / {lost_count}L)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n{body}",
        {"inline_keyboard": [[{"text": "🏠 Main Menu", "callback_data": "menu:home", "style": "primary"}]]},
    )


async def show_account(chat_id, user_id):
    global bot_username
    user = get_user(user_id)
    country = get_country_info(get_user_country(user_id))
    with connect_db() as db:
        sub = db.execute("SELECT * FROM subscriptions WHERE user_id=?", (user_id,)).fetchone()
        ref_count = db.execute("SELECT COUNT(*) AS n FROM users WHERE referred_by=?", (user_id,)).fetchone()["n"]
        paid_refs = db.execute(
            "SELECT COUNT(*) AS n FROM users WHERE referred_by=? AND referral_rewarded=1",
            (user_id,),
        ).fetchone()["n"]

    if sub and sub["status"] == "ACTIVE":
        exp = "Lifetime (Never Expires)" if sub["is_lifetime"] else format_eat(sub["expires_at"])
        vip_status = f"✅ <b>ACTIVE</b> ({html_escape(sub['plan'].title())})\n📅 Expires: <b>{exp}</b>"
    else:
        vip_status = "❌ <b>INACTIVE</b>"

    ref_link = f"https://t.me/{bot_username}?start=ref_{user_id}" if bot_username else "Generating..."
    reward_days = get_setting("referral_reward_days", "2")

    n_free = "🔔 ON" if (not user or user["notify_free"]) else "🔕 OFF"
    n_vip = "🔔 ON" if (not user or user["notify_vip"]) else "🔕 OFF"
    n_news = "🔔 ON" if (not user or user["notify_news"]) else "🔕 OFF"

    await send_message(
        chat_id,
        f"👤 <b>MY ACCOUNT — {PROJECT_NAME.upper()}</b> ({BRAND_TAG})\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 User ID: <code>{user_id}</code>\n"
        f"{country['flag']} Country: <b>{country['name']} ({country['currency']})</b>\n"
        f"👑 VIP: {vip_status}\n\n"
        f"🎁 <b>REFERRAL PROGRAM (+{reward_days} VIP days per paid friend)</b>\n"
        f"👥 Invited: <b>{ref_count}</b>  •  💎 Paid & Rewarded: <b>{paid_refs}</b>\n"
        f"🔗 Your Link:\n<code>{ref_link}</code>\n\n"
        f"⚙️ <b>Tap below to customize:</b>",
        {
            "inline_keyboard": [
                [
                    {"text": f"Free Tips: {n_free}", "callback_data": "notify:free", "style": "success"},
                    {"text": f"VIP Tips: {n_vip}", "callback_data": "notify:vip", "style": "primary"},
                ],
                [
                    {"text": f"News: {n_news}", "callback_data": "notify:news", "style": "primary"},
                    {"text": f"{country['flag']} Change Country", "callback_data": "menu:country", "style": "success"},
                ],
                [
                    {"text": "👑 Renew / Join VIP", "callback_data": "menu:join_vip", "style": "danger"},
                    {"text": "🏠 Main Menu", "callback_data": "menu:home"},
                ],
            ]
        },
    )


async def show_user_payments(chat_id, user_id):
    with connect_db() as db:
        orders = db.execute(
            "SELECT * FROM orders WHERE user_id=? ORDER BY created_at DESC LIMIT 8",
            (user_id,),
        ).fetchall()

    vip_line = "✅ Active" if has_active_vip(user_id) else "❌ Not Active"
    text = f"💳 <b>PAYMENT & SUBSCRIPTION</b> ({PROJECT_NAME} • {BRAND_TAG})\n\n👑 Status: <b>{vip_line}</b>\n\n"
    if not orders:
        text += "ℹ️ No payment records yet."
    else:
        text += "<b>Recent Transactions:</b>\n"
        for o in orders:
            rec = f" ({o['mpesa_receipt']})" if o["mpesa_receipt"] else ""
            amt_str = o["local_amount"] or f"KES {o['amount']}"
            text += f"• <b>{o['plan'].title()}</b> — {amt_str} [{o['status']}]{rec}\n"

    await send_message(
        chat_id,
        text,
        {
            "inline_keyboard": [
                [{"text": "👑 Subscribe / Renew VIP", "callback_data": "menu:join_vip", "style": "danger"}],
                [{"text": "🏠 Main Menu", "callback_data": "menu:home", "style": "primary"}],
            ]
        },
    )


# --------------------------------------------------
# ADMIN DASHBOARD, MATCH EDITOR, USERS & CSV EXPORT
# --------------------------------------------------

async def show_admin_stats(chat_id):
    today_eat_midnight_utc = (
        utcnow().astimezone(EAT).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()
    )
    with connect_db() as db:
        total_users = db.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        active_vip = db.execute("SELECT COUNT(*) AS n FROM subscriptions WHERE status='ACTIVE'").fetchone()["n"]
        expired_vip = db.execute("SELECT COUNT(*) AS n FROM subscriptions WHERE status='EXPIRED'").fetchone()["n"]
        paid_cnt = db.execute("SELECT COUNT(*) AS n FROM orders WHERE status='PAID'").fetchone()["n"]
        pending_cnt = db.execute("SELECT COUNT(*) AS n FROM orders WHERE status IN ('PENDING','PENDING_REVIEW')").fetchone()["n"]
        failed_cnt = db.execute("SELECT COUNT(*) AS n FROM orders WHERE status='FAILED'").fetchone()["n"]
        total_rev = db.execute("SELECT COALESCE(SUM(amount),0) AS n FROM orders WHERE status='PAID'").fetchone()["n"]
        today_rev = db.execute(
            "SELECT COALESCE(SUM(amount),0) AS n FROM orders WHERE status='PAID' AND created_at >= ?",
            (today_eat_midnight_utc,),
        ).fetchone()["n"]

    await send_message(
        chat_id,
        f"📊 <b>{PROJECT_NAME.upper()} ANALYTICS</b> ({BRAND_TAG})\n"
        f"🗄 Storage: <b>{storage_label()}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 Registered Users: <b>{total_users}</b>\n"
        f"👑 Active VIP Members: <b>{active_vip}</b>\n"
        f"⏰ Expired VIP Members: <b>{expired_vip}</b>\n\n"
        f"💰 Total Revenue (KES equiv): <b>KES {total_rev}</b>\n"
        f"📅 Today's Revenue (EAT): <b>KES {today_rev}</b>\n"
        f"✅ Paid Orders: <b>{paid_cnt}</b>\n"
        f"⏳ Pending/Review: <b>{pending_cnt}</b>\n"
        f"❌ Failed Orders: <b>{failed_cnt}</b>",
        admin_keyboard(),
    )


async def show_admin_packages(chat_id):
    with connect_db() as db:
        plans = db.execute("SELECT * FROM plans ORDER BY price").fetchall()

    txt = (
        f"💰 <b>VIP PACKAGES ({PROJECT_NAME})</b>\n"
        f"🗄 Storage: <b>{storage_label()}</b>\n"
        "<i>Base prices are in KES (auto-converted to each buyer's country currency).</i>\n\n"
    )
    rows = []
    for p in plans:
        title = p["title"] or p["name"].title()
        dur = "Lifetime" if p["is_lifetime"] else f"{p['days']}d"
        txt += f"• <b>{html_escape(title)}</b> — <b>KES {p['price']}</b> ({dur})\n  <i>{html_escape(p['benefits'])}</i>\n"
        rows.append([
            {"text": f"💰 {title} Price", "callback_data": f"admin:editprice:{p['name']}", "style": "success"},
            {"text": "⏱ Days", "callback_data": f"admin:editdays:{p['name']}", "style": "primary"},
            {"text": "📝 Benefits", "callback_data": f"admin:editbenefits:{p['name']}", "style": "primary"},
        ])

    rows.append([{"text": "⬅️ Back to Admin", "callback_data": "admin:home"}])
    await send_message(chat_id, txt, {"inline_keyboard": rows})


async def show_admin_matches(chat_id):
    with connect_db() as db:
        queued_free = db.execute("SELECT COUNT(*) AS n FROM matches WHERE tier='FREE' AND publish_status='QUEUED'").fetchone()["n"]
        queued_vip = db.execute("SELECT COUNT(*) AS n FROM matches WHERE tier='VIP' AND publish_status='QUEUED'").fetchone()["n"]
        recent = db.execute("SELECT * FROM matches ORDER BY id DESC LIMIT 6").fetchall()

    text = (
        f"⚽ <b>MATCH STUDIO — {PROJECT_NAME.upper()}</b> ({BRAND_TAG})\n"
        f"Queued: <b>{queued_free} FREE</b>  |  <b>{queued_vip} VIP</b>\n\n"
        "<b>Format (add optional 7th item to Schedule in EAT):</b>\n"
        "<code>/addmatch VIP | Arsenal vs Chelsea | Over 2.5 | 1.85 | 2026-11-15 | 19:45 | 2026-11-15 16:00</code>\n\n"
        "<b>Recent Matches (updates channel post automatically!):</b>\n"
    )
    rows = [
        [{"text": "➕ Guided Add Match", "callback_data": "admin:add_match", "style": "primary"}],
        [
            {"text": f"📢 Post Queued FREE ({queued_free})", "callback_data": "admin:pub_free", "style": "success"},
            {"text": f"💎 Post Queued VIP ({queued_vip})", "callback_data": "admin:pub_vip", "style": "danger"},
        ],
    ]
    for m in recent:
        text += f"• <b>#{m['id']}</b> [{m['tier']}] {html_escape(m['match_name'])} — {m['result']} ({m['publish_status']})\n"
        rows.append([
            {"text": f"#{m['id']} ✅Won", "callback_data": f"admin:res:{m['id']}:WON", "style": "success"},
            {"text": "❌Lost", "callback_data": f"admin:res:{m['id']}:LOST", "style": "danger"},
            {"text": "⏸Postp", "callback_data": f"admin:res:{m['id']}:POSTPONED", "style": "primary"},
            {"text": "✏️Edit", "callback_data": f"admin:editmatch:{m['id']}", "style": "primary"},
            {"text": "🗑", "callback_data": f"admin:delmatch:{m['id']}", "style": "danger"},
        ])
    rows.append([{"text": "⬅️ Back to Admin", "callback_data": "admin:home"}])
    await send_message(chat_id, text, {"inline_keyboard": rows})


async def show_admin_transactions(chat_id):
    with connect_db() as db:
        orders = db.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT 10").fetchall()

    if not orders:
        await send_message(chat_id, "ℹ️ No transactions yet.", admin_keyboard())
        return

    text = f"🧾 <b>RECENT TRANSACTIONS</b> ({BRAND_TAG})\n\n"
    rows = []
    for o in orders:
        amt_str = o["local_amount"] or f"KES {o['amount']}"
        text += (
            f"• <code>{o['external_ref']}</code> | <b>{o['plan'].title()}</b> {amt_str} "
            f"| <b>{o['status']}</b> ({o['provider']}) | User <code>{o['user_id']}</code>\n"
        )
        if o["status"] in ("PENDING", "PENDING_REVIEW"):
            rows.append([
                {"text": f"✅ Approve {o['external_ref'][:8]}", "callback_data": f"admin:approve:{o['external_ref']}", "style": "success"},
                {"text": "❌ Reject", "callback_data": f"admin:reject:{o['external_ref']}", "style": "danger"},
            ])
    rows.append([{"text": "⬅️ Back to Admin", "callback_data": "admin:home"}])
    await send_message(chat_id, text, {"inline_keyboard": rows})


async def show_admin_users(chat_id):
    with connect_db() as db:
        users = db.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT 10").fetchall()
        subs = db.execute("SELECT COUNT(*) AS n FROM subscriptions WHERE status='ACTIVE'").fetchone()["n"]

    txt = f"👥 <b>USERS & VIP MANAGEMENT</b> (Active VIPs: {subs})\n\n"
    rows = []
    for u in users:
        c = get_country_info(u["country"])
        vip_tag = "💎" if has_active_vip(u["user_id"]) else "🆓"
        blk_tag = "🚫BLOCKED" if u["is_blocked"] else ""
        uname = f"@{u['username']}" if u["username"] else u["first_name"]
        txt += f"• {vip_tag} {c['flag']} <code>{u['user_id']}</code> ({html_escape(uname)}) {blk_tag}\n"
        rows.append([{
            "text": f"{'✅ Unblock' if u['is_blocked'] else '🚫 Block'} {u['user_id']}",
            "callback_data": f"admin:toggleblock:{u['user_id']}",
            "style": "success" if u["is_blocked"] else "danger",
        }])

    txt += "\nCommands: <code>/grant &lt;id&gt; &lt;days&gt;</code> · <code>/revoke &lt;id&gt;</code> · <code>/reply &lt;id&gt; &lt;msg&gt;</code>"
    rows.append([{"text": "⬅️ Back to Admin", "callback_data": "admin:home"}])
    await send_message(chat_id, txt, {"inline_keyboard": rows})


async def show_admin_channels_panel(chat_id):
    auto_btn = get_setting("auto_channel_buttons", "0") == "1"
    await send_message(
        chat_id,
        f"⚙️ <b>CHANNELS & SETTINGS ({PROJECT_NAME})</b>\n\n"
        f"• Database: <b>{storage_label()}</b>\n"
        f"• Main Channel: <code>{get_setting('main_channel_id', CHANNEL_ID)}</code>\n"
        f"• VIP Channel: <code>{get_setting('vip_channel_id', VIP_CHANNEL_ID)}</code>\n"
        f"• PayHero Channel ID: <code>{get_setting('payhero_channel_id', PAYHERO_CHANNEL_ID)}</code>\n"
        f"• PayHero Auth: <code>****{PAYHERO_AUTH_TOKEN[-4:]}</code>\n"
        f"• Referral Bonus Days: <code>{get_setting('referral_reward_days', '2')}</code>\n"
        f"• Website URL: <code>{html_escape(get_website_url())}</code>\n"
        f"• Contact Admin URL: <code>{html_escape(get_setting('admin_contact_url'))}</code>\n"
        f"• Auto-Attach Buttons on Manual Channel Posts: <b>{'ON ✅' if auto_btn else 'OFF 🔕 (Manual)'}</b>\n\n"
        "<i>Update FX rates anytime via:</i> <code>/setrate UG 29.0</code>",
        {
            "inline_keyboard": [
                [{"text": f"Toggle Auto Channel Buttons ({'ON' if auto_btn else 'OFF'})", "callback_data": "admin:toggle_autobtn", "style": "danger" if auto_btn else "success"}],
                [
                    {"text": "✏️ Main Channel ID", "callback_data": "admin:editsetting:main_channel_id", "style": "primary"},
                    {"text": "✏️ VIP Channel ID", "callback_data": "admin:editsetting:vip_channel_id", "style": "primary"},
                ],
                [
                    {"text": "✏️ Website URL", "callback_data": "admin:editsetting:website_url", "style": "success"},
                    {"text": "✏️ Referral Days", "callback_data": "admin:editsetting:referral_reward_days", "style": "success"},
                ],
                [{"text": "⬅️ Back to Admin", "callback_data": "admin:home"}],
            ]
        },
    )


async def export_csv_backups(chat_id):
    with connect_db() as db:
        orders = db.execute("SELECT * FROM orders ORDER BY created_at DESC").fetchall()
        subs = db.execute("SELECT * FROM subscriptions ORDER BY expires_at DESC").fetchall()

    buf1 = io.StringIO()
    w1 = csv.writer(buf1)
    w1.writerow(["external_ref", "user_id", "plan", "amount_kes", "currency", "local_amount", "phone", "provider", "status", "receipt", "created_at"])
    for o in orders:
        w1.writerow([o["external_ref"], o["user_id"], o["plan"], o["amount"], o["currency"], o["local_amount"], o["phone"], o["provider"], o["status"], o["mpesa_receipt"], o["created_at"]])
    await telegram_send_document(chat_id, "prime_tips_orders.csv", buf1.getvalue().encode("utf-8"), "📦 Orders Backup")

    buf2 = io.StringIO()
    w2 = csv.writer(buf2)
    w2.writerow(["user_id", "plan", "expires_at", "status", "is_lifetime"])
    for s in subs:
        w2.writerow([s["user_id"], s["plan"], s["expires_at"], s["status"], s["is_lifetime"]])
    await telegram_send_document(chat_id, "prime_tips_subscriptions.csv", buf2.getvalue().encode("utf-8"), "👑 VIP Subscriptions Backup")


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
    if db_user and db_user["is_blocked"] and not is_admin(user_id):
        await send_message(chat_id, "🚫 Your account has been suspended by the administrator.")
        return

    if message.get("contact"):
        c_phone = message["contact"].get("phone_number", "")
        detected = detect_country_from_phone(c_phone)
        if detected:
            set_user_country(user_id, detected)
            c_info = get_country_info(detected)
            await send_message(chat_id, f"✅ Detected country: {c_info['flag']} <b>{c_info['name']} ({c_info['currency']})</b>!")

    if text.startswith("/"):
        cmd = text.split()[0].split("@")[0].lower()

        if cmd == "/start":
            clear_session(user_id)
            if " vip" in text.lower():
                await show_join_vip_screen(chat_id, user_id)
            else:
                await show_main_menu(chat_id, user_id)
            return

        if cmd == "/country":
            await send_message(chat_id, "🌍 <b>Select your country & currency:</b>", country_keyboard("menu:home"))
            return

        if cmd == "/cancel":
            clear_session(user_id)
            await send_message(chat_id, "🛑 Action cancelled.", main_menu_keyboard(user_id))
            return

        if cmd == "/status":
            await show_user_payments(chat_id, user_id)
            return

        if cmd == "/admin":
            if not is_admin(user_id):
                await send_message(chat_id, "⛔ Unauthorized.")
                return
            await send_message(chat_id, f"⚡ <b>{PROJECT_NAME.upper()} ADMIN</b> ({BRAND_TAG})", admin_keyboard())
            return

        if cmd == "/setrate" and is_admin(user_id):
            parts = text.split()
            if len(parts) != 3 or parts[1].upper() not in COUNTRIES or parts[1].upper() == "KE":
                await send_message(chat_id, "Usage: <code>/setrate UG 29.0</code>\nCodes: UG, TZ, RW, NG, GH, ZA, INTL")
                return
            try:
                rate = float(parts[2])
                if rate <= 0:
                    raise ValueError
            except ValueError:
                await send_message(chat_id, "⚠️ Enter a positive numeric exchange rate per 1 KES.")
                return
            code = parts[1].upper()
            set_setting(f"rate_{code}", str(rate))
            await send_message(chat_id, f"✅ Updated <b>{code}</b> rate to <b>{rate}</b> per 1 KES.")
            return

        if cmd == "/reply" and is_admin(user_id):
            parts = text.split(None, 2)
            if len(parts) < 3 or not parts[1].isdigit():
                await send_message(chat_id, "Usage: <code>/reply &lt;user_id&gt; &lt;message&gt;</code>")
                return
            target_uid, reply_body = int(parts[1]), parts[2]
            try:
                await send_message(target_uid, f"💬 <b>REPLY FROM ADMIN ({BRAND_TAG})</b>\n\n{html_escape(reply_body)}")
                await send_message(chat_id, f"✅ Reply sent to <code>{target_uid}</code>.")
            except Exception as e:
                await send_message(chat_id, f"⚠️ Could not send reply: <code>{html_escape(str(e))}</code>")
            return

        if cmd == "/export" and is_admin(user_id):
            await export_csv_backups(chat_id)
            return

        if cmd == "/addpostbuttons" and is_admin(user_id):
            arg = text.split(None, 1)[1].strip() if len(text.split(None, 1)) > 1 else ""
            msg_id = extract_channel_msg_id(message, arg)
            if not msg_id:
                await send_message(chat_id, "Usage: <code>/addpostbuttons https://t.me/yourchannel/45</code> or <code>/addpostbuttons 45</code>")
                return
            try:
                await attach_buttons_to_main_post(msg_id)
                await send_message(chat_id, f"✅ Colorful buttons attached to Main Channel post <code>{msg_id}</code>!")
            except Exception as e:
                await send_message(chat_id, f"⚠️ Could not attach buttons: <code>{html_escape(str(e))}</code>")
            return

        if cmd == "/setvipodds" and is_admin(user_id):
            value = text.split(None, 1)[1].strip() if len(text.split(None, 1)) > 1 else ""
            if not value:
                await send_message(chat_id, "Usage: <code>/setvipodds 🔥 Today: 14.50 Total VIP Odds (4 Matches)</code>")
                return
            set_setting("vip_odds_info", value[:220])
            await send_message(chat_id, f"✅ VIP Odds description updated:\n<b>{html_escape(value[:220])}</b>")
            return

        if cmd == "/postchannel" and is_admin(user_id):
            body = text.split(None, 1)[1].strip() if len(text.split(None, 1)) > 1 else ""
            if not body and not photo_file_id:
                await send_message(chat_id, "Usage: <code>/postchannel Today's VIP ticket is ready!</code>")
                return
            await publish_custom_main_post(body, photo_file_id=photo_file_id)
            await send_message(chat_id, "✅ Posted to Main Channel with colorful buttons!")
            return

        if cmd == "/addbutton" and is_admin(user_id):
            value = text.split(None, 1)[1].strip() if len(text.split(None, 1)) > 1 else ""
            if "|" not in value:
                await send_message(chat_id, "Usage: <code>/addbutton Channel|https://t.me/example</code>")
                return
            label, url = [x.strip() for x in value.split("|", 1)]
            if not label or not url.startswith("https://"):
                await send_message(chat_id, "⚠️ Provide a label and valid HTTPS URL.")
                return
            with connect_db() as db:
                db.execute("INSERT INTO custom_buttons(label,url) VALUES(?,?)", (label[:50], url))
            await send_message(chat_id, "✅ Custom button added under /postvip.")
            return

        if cmd == "/removebuttons" and is_admin(user_id):
            with connect_db() as db:
                db.execute("DELETE FROM custom_buttons")
            await send_message(chat_id, "🗑 Custom buttons cleared.")
            return

        if cmd == "/setprice" and is_admin(user_id):
            parts = text.split()
            if len(parts) != 3 or not parts[2].isdigit():
                await send_message(chat_id, "Usage: <code>/setprice monthly 1000</code>")
                return
            plan_name, price = parts[1].lower(), int(parts[2])
            if not get_plan(plan_name) or price < 1:
                await send_message(chat_id, "⚠️ Invalid plan or price.")
                return
            with connect_db() as db:
                db.execute("UPDATE plans SET price=? WHERE name=?", (price, plan_name))
            await send_message(chat_id, f"✅ Updated <b>{plan_name.title()}</b> to <b>KES {price}</b>.")
            return

        if cmd == "/grant" and is_admin(user_id):
            parts = text.split()
            if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
                await send_message(chat_id, "Usage: <code>/grant &lt;user_id&gt; &lt;days&gt;</code>")
                return
            target_id, days = int(parts[1]), int(parts[2])
            await give_vip_access(target_id, f"{days}-day", days_override=days)
            await send_message(chat_id, f"✅ Granted {days} VIP day(s) to <code>{target_id}</code>.")
            return

        if cmd == "/revoke" and is_admin(user_id):
            parts = text.split()
            if len(parts) != 2 or not parts[1].isdigit():
                await send_message(chat_id, "Usage: <code>/revoke &lt;user_id&gt;</code>")
                return
            target_id = int(parts[1])
            vip_chat = get_setting("vip_channel_id", VIP_CHANNEL_ID)
            try:
                await telegram("banChatMember", {"chat_id": vip_chat, "user_id": target_id})
                await telegram("unbanChatMember", {"chat_id": vip_chat, "user_id": target_id, "only_if_banned": True})
            except Exception:
                pass
            with connect_db() as db:
                db.execute("UPDATE subscriptions SET status='EXPIRED' WHERE user_id=?", (target_id,))
            await send_message(chat_id, f"🚫 Revoked VIP for <code>{target_id}</code>.")
            return

        if cmd in ("/block", "/unblock") and is_admin(user_id):
            parts = text.split()
            if len(parts) != 2 or not parts[1].isdigit():
                await send_message(chat_id, f"Usage: <code>{cmd} &lt;user_id&gt;</code>")
                return
            target_id = int(parts[1])
            if is_admin(target_id):
                await send_message(chat_id, "⚠️ Cannot block administrator.")
                return
            val = 1 if cmd == "/block" else 0
            with connect_db() as db:
                db.execute("UPDATE users SET is_blocked=? WHERE user_id=?", (val, target_id))
            await send_message(chat_id, f"{'🚫 Blocked' if val else '✅ Unblocked'} user <code>{target_id}</code>.")
            return

        if cmd == "/addmatch" and is_admin(user_id):
            raw = text.split(None, 1)[1] if len(text.split(None, 1)) > 1 else ""
            parts = [p.strip() for p in raw.split("|")]
            if len(parts) < 6 or parts[0].upper() not in ("FREE", "VIP"):
                await send_message(
                    chat_id,
                    "Usage:\n<code>/addmatch VIP | Arsenal vs Chelsea | Over 2.5 | 1.85 | 2026-11-15 | 19:45</code>\n"
                    "Optional 7th item for scheduling (EAT): <code>| 2026-11-15 16:00</code>",
                )
                return
            tier, match_name, tip, odds, mdate, mtime = parts[:6]
            sched_utc = None
            pub_status = "QUEUED"
            if len(parts) >= 7 and parts[6]:
                try:
                    dt_eat = datetime.strptime(parts[6], "%Y-%m-%d %H:%M").replace(tzinfo=EAT)
                    sched_utc = dt_eat.astimezone(timezone.utc).isoformat()
                    pub_status = "SCHEDULED"
                except ValueError:
                    await send_message(chat_id, "⚠️ Optional schedule format must be <code>YYYY-MM-DD HH:MM</code> (EAT).")
                    return

            with connect_db() as db:
                db.execute(
                    """
                    INSERT INTO matches(tier,match_name,tip,odds,match_date,kickoff_time,publish_status,scheduled_for,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (tier.upper(), match_name, tip, odds, mdate, mtime, pub_status, sched_utc, utcnow().isoformat()),
                )
            await send_message(chat_id, f"✅ Match saved as <b>{pub_status}</b> ({tier.upper()}). Open Match Studio to publish!")
            return

        if cmd == "/setsetting" and is_admin(user_id):
            parts = text.split(None, 2)
            allowed = {
                "main_channel_id",
                "vip_channel_id",
                "payhero_channel_id",
                "referral_reward_days",
                "vip_odds_info",
                "admin_contact_url",
                "website_url",
                "auto_channel_buttons",
            }
            if len(parts) != 3 or parts[1] not in allowed:
                await send_message(chat_id, f"Usage: <code>/setsetting &lt;{'|'.join(sorted(allowed))}&gt; &lt;value&gt;</code>")
                return
            set_setting(parts[1], parts[2].strip())
            await send_message(chat_id, f"✅ Saved <code>{parts[1]}</code> = <code>{html_escape(parts[2].strip())}</code>")
            return

        if cmd == "/postvip" and is_admin(user_id):
            await publish_vip_post()
            await send_message(chat_id, "✅ VIP promotional banner published.")
            return

        await send_message(chat_id, "❓ Unknown command. Tap below to open the menu.", main_menu_keyboard(user_id))
        return

    # ---- Session state inputs ----
    sess = get_session(user_id)
    if sess:
        state, payload = sess["state"], sess["payload"]

        if state == "WAIT_ATTACH_POST" and is_admin(user_id):
            clear_session(user_id)
            msg_id = extract_channel_msg_id(message, text)
            if not msg_id:
                await send_message(
                    chat_id,
                    "⚠️ Could not read a post ID. Forward the post from your Main Channel, or paste its link/ID.",
                    admin_keyboard(),
                )
                return
            try:
                await attach_buttons_to_main_post(msg_id)
                await send_message(chat_id, f"✅ Colorful buttons attached to post <code>{msg_id}</code>!", admin_keyboard())
            except Exception as e:
                await send_message(chat_id, f"⚠️ Failed: <code>{html_escape(str(e))}</code>", admin_keyboard())
            return

        if state == "WAIT_MPESA_PHONE":
            detected = detect_country_from_phone(text)
            if detected and detected != "KE":
                set_user_country(user_id, detected)
                clear_session(user_id)
                c = get_country_info(detected)
                await send_message(
                    chat_id,
                    f"🌍 Detected <b>{c['flag']} {c['name']}</b> number! Switched your currency to <b>{c['currency']}</b>.\n"
                    "Tap below to pay using your country's payment method:",
                    plans_keyboard(detected),
                )
                return
            phone = normalize_phone(text)
            if not phone:
                await send_message(chat_id, "⚠️ Enter a valid Kenyan number (e.g. <code>0712345678</code>) or /cancel.")
                return
            clear_session(user_id)
            await start_mpesa_payment(user_id, payload, phone, notify_telegram=True)
            return

        if state.startswith("WAIT_MANUAL_"):
            provider = state.replace("WAIT_MANUAL_", "").lower()
            clear_session(user_id)
            await submit_manual_mobile_money(user_id, payload, provider, text)
            return

        if state == "WAIT_SUPPORT_MSG":
            clear_session(user_id)
            kb = {"inline_keyboard": [[{"text": "💬 Reply to User", "callback_data": f"admin:reply:{user_id}", "style": "primary"}]]}
            if photo_file_id:
                await telegram("sendPhoto", {
                    "chat_id": ADMIN_USER_ID,
                    "photo": photo_file_id,
                    "caption": f"📩 <b>NEW SUPPORT PHOTO</b>\nFrom: <code>{user_id}</code>\n\n{html_escape(text)}"[:1020],
                    "parse_mode": "HTML",
                    "reply_markup": kb,
                })
            else:
                await send_message(
                    ADMIN_USER_ID,
                    f"📩 <b>NEW SUPPORT MESSAGE</b>\nFrom: <code>{user_id}</code>\n\n{html_escape(text)}",
                    kb,
                )
            await send_message(chat_id, f"✅ Sent to admin ({BRAND_TAG}). We will reply shortly!", main_menu_keyboard(user_id))
            return

        if state == "WAIT_ADMIN_REPLY" and is_admin(user_id):
            clear_session(user_id)
            target_uid = int(payload)
            try:
                if photo_file_id:
                    await telegram("sendPhoto", {
                        "chat_id": target_uid,
                        "photo": photo_file_id,
                        "caption": f"💬 <b>REPLY FROM ADMIN ({BRAND_TAG})</b>\n\n{html_escape(text)}"[:1020],
                        "parse_mode": "HTML",
                    })
                else:
                    await send_message(target_uid, f"💬 <b>REPLY FROM ADMIN ({BRAND_TAG})</b>\n\n{html_escape(text)}")
                await send_message(chat_id, f"✅ Reply delivered to <code>{target_uid}</code>.", admin_keyboard())
            except Exception as e:
                await send_message(chat_id, f"⚠️ Could not deliver reply: <code>{html_escape(str(e))}</code>", admin_keyboard())
            return

        if state == "WAIT_BROADCAST" and is_admin(user_id):
            clear_session(user_id)
            with connect_db() as db:
                targets = db.execute("SELECT user_id FROM users WHERE is_blocked=0 AND notify_news=1").fetchall()
            sent = 0
            cap = f"📣 <b>ANNOUNCEMENT ({PROJECT_NAME} • {BRAND_TAG})</b>\n\n{html_escape(text)}"
            for t in targets:
                uid = int(t["user_id"])
                try:
                    if photo_file_id:
                        await telegram("sendPhoto", {"chat_id": uid, "photo": photo_file_id, "caption": cap[:1020], "parse_mode": "HTML"})
                    else:
                        await send_message(uid, cap)
                    sent += 1
                except Exception:
                    pass
            await send_message(chat_id, f"✅ Broadcast delivered to <b>{sent}</b> users.", admin_keyboard())
            return

        if state == "WAIT_VIP_ODDS" and is_admin(user_id):
            clear_session(user_id)
            set_setting("vip_odds_info", text[:220])
            await send_message(chat_id, f"✅ <b>VIP Odds Updated!</b>\n\n🎯 {html_escape(text[:220])}", admin_keyboard())
            return

        if state == "WAIT_PLAN_PRICE" and is_admin(user_id):
            clear_session(user_id)
            if not text.isdigit() or int(text) < 1:
                await send_message(chat_id, "⚠️ Enter a whole-number KES price (e.g. <code>500</code>).")
                return
            with connect_db() as db:
                db.execute("UPDATE plans SET price=? WHERE name=?", (int(text), payload))
            await show_admin_packages(chat_id)
            return

        if state == "WAIT_PLAN_DAYS" and is_admin(user_id):
            clear_session(user_id)
            if not text.isdigit() or int(text) < 1:
                await send_message(chat_id, "⚠️ Enter a valid number of days (e.g. <code>30</code>).")
                return
            with connect_db() as db:
                db.execute("UPDATE plans SET days=? WHERE name=?", (int(text), payload))
            await show_admin_packages(chat_id)
            return

        if state == "WAIT_PLAN_BENEFITS" and is_admin(user_id):
            clear_session(user_id)
            with connect_db() as db:
                db.execute("UPDATE plans SET benefits=? WHERE name=?", (text[:200], payload))
            await show_admin_packages(chat_id)
            return

        if state == "WAIT_SETTING_VALUE" and is_admin(user_id):
            clear_session(user_id)
            set_setting(payload, text)
            await show_admin_channels_panel(chat_id)
            return

        if state == "WAIT_CHANNEL_POST" and is_admin(user_id):
            clear_session(user_id)
            await publish_custom_main_post(text, photo_file_id=photo_file_id)
            await send_message(chat_id, "✅ Posted to Main Channel with colorful buttons!", admin_keyboard())
            return

        if state == "WAIT_MATCH_INPUT" and is_admin(user_id):
            clear_session(user_id)
            parts = [p.strip() for p in text.split("|")]
            if len(parts) < 6 or parts[0].upper() not in ("FREE", "VIP"):
                await send_message(
                    chat_id,
                    "⚠️ Format error. Send:\n<code>VIP | Match | Tip | Odds | YYYY-MM-DD | HH:MM</code>",
                )
                return
            tier, match_name, tip, odds, mdate, mtime = parts[:6]
            sched_utc, pub_status = None, "QUEUED"
            if len(parts) >= 7 and parts[6]:
                try:
                    sched_utc = datetime.strptime(parts[6], "%Y-%m-%d %H:%M").replace(tzinfo=EAT).astimezone(timezone.utc).isoformat()
                    pub_status = "SCHEDULED"
                except ValueError:
                    await send_message(chat_id, "⚠️ Schedule format must be <code>YYYY-MM-DD HH:MM</code> (EAT).")
                    return

            with connect_db() as db:
                db.execute(
                    """
                    INSERT INTO matches(tier,match_name,tip,odds,match_date,kickoff_time,publish_status,scheduled_for,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (tier.upper(), match_name, tip, odds, mdate, mtime, pub_status, sched_utc, utcnow().isoformat()),
                )
            await show_admin_matches(chat_id)
            return

        if state == "WAIT_EDIT_MATCH" and is_admin(user_id):
            clear_session(user_id)
            mid = int(payload)
            parts = [p.strip() for p in text.split("|")]
            if len(parts) != 4:
                await send_message(chat_id, "⚠️ Format: <code>Match Name | Tip | Odds | KickoffTime</code>")
                return
            mname, tip, odds, ktime = parts
            with connect_db() as db:
                db.execute(
                    "UPDATE matches SET match_name=?, tip=?, odds=?, kickoff_time=? WHERE id=?",
                    (mname, tip, odds, ktime, mid),
                )
            await sync_channel_message_for_match(mid)
            await show_admin_matches(chat_id)
            return

    await show_main_menu(chat_id, user_id)


# --------------------------------------------------
# CALLBACK HANDLER
# --------------------------------------------------

async def handle_callback(query):
    cid = query["id"]
    user = query["from"]
    user_id = user["id"]
    data = query.get("data", "")
    chat_id = query.get("message", {}).get("chat", {}).get("id") or user_id

    register_user(user)
    db_user = get_user(user_id)
    if db_user and db_user["is_blocked"] and not is_admin(user_id):
        await answer_callback(cid, "Account blocked")
        return

    if data == "menu:home":
        await answer_callback(cid)
        await show_main_menu(chat_id, user_id)
        return

    if data == "menu:country":
        await answer_callback(cid)
        curr = get_country_info(get_user_country(user_id))
        await send_message(
            chat_id,
            f"🌍 <b>SELECT YOUR COUNTRY & CURRENCY</b>\n\n"
            f"Current: {curr['flag']} <b>{curr['name']} ({curr['currency']})</b>\n\n"
            "Prices and payment instructions adapt automatically to your country 👇",
            country_keyboard("menu:join_vip"),
        )
        return

    if data.startswith("setcountry:"):
        code = data.split(":", 1)[1]
        saved = set_user_country(user_id, code)
        c = get_country_info(saved)
        await answer_callback(cid, f"Set to {c['name']} ({c['currency']})")
        await show_join_vip_screen(chat_id, user_id)
        return

    if data == "menu:join_vip":
        await answer_callback(cid)
        await show_join_vip_screen(chat_id, user_id)
        return

    if data == "menu:free_tips":
        await answer_callback(cid)
        await show_predictions_list(chat_id, user_id, "FREE")
        return

    if data == "menu:vip_tips":
        await answer_callback(cid)
        if not has_active_vip(user_id) and not is_admin(user_id):
            vip_odds_info = get_setting("vip_odds_info")
            await send_message(
                chat_id,
                f"🔒 <b>{PROJECT_NAME.upper()} VIP LOCKED</b>\n\n"
                f"🎯 <b>VIP Odds:</b> {html_escape(vip_odds_info)}\n\n"
                "Only active VIP subscribers can access VIP picks.\n"
                "👇 Tap below to activate your VIP membership:",
                plans_keyboard(get_user_country(user_id)),
            )
            return
        await show_predictions_list(chat_id, user_id, "VIP")
        return

    if data == "menu:results":
        await answer_callback(cid)
        await show_results_or_tickets(chat_id, only_won=False)
        return

    if data == "menu:tickets":
        await answer_callback(cid)
        await show_results_or_tickets(chat_id, only_won=True)
        return

    if data == "menu:history":
        await answer_callback(cid)
        await show_results_or_tickets(chat_id, only_won=False)
        return

    if data == "menu:payments":
        await answer_callback(cid)
        await show_user_payments(chat_id, user_id)
        return

    if data == "menu:account":
        await answer_callback(cid)
        await show_account(chat_id, user_id)
        return

    if data == "menu:contact":
        await answer_callback(cid)
        set_session(user_id, "WAIT_SUPPORT_MSG")
        await send_message(
            chat_id,
            f"📞 <b>CONTACT ADMIN ({PROJECT_NAME} • {BRAND_TAG})</b>\n\n"
            f"Direct Telegram: {BRAND_TAG}\n"
            "Or send your message / screenshot right now and I will deliver it to the admin.\n\n"
            "Send /cancel to go back.",
        )
        return

    if data == "menu:how":
        await answer_callback(cid)
        country_code = get_user_country(user_id)
        c = get_country_info(country_code)
        await send_message(
            chat_id,
            f"ℹ️ <b>HOW {PROJECT_NAME.upper()} WORKS ({c['flag']} {c['name']})</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"{c['how_to_pay']}\n\n"
            "🔒 <i>All payments are verified via official API/admin before VIP activation.</i>",
            {
                "inline_keyboard": [
                    [{"text": "👑 JOIN VIP NOW", "callback_data": "menu:join_vip", "style": "danger"}],
                    [{"text": f"{c['flag']} Change Country", "callback_data": "menu:country", "style": "primary"}],
                    [{"text": "🌐 Visit Website", "url": get_website_url(), "style": "success"}],
                    [{"text": "🏠 Main Menu", "callback_data": "menu:home"}],
                ]
            },
        )
        return

    if data.startswith("notify:"):
        field_map = {"free": "notify_free", "vip": "notify_vip", "news": "notify_news"}
        key = data.split(":", 1)[1]
        if key in field_map:
            col = field_map[key]
            with connect_db() as db:
                db.execute(f"UPDATE users SET {col} = 1 - {col} WHERE user_id=?", (user_id,))
            await answer_callback(cid, "Preference updated")
            await show_account(chat_id, user_id)
        return

    if data.startswith("plan:"):
        plan_name = data.split(":", 1)[1]
        plan = get_plan(plan_name)
        if not plan:
            await answer_callback(cid, "Plan unavailable")
            return
        await answer_callback(cid)

        country_code = get_user_country(user_id)
        c = get_country_info(country_code)
        _, local_str = convert_price(plan["price"], country_code)
        plan_title = plan["title"] or f"{plan_name.title()} VIP"

        method_rows = []
        for m_code in c["methods"]:
            label, style = METHOD_LABELS[m_code]
            method_rows.append([{"text": label, "callback_data": f"pay:{plan_name}:{m_code}", "style": style}])
        method_rows.append([{"text": f"{c['flag']} Change Country ({c['currency']})", "callback_data": "menu:country", "style": "primary"}])
        method_rows.append([{"text": "⬅️ Back", "callback_data": "menu:join_vip"}])

        await send_message(
            chat_id,
            f"💳 <b>CHECKOUT — {c['flag']} {c['name'].upper()}</b>\n\n"
            f"👑 Package: <b>{html_escape(plan_title)}</b>\n"
            f"💲 Price: <b>{html_escape(local_str)}</b>\n\n"
            f"<b>Instructions:</b>\n{c['how_to_pay']}\n\n"
            "👇 Choose your payment method:",
            {"inline_keyboard": method_rows},
        )
        return

    if data.startswith("pay:"):
        _, plan_name, provider = data.split(":", 2)
        await answer_callback(cid)
        country = get_country_info(get_user_country(user_id))
        plan = get_plan(plan_name)
        _, local_str = convert_price(plan["price"], get_user_country(user_id)) if plan else (0, "")

        if provider == "mpesa":
            set_session(user_id, "WAIT_MPESA_PHONE", plan_name)
            await send_message(
                chat_id,
                f"📱 Enter your <b>Kenyan M-Pesa phone number</b> (e.g. <code>0712345678</code>) to pay <b>{local_str}</b>:\n\nSend /cancel to stop.",
            )
        else:
            set_session(user_id, f"WAIT_MANUAL_{provider.upper()}", plan_name)
            label, _ = METHOD_LABELS.get(provider, (provider.upper(), "primary"))
            await send_message(
                chat_id,
                f"💳 <b>{html_escape(label)}</b> ({country['flag']} {country['name']})\n\n"
                f"Amount due: <b>{html_escape(local_str)}</b>\n\n"
                f"1️⃣ Contact {BRAND_TAG} for the payment number/details if needed.\n"
                "2️⃣ Send the payment.\n"
                "3️⃣ Reply here with:\n<code>YourPhoneOrEmail | TransactionID</code>\n\n"
                "Send /cancel to stop.",
            )
        return

    if data.startswith("verify:"):
        external_ref = data.split(":", 1)[1]
        with connect_db() as db:
            order = db.execute("SELECT * FROM orders WHERE external_ref=? AND user_id=?", (external_ref, user_id)).fetchone()
        if not order:
            await answer_callback(cid, "Order not found")
            return
        if order["status"] == "PAID":
            await answer_callback(cid, "Already confirmed!")
            return
        if not order["payhero_reference"]:
            await answer_callback(cid, "Processing...")
            return

        await answer_callback(cid, "Checking M-Pesa...")
        try:
            verified = await verify_payhero_transaction(order["payhero_reference"])
            outcome = await finalize_paid_order(order, verified=verified)
            if outcome == "PENDING":
                await send_message(user_id, "⏳ Still waiting on M-Pesa. Tap Verify again in 15 seconds.")
        except Exception:
            await send_message(user_id, "⚠️ Could not reach M-Pesa right now. Try again shortly.")
        return

    if data.startswith("admin:"):
        if not is_admin(user_id):
            await answer_callback(cid, "Unauthorized")
            return
        parts = data.split(":")
        action = parts[1]
        await answer_callback(cid)

        if action == "home":
            await send_message(chat_id, f"⚡ <b>{PROJECT_NAME.upper()} ADMIN</b> ({BRAND_TAG})", admin_keyboard())
        elif action == "stats":
            await show_admin_stats(chat_id)
        elif action == "matches":
            await show_admin_matches(chat_id)
        elif action == "add_match":
            set_session(user_id, "WAIT_MATCH_INPUT")
            await send_message(
                chat_id,
                "⚽ Send match details:\n"
                "<code>VIP | Arsenal vs Chelsea | Over 2.5 | 1.85 | 2026-11-15 | 19:45</code>\n\n"
                "<i>Optional 7th item to auto-schedule (EAT):</i>\n"
                "<code>| 2026-11-15 16:00</code>\n(or /cancel)",
            )
        elif action == "pub_free":
            msg = await publish_match_batch("FREE")
            await send_message(chat_id, "✅ Published queued FREE matches!" if msg else "ℹ️ No queued FREE matches.")
        elif action == "pub_vip":
            msg = await publish_match_batch("VIP")
            await send_message(chat_id, "✅ Published queued VIP matches!" if msg else "ℹ️ No queued VIP matches.")
        elif action == "res" and len(parts) == 4:
            mid, new_res = int(parts[2]), parts[3]
            with connect_db() as db:
                db.execute("UPDATE matches SET result=? WHERE id=?", (new_res, mid))
            await sync_channel_message_for_match(mid)
            await show_admin_matches(chat_id)
        elif action == "editmatch" and len(parts) == 3:
            mid = int(parts[2])
            set_session(user_id, "WAIT_EDIT_MATCH", str(mid))
            await send_message(
                chat_id,
                f"✏️ Editing Match <b>#{mid}</b>. Send updated details in this format:\n"
                "<code>Match Name | Tip | Odds | KickoffTime</code>\n"
                "Example: <code>Arsenal vs Chelsea | Home Win | 1.95 | 19:45 EAT</code>\n(or /cancel)",
            )
        elif action == "delmatch" and len(parts) == 3:
            mid = int(parts[2])
            with connect_db() as db:
                m = db.execute("SELECT tier, channel_msg_id FROM matches WHERE id=?", (mid,)).fetchone()
                db.execute("DELETE FROM matches WHERE id=?", (mid,))
                remaining = []
                if m and m["channel_msg_id"]:
                    remaining = db.execute(
                        "SELECT * FROM matches WHERE channel_msg_id=? ORDER BY id",
                        (m["channel_msg_id"],),
                    ).fetchall()
            if m and m["channel_msg_id"]:
                target_chat = get_setting("vip_channel_id", VIP_CHANNEL_ID) if m["tier"] == "VIP" else get_setting("main_channel_id", CHANNEL_ID)
                if remaining:
                    full_text, _ = render_batch_text(m["tier"], remaining)
                    try:
                        await telegram("editMessageText", {
                            "chat_id": target_chat,
                            "message_id": m["channel_msg_id"],
                            "parse_mode": "HTML",
                            "text": full_text[:4090],
                            "reply_markup": channel_post_keyboard(include_custom=False),
                            "disable_web_page_preview": True,
                        })
                    except Exception:
                        pass
                else:
                    try:
                        await telegram("deleteMessage", {"chat_id": target_chat, "message_id": m["channel_msg_id"]})
                    except Exception:
                        pass
            await show_admin_matches(chat_id)
        elif action == "packages":
            await show_admin_packages(chat_id)
        elif action in ("editprice", "editdays", "editbenefits") and len(parts) == 3:
            plan = get_plan(parts[2])
            if not plan:
                await send_message(chat_id, "⚠️ Package not found.")
                return
            state_map = {"editprice": "WAIT_PLAN_PRICE", "editdays": "WAIT_PLAN_DAYS", "editbenefits": "WAIT_PLAN_BENEFITS"}
            prompt_map = {
                "editprice": f"💰 Enter new <b>KES price</b> for <b>{html_escape(plan['title'])}</b> (currently KES {plan['price']}):",
                "editdays": f"⏱ Enter new <b>duration in days</b> for <b>{html_escape(plan['title'])}</b> (currently {plan['days']}):",
                "editbenefits": f"📝 Enter new <b>benefits description</b> for <b>{html_escape(plan['title'])}</b>:",
            }
            set_session(user_id, state_map[action], parts[2])
            await send_message(chat_id, prompt_map[action] + "\n\nSend /cancel to stop.")
        elif action == "vip_odds":
            set_session(user_id, "WAIT_VIP_ODDS")
            current = get_setting("vip_odds_info")
            await send_message(
                chat_id,
                f"🎯 <b>CURRENT VIP ODDS DESCRIPTION:</b>\n<i>{html_escape(current)}</i>\n\n"
                "Send your new VIP odds description now (or /cancel):",
            )
        elif action == "post_channel":
            set_session(user_id, "WAIT_CHANNEL_POST")
            await send_message(
                chat_id,
                "📝 Send a <b>text message OR a photo with caption</b> to post to your <b>Main Channel</b>.\n\n"
                "Colorful <b>👑 JOIN VIP NOW</b>, <b>📞 Contact Admin</b> & <b>🌐 Website</b> buttons will be attached!",
            )
        elif action == "attach_buttons":
            set_session(user_id, "WAIT_ATTACH_POST")
            await send_message(
                chat_id,
                "📎 <b>MANUAL CHANNEL BUTTONS</b>\n\n"
                "Forward the post from your Main Channel here, OR paste its post link / message ID (or /cancel):",
            )
        elif action == "transactions":
            await show_admin_transactions(chat_id)
        elif action == "approve" and len(parts) == 3:
            with connect_db() as db:
                order = db.execute("SELECT * FROM orders WHERE external_ref=?", (parts[2],)).fetchone()
            if order:
                await finalize_paid_order(order, manual_admin=True)
                await send_message(chat_id, f"✅ Approved <code>{parts[2]}</code>.")
        elif action == "reject" and len(parts) == 3:
            with connect_db() as db:
                order = db.execute("SELECT * FROM orders WHERE external_ref=?", (parts[2],)).fetchone()
                db.execute("UPDATE orders SET status='FAILED' WHERE external_ref=?", (parts[2],))
            if order:
                await send_message(int(order["user_id"]), f"❌ Your payment ref <code>{parts[2]}</code> was rejected. Contact {BRAND_TAG}.")
            await send_message(chat_id, f"🚫 Rejected <code>{parts[2]}</code>.")
        elif action == "users":
            await show_admin_users(chat_id)
        elif action == "toggleblock" and len(parts) == 3:
            target_uid = int(parts[2])
            if not is_admin(target_uid):
                with connect_db() as db:
                    db.execute("UPDATE users SET is_blocked = 1 - is_blocked WHERE user_id=?", (target_uid,))
            await show_admin_users(chat_id)
        elif action == "reply" and len(parts) == 3:
            set_session(user_id, "WAIT_ADMIN_REPLY", parts[2])
            await send_message(chat_id, f"💬 Type your reply (or send a photo) to user <code>{parts[2]}</code> (or /cancel):")
        elif action == "broadcast":
            set_session(user_id, "WAIT_BROADCAST")
            await send_message(chat_id, "📣 Send the announcement text (or photo + caption) to broadcast to users (or /cancel):")
        elif action == "channels":
            await show_admin_channels_panel(chat_id)
        elif action == "toggle_autobtn":
            curr = get_setting("auto_channel_buttons", "0")
            set_setting("auto_channel_buttons", "0" if curr == "1" else "1")
            await show_admin_channels_panel(chat_id)
        elif action == "editsetting" and len(parts) == 3:
            set_session(user_id, "WAIT_SETTING_VALUE", parts[2])
            await send_message(chat_id, f"✏️ Enter the new value for <code>{html_escape(parts[2])}</code> (or /cancel):")
        elif action == "post":
            await publish_vip_post()
            await send_message(chat_id, "✅ VIP promotional banner published.", admin_keyboard())
        elif action == "export":
            await export_csv_backups(chat_id)
        return


# --------------------------------------------------
# BACKGROUND WORKER: EXPIRY, REMINDERS & SCHEDULER
# --------------------------------------------------

async def background_tick():
    now = utcnow()
    now_iso = now.isoformat()
    remind_cutoff = (now + timedelta(hours=12)).isoformat()

    with connect_db() as db:
        due_free = db.execute(
            "SELECT id FROM matches WHERE tier='FREE' AND publish_status='SCHEDULED' AND scheduled_for <= ?",
            (now_iso,),
        ).fetchall()
        due_vip = db.execute(
            "SELECT id FROM matches WHERE tier='VIP' AND publish_status='SCHEDULED' AND scheduled_for <= ?",
            (now_iso,),
        ).fetchall()

    if due_free:
        await publish_match_batch("FREE", [r["id"] for r in due_free])
    if due_vip:
        await publish_match_batch("VIP", [r["id"] for r in due_vip])

    with connect_db() as db:
        to_remind = db.execute(
            """
            SELECT * FROM subscriptions
            WHERE status='ACTIVE' AND is_lifetime=0 AND reminded=0
              AND expires_at > ? AND expires_at <= ?
            """,
            (now_iso, remind_cutoff),
        ).fetchall()

    for m in to_remind:
        uid = m["user_id"]
        with connect_db() as db:
            db.execute("UPDATE subscriptions SET reminded=1 WHERE user_id=?", (uid,))
        try:
            await send_message(
                uid,
                f"⏳ <b>{PROJECT_NAME.upper()} EXPIRY REMINDER</b> ({BRAND_TAG})\n\n"
                f"Your VIP expires on <b>{format_eat(m['expires_at'])}</b>. Renew now to keep access:",
                plans_keyboard(get_user_country(uid)),
            )
        except Exception:
            pass

    with connect_db() as db:
        expired = db.execute(
            "SELECT * FROM subscriptions WHERE status='ACTIVE' AND is_lifetime=0 AND expires_at <= ?",
            (now_iso,),
        ).fetchall()

    vip_chat = get_setting("vip_channel_id", VIP_CHANNEL_ID)
    for m in expired:
        uid = m["user_id"]
        try:
            await telegram("banChatMember", {"chat_id": vip_chat, "user_id": uid})
            await telegram("unbanChatMember", {"chat_id": vip_chat, "user_id": uid, "only_if_banned": True})
        except Exception:
            pass

        with connect_db() as db:
            db.execute("UPDATE subscriptions SET status='EXPIRED' WHERE user_id=?", (uid,))

        try:
            await send_message(
                uid,
                f"⏰ <b>Your {PROJECT_NAME} membership has expired.</b> ({BRAND_TAG})\n\nTap below to renew immediately:",
                plans_keyboard(get_user_country(uid)),
            )
        except Exception:
            pass


async def background_worker():
    global bot_username
    for attempt in range(5):
        try:
            me = await telegram("getMe", {})
            bot_username = me["username"]
            await telegram("setWebhook", {
                "url": PUBLIC_URL.rstrip("/") + "/telegram",
                "secret_token": WEBHOOK_SECRET,
                "allowed_updates": ["message", "callback_query", "channel_post"],
            })
            log.info("%s started as @%s | storage=%s", PROJECT_NAME, bot_username, "postgresql" if USE_POSTGRES else "sqlite")
            break
        except Exception:
            log.exception("Telegram webhook setup failed (attempt %s/5)", attempt + 1)
            await asyncio.sleep(5)

    while True:
        try:
            await background_tick()
        except Exception:
            log.exception("Background worker error")
        await asyncio.sleep(60)


# --------------------------------------------------
# FASTAPI LIFESPAN, CORS, WEBHOOKS & WEBSITE API
# --------------------------------------------------

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class WebPaymentReq(BaseModel):
    user_id: int
    plan: str
    phone: str


class WebManualPaymentReq(BaseModel):
    user_id: int
    plan: str
    provider: str
    contact: str
    transaction_id: str


def verify_website_key(x_api_key: str):
    if not WEBSITE_API_KEY or not secrets.compare_digest(x_api_key, WEBSITE_API_KEY):
        raise HTTPException(status_code=403, detail="Forbidden")


@app.get("/")
async def home():
    return {
        "status": f"{PROJECT_NAME} Platform running",
        "brand": BRAND_TAG,
        "website": get_website_url(),
        "storage": "postgresql" if USE_POSTGRES else "sqlite",
    }


@app.get("/health")
async def health():
    return {"status": "healthy", "storage": "postgresql" if USE_POSTGRES else "sqlite"}


@app.post("/api/auth/telegram")
async def api_auth_telegram(payload: dict):
    received_hash = str(payload.get("hash", ""))
    auth_date = int(payload.get("auth_date", 0) or 0)
    if not received_hash or abs(int(utcnow().timestamp()) - auth_date) > 86400:
        raise HTTPException(status_code=403, detail="Expired or invalid login data")

    items = [f"{k}={v}" for k, v in sorted(payload.items()) if k != "hash" and v is not None]
    data_check_string = "\n".join(items)
    secret_key = hashlib.sha256(BOT_TOKEN.encode("utf-8")).digest()
    calc_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    if not secrets.compare_digest(calc_hash, received_hash):
        raise HTTPException(status_code=403, detail="Invalid Telegram signature")

    uid = int(payload["id"])
    register_user({
        "id": uid,
        "username": payload.get("username", ""),
        "first_name": payload.get("first_name", "Member"),
    })
    return {
        "ok": True,
        "user_id": uid,
        "vip_active": has_active_vip(uid),
        "country": get_user_country(uid),
    }


@app.get("/api/countries")
async def api_countries(request: Request):
    raw_country = (request.headers.get("CF-IPCountry") or request.headers.get("X-Country-Code") or "").upper()
    detected = raw_country if raw_country in COUNTRIES else ("INTL" if raw_country else "")
    return {"detected": detected, "countries": COUNTRIES}


@app.get("/api/packages")
async def api_packages(request: Request, country: str = Query(default="")):
    raw = (country or request.headers.get("CF-IPCountry") or request.headers.get("X-Country-Code") or "KE").upper()
    code = raw if raw in COUNTRIES else "INTL"

    with connect_db() as db:
        plans = db.execute("SELECT name,title,price,days,benefits,is_lifetime FROM plans ORDER BY price").fetchall()

    enriched = []
    for p in plans:
        item = dict(p)
        local_val, local_str = convert_price(p["price"], code)
        item["local_price"] = local_val
        item["local_display"] = local_str
        item["currency"] = get_country_info(code)["currency"]
        enriched.append(item)

    return {
        "country": code,
        "country_info": get_country_info(code),
        "vip_odds_info": get_setting("vip_odds_info"),
        "packages": enriched,
    }


@app.get("/api/predictions")
async def api_predictions():
    with connect_db() as db:
        free = db.execute(
            "SELECT id,match_name,tip,odds,match_date,kickoff_time,result FROM matches WHERE tier='FREE' AND publish_status='PUBLISHED' ORDER BY id DESC LIMIT 15"
        ).fetchall()
        won = db.execute("SELECT COUNT(*) AS n FROM matches WHERE result='WON'").fetchone()["n"]
        lost = db.execute("SELECT COUNT(*) AS n FROM matches WHERE result='LOST'").fetchone()["n"]
    decided = won + lost
    return {
        "project": PROJECT_NAME,
        "brand": BRAND_TAG,
        "win_rate": round(won / decided * 100, 1) if decided else None,
        "free_predictions": [dict(r) for r in free],
    }


@app.get("/api/results")
async def api_results():
    with connect_db() as db:
        rows = db.execute(
            "SELECT id,tier,match_name,tip,odds,match_date,kickoff_time,result FROM matches WHERE result!='PENDING' ORDER BY id DESC LIMIT 25"
        ).fetchall()
        won = db.execute("SELECT COUNT(*) AS n FROM matches WHERE result='WON'").fetchone()["n"]
        lost = db.execute("SELECT COUNT(*) AS n FROM matches WHERE result='LOST'").fetchone()["n"]
    decided = won + lost
    return {
        "win_rate": round(won / decided * 100, 1) if decided else None,
        "won": won,
        "lost": lost,
        "results": [dict(r) for r in rows],
    }


@app.get("/api/tickets")
async def api_tickets():
    with connect_db() as db:
        rows = db.execute(
            "SELECT id,tier,match_name,tip,odds,match_date,kickoff_time,result FROM matches WHERE result='WON' ORDER BY id DESC LIMIT 20"
        ).fetchall()
    return {"winning_tickets": [dict(r) for r in rows]}


@app.get("/api/vip/predictions")
async def api_vip_predictions(user_id: int = Query(...), x_api_key: str = Header(default="")):
    verify_website_key(x_api_key)
    if not has_active_vip(user_id):
        raise HTTPException(status_code=403, detail="Active VIP required")
    with connect_db() as db:
        rows = db.execute(
            "SELECT id,match_name,tip,odds,match_date,kickoff_time,result FROM matches WHERE tier='VIP' AND publish_status='PUBLISHED' ORDER BY id DESC LIMIT 15"
        ).fetchall()
    return {"vip_predictions": [dict(r) for r in rows]}


@app.get("/api/account/{user_id}")
async def api_account(user_id: int, x_api_key: str = Header(default="")):
    verify_website_key(x_api_key)
    with connect_db() as db:
        sub = db.execute("SELECT * FROM subscriptions WHERE user_id=?", (user_id,)).fetchone()
        user = db.execute("SELECT user_id,username,first_name,country FROM users WHERE user_id=?", (user_id,)).fetchone()
    return {
        "user_id": user_id,
        "user": dict(user) if user else None,
        "vip_active": has_active_vip(user_id),
        "subscription": dict(sub) if sub else None,
    }


@app.post("/api/payments/initiate")
async def api_payments_initiate(req: WebPaymentReq, x_api_key: str = Header(default="")):
    verify_website_key(x_api_key)
    phone = normalize_phone(req.phone)
    if not phone:
        raise HTTPException(status_code=400, detail="Invalid Kenyan M-Pesa phone number")
    return await start_mpesa_payment(req.user_id, req.plan, phone, notify_telegram=True)


@app.post("/api/payments/submit-manual")
async def api_payments_submit_manual(req: WebManualPaymentReq, x_api_key: str = Header(default="")):
    verify_website_key(x_api_key)
    return await create_manual_order(req.user_id, req.plan, req.provider.lower(), req.contact, req.transaction_id, notify_telegram=True)


@app.get("/api/payments/status/{external_ref}")
async def api_payments_status(external_ref: str, x_api_key: str = Header(default="")):
    verify_website_key(x_api_key)
    with connect_db() as db:
        order = db.execute("SELECT * FROM orders WHERE external_ref=?", (external_ref,)).fetchone()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order["status"] == "PENDING" and order["payhero_reference"]:
        verified = await verify_payhero_transaction(order["payhero_reference"])
        await finalize_paid_order(order, verified=verified)
        with connect_db() as db:
            order = db.execute("SELECT * FROM orders WHERE external_ref=?", (external_ref,)).fetchone()
    return {"external_ref": external_ref, "status": order["status"], "receipt": order["mpesa_receipt"]}


@app.get("/app", response_class=HTMLResponse)
async def mini_app():
    bot_link = f"https://t.me/{bot_username}?start=vip" if bot_username else "#"
    contact_url = get_setting("admin_contact_url", f"https://t.me/{BRAND_TAG.lstrip('@')}")
    website_url = get_website_url()
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{PROJECT_NAME} • {BRAND_TAG}</title>
<style>
  body{{margin:0;font-family:system-ui,sans-serif;background:linear-gradient(160deg,#05060c,#0b1126,#1b0b33);color:#f8fafc;min-height:100vh;padding:16px}}
  .top-banner{{position:fixed;top:12px;right:12px;z-index:99;background:linear-gradient(135deg,#f59e0b,#7c3aed);color:#fff;font-weight:800;padding:10px 16px;border-radius:999px;text-decoration:none;box-shadow:0 0 18px rgba(245,158,11,.65),0 0 28px rgba(124,58,237,.6);font-size:13px}}
  .hero{{margin-top:46px;background:rgba(15,23,42,.85);border:1px solid rgba(124,58,237,.5);border-radius:18px;padding:18px;box-shadow:0 10px 30px rgba(0,0,0,.5)}}
  h1{{margin:0 0 6px;font-size:22px;background:linear-gradient(90deg,#f59e0b,#10b981,#3b82f6);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
  .odds-box{{margin-top:12px;padding:12px;border-radius:12px;background:linear-gradient(90deg,rgba(245,158,11,.15),rgba(124,58,237,.18));border:1px solid rgba(245,158,11,.45);font-size:13px;color:#fde68a}}
  .card{{background:#0d142b;border:1px solid #1e293b;border-radius:14px;padding:14px;margin-top:12px}}
  .pill{{display:inline-block;padding:3px 9px;border-radius:999px;font-size:11px;font-weight:700;background:#10b98122;color:#10b981;border:1px solid #10b98155}}
  select{{width:100%;margin-top:10px;padding:10px;border-radius:10px;background:#0f172a;color:#f8fafc;border:1px solid #334155}}
  .btn{{display:block;text-align:center;margin-top:12px;padding:12px;border-radius:12px;background:linear-gradient(90deg,#7c3aed,#3b82f6);color:#fff;font-weight:700;text-decoration:none}}
  .btn-contact{{background:linear-gradient(90deg,#059669,#10b981)}}
  .btn-web{{background:linear-gradient(90deg,#d97706,#f59e0b);color:#0f172a}}
</style>
</head>
<body>
  <a class="top-banner" href="{bot_link}">👑 JOIN VIP NOW</a>
  <div class="hero">
    <h1>{PROJECT_NAME.upper()}</h1>
    <div style="color:#94a3b8;font-size:13px">Luxury Football Predictions Platform • {BRAND_TAG}</div>
    <div id="odds-banner" class="odds-box">🎯 Loading VIP odds info...</div>
    <select id="countrySel" onchange="loadData(this.value)">
      <option value="KE">🇰🇪 Kenya (KES)</option>
      <option value="UG">🇺🇬 Uganda (UGX)</option>
      <option value="TZ">🇹🇿 Tanzania (TZS)</option>
      <option value="RW">🇷🇼 Rwanda (RWF)</option>
      <option value="NG">🇳🇬 Nigeria (NGN)</option>
      <option value="GH">🇬🇭 Ghana (GHS)</option>
      <option value="ZA">🇿🇦 South Africa (ZAR)</option>
      <option value="INTL">🌍 International (USD)</option>
    </select>
  </div>
  <div id="content" class="card">Loading live predictions & VIP packages...</div>
  <a class="btn" href="{bot_link}">⚡ Activate VIP Now</a>
  <a class="btn btn-web" href="{website_url}">🌐 Visit Our Website (primetips.site)</a>
  <a class="btn btn-contact" href="{contact_url}">📞 Contact Admin ({BRAND_TAG})</a>
<script>
const esc=s=>String(s??'').replace(/[&<>"']/g,m=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[m]));
function guessCountry(){{
  const tz=(Intl.DateTimeFormat().resolvedOptions().timeZone||'').toLowerCase();
  if(tz.includes('nairobi')) return 'KE';
  if(tz.includes('kampala')) return 'UG';
  if(tz.includes('dar_es_salaam')) return 'TZ';
  if(tz.includes('kigali')) return 'RW';
  if(tz.includes('lagos')) return 'NG';
  if(tz.includes('accra')) return 'GH';
  if(tz.includes('johannesburg')) return 'ZA';
  return 'INTL';
}}
function loadData(code){{
  Promise.all([fetch('/api/predictions').then(r=>r.json()),fetch('/api/packages?country='+encodeURIComponent(code)).then(r=>r.json())])
  .then(([p,pkg])=>{{
    document.getElementById('odds-banner').innerHTML='🎯 <b>VIP Odds:</b> '+esc(pkg.vip_odds_info);
    let h=`<div class="pill">Verified Win Rate: ${{esc(p.win_rate ?? 'Tracking')}}%</div><h3>⚽ Latest Free Picks</h3>`;
    if(!p.free_predictions.length) h+=`<p style="color:#94a3b8">No free picks posted yet.</p>`;
    p.free_predictions.forEach(m=>{{h+=`<div class="card"><b>${{esc(m.match_name)}}</b><br>🎯 ${{esc(m.tip)}} | 📈 ${{esc(m.odds)}} | ${{esc(m.result)}}</div>`;}});
    h+=`<h3 style="margin-top:18px">👑 VIP Packages (${{esc(pkg.country_info.flag)}} ${{esc(pkg.country_info.currency)}})</h3>`;
    pkg.packages.forEach(x=>{{h+=`<div class="card"><b>${{esc(x.title)}}</b> — <span style="color:#f59e0b">${{esc(x.local_display)}}</span><br><small style="color:#94a3b8">${{esc(x.benefits)}}</small></div>`;}});
    document.getElementById('content').innerHTML=h;
  }});
}}
fetch('/api/countries').then(r=>r.json()).then(d=>{{
  const c=d.detected||guessCountry();
  document.getElementById('countrySel').value=c;
  loadData(c);
}}).catch(()=>{{
  const c=guessCountry();
  document.getElementById('countrySel').value=c;
  loadData(c);
}});
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
        elif "channel_post" in update:
            await handle_channel_post(update["channel_post"])
    except Exception:
        log.exception("Telegram update processing failed")
    return {"ok": True}


@app.post("/payhero/callback")
async def payhero_callback(request: Request):
    payload = await request.json()
    try:
        await process_payhero_callback(payload)
    except Exception:
        log.exception("PayHero callback processing failed")
        raise HTTPException(status_code=503, detail="Payment verification failed")
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
```

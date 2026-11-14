import os
import re
import json
import time
import asyncio
import secrets
import sqlite3
import logging
from contextlib import contextmanager
from html import escape as html_escape
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, Request, HTTPException

# --------------------------------------------------
# JOHN TIPSTER VIP BOT
# Telegram administration + PayHero M-Pesa
# --------------------------------------------------

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("john-tipster-vip")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")
CHANNEL_ID = os.getenv("CHANNEL_ID")
VIP_CHANNEL_ID = os.getenv("VIP_CHANNEL_ID")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

PAYHERO_AUTH_TOKEN = os.getenv("PAYHERO_AUTH_TOKEN")
PAYHERO_CHANNEL_ID = os.getenv("PAYHERO_CHANNEL_ID")

PUBLIC_URL = (
    os.getenv("RENDER_EXTERNAL_URL")
    or os.getenv("PUBLIC_URL")
)

DB_PATH = os.getenv("DB_PATH", "john_tipster.db")

PAYHERO_URL = "https://backend.payhero.co.ke/api/v2"

app = FastAPI()
bot_username = None
expiry_task = None


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
        raise RuntimeError(
            "Missing environment variables: " + ", ".join(missing)
        )

    if not str(ADMIN_USER_ID).isdigit():
        raise RuntimeError("ADMIN_USER_ID must be a numeric Telegram user ID")

    if not str(PAYHERO_CHANNEL_ID).isdigit():
        raise RuntimeError("PAYHERO_CHANNEL_ID must be numeric")


def utcnow():
    return datetime.now(timezone.utc)


@contextmanager
def connect_db():
    """
    Proper SQLite context manager to prevent file descriptor leaks.
    """
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db():
    with connect_db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS plans (
                name TEXT PRIMARY KEY,
                price INTEGER NOT NULL,
                days INTEGER NOT NULL
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                user_id INTEGER PRIMARY KEY,
                plan TEXT NOT NULL
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                external_ref TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                plan TEXT NOT NULL,
                amount INTEGER NOT NULL,
                phone TEXT NOT NULL,
                status TEXT NOT NULL,
                payhero_reference TEXT,
                checkout_id TEXT,
                created_at TEXT NOT NULL
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id INTEGER PRIMARY KEY,
                plan TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                status TEXT NOT NULL
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS custom_buttons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                url TEXT NOT NULL
            )
        """)

        defaults = [
            ("daily", 100, 1),
            ("weekly", 300, 7),
            ("monthly", 1000, 30),
        ]

        for name, price, days in defaults:
            con.execute(
                "INSERT OR IGNORE INTO plans(name,price,days) VALUES(?,?,?)",
                (name, price, days)
            )


def is_admin(user_id):
    return str(user_id) == str(ADMIN_USER_ID)


# --------------------------------------------------
# TELEGRAM API
# --------------------------------------------------

async def telegram(method, data):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(url, json=data)
        result = response.json()

    if not result.get("ok"):
        raise RuntimeError(
            f"Telegram {method} failed: {result.get('description', result)}"
        )

    return result.get("result")


async def send_message(chat_id, text, keyboard=None):
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    if keyboard:
        data["reply_markup"] = keyboard

    return await telegram("sendMessage", data)


async def answer_callback(callback_id, text=""):
    try:
        await telegram("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": text,
        })
    except Exception:
        log.exception("Could not answer callback")


def plans_keyboard():
    with connect_db() as con:
        plans = con.execute(
            "SELECT name,price FROM plans ORDER BY price"
        ).fetchall()

    rows = []

    for plan in plans:
        name = plan["name"]
        price = plan["price"]

        rows.append([{
            "text": f"👑 {name.title()} VIP — KES {price}",
            "callback_data": f"plan:{name}",
            "style": "primary",   # Bot API 9.0+: blue button
        }])

    return {"inline_keyboard": rows}


def admin_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "💰 Prices", "callback_data": "admin:prices"},
                {"text": "📊 Statistics", "callback_data": "admin:stats"}
            ],
            [
                {"text": "👥 VIP Members", "callback_data": "admin:members"},
                {"text": "🧾 Payments", "callback_data": "admin:payments"}
            ],
            [
                {
                    "text": "📢 Publish VIP Advert",
                    "callback_data": "admin:post",
                    "style": "success",
                }
            ],
            [
                {"text": "🔘 Button Commands", "callback_data": "admin:buttons"}
            ]
        ]
    }


# --------------------------------------------------
# PAYMENT HELPERS
# --------------------------------------------------

def payhero_headers():
    token = PAYHERO_AUTH_TOKEN.strip()

    if token.lower().startswith("basic "):
        authorization = token
    else:
        authorization = "Basic " + token

    return {
        "Authorization": authorization,
        "Content-Type": "application/json",
    }


def normalize_phone(phone):
    phone = re.sub(r"[\s+\-]", "", phone)

    if phone.startswith("254") and len(phone) == 12:
        phone = "0" + phone[3:]

    if phone.startswith("7") or phone.startswith("1"):
        if len(phone) == 9:
            phone = "0" + phone

    if not re.fullmatch(r"0[17]\d{8}", phone):
        return None

    return phone


def get_plan(plan_name):
    with connect_db() as con:
        return con.execute(
            "SELECT * FROM plans WHERE name=?",
            (plan_name,)
        ).fetchone()


async def start_payment(user_id, plan_name, phone):
    plan = get_plan(plan_name)

    if not plan:
        await send_message(user_id, "⚠️ That VIP plan is unavailable.")
        return

    external_ref = "JT" + secrets.token_hex(8).upper()
    amount = int(plan["price"])

    with connect_db() as con:
        con.execute("""
            INSERT INTO orders
            (external_ref,user_id,plan,amount,phone,status,created_at)
            VALUES(?,?,?,?,?,?,?)
        """, (
            external_ref,
            user_id,
            plan_name,
            amount,
            phone,
            "PENDING",
            utcnow().isoformat()
        ))

    payload = {
        "amount": amount,
        "phone_number": phone,
        "channel_id": int(PAYHERO_CHANNEL_ID),
        "provider": "m-pesa",
        "external_reference": external_ref,
        "callback_url": PUBLIC_URL.rstrip("/") + "/payhero/callback"
    }

    try:
        async with httpx.AsyncClient(timeout=40) as client:
            response = await client.post(
                PAYHERO_URL + "/payments",
                headers=payhero_headers(),
                json=payload
            )

        result = response.json()

        if response.status_code not in (200, 201):
            log.error("PayHero payment error: %s", result)

            with connect_db() as con:
                con.execute(
                    "UPDATE orders SET status='FAILED' WHERE external_ref=?",
                    (external_ref,)
                )

            await send_message(
                user_id,
                "❌ <b>Payment request could not be started.</b>\n\n"
                "Please try again later or contact the administrator."
            )
            return

        if not result.get("success"):
            with connect_db() as con:
                con.execute(
                    "UPDATE orders SET status='FAILED' WHERE external_ref=?",
                    (external_ref,)
                )
            raise RuntimeError(f"PayHero rejected payment: {result}")

        reference = result.get("reference")
        checkout_id = result.get("CheckoutRequestID")

        if not reference:
            with connect_db() as con:
                con.execute(
                    "UPDATE orders SET status='FAILED' WHERE external_ref=?",
                    (external_ref,)
                )
            raise RuntimeError("PayHero did not return a transaction reference")

        with connect_db() as con:
            con.execute("""
                UPDATE orders
                SET payhero_reference=?, checkout_id=?
                WHERE external_ref=?
            """, (reference, checkout_id, external_ref))

        await send_message(
            user_id,
            "📲 <b>M-Pesa STK Push sent!</b>\n\n"
            f"💼 Plan: <b>{html_escape(plan_name.title())} VIP</b>\n"
            f"💰 Amount: <b>KES {amount}</b>\n"
            f"📞 Phone: <code>{html_escape(phone)}</code>\n\n"
            "🔐 Enter your <b>M-Pesa PIN</b> on your phone to complete payment.\n\n"
            "✅ Your VIP access will be sent automatically once payment is verified."
        )

    except Exception:
        log.exception("Payment initiation failed")

        await send_message(
            user_id,
            "⚠️ We could not start your payment.\n\n"
            "Please contact the administrator."
        )


async def verify_payhero_transaction(reference):
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            PAYHERO_URL + "/transaction-status",
            headers=payhero_headers(),
            params={"reference": reference}
        )

    response.raise_for_status()
    return response.json()


# --------------------------------------------------
# VIP MEMBERSHIP
# --------------------------------------------------

async def give_vip_access(user_id, plan_name):
    plan = get_plan(plan_name)

    if not plan:
        return

    existing = None

    with connect_db() as con:
        existing = con.execute(
            "SELECT * FROM subscriptions WHERE user_id=?",
            (user_id,)
        ).fetchone()

    now = utcnow()

    if existing and existing["status"] == "ACTIVE":
        old_expiry = datetime.fromisoformat(existing["expires_at"])
        start = max(now, old_expiry)
    else:
        start = now

    expiry = start + timedelta(days=int(plan["days"]))

    with connect_db() as con:
        con.execute("""
            INSERT INTO subscriptions(user_id,plan,expires_at,status)
            VALUES(?,?,?,'ACTIVE')
            ON CONFLICT(user_id) DO UPDATE SET
                plan=excluded.plan,
                expires_at=excluded.expires_at,
                status='ACTIVE'
        """, (
            user_id,
            plan_name,
            expiry.isoformat()
        ))

    try:
        invite = await telegram("createChatInviteLink", {
            "chat_id": VIP_CHANNEL_ID,
            "expire_date": int(expiry.timestamp()),
            "member_limit": 1,
            "name": f"VIP {user_id}"
        })

        await send_message(
            user_id,
            "🎉 <b>PAYMENT CONFIRMED!</b>\n\n"
            "👑 <b>Welcome to JOHN TIPSTER VIP</b>\n\n"
            f"💼 Plan: <b>{html_escape(plan_name.title())} VIP</b>\n"
            f"📅 Expires: <b>{expiry.strftime('%d %b %Y %H:%M UTC')}</b>\n\n"
            "👇 Tap your private invitation link below to join:",
            {
                "inline_keyboard": [[{
                    "text": "👑 JOIN VIP CHANNEL",
                    "url": invite["invite_link"],
                    "style": "primary",
                }]]
            }
        )

    except Exception:
        log.exception("Could not create or send VIP invitation")

        await send_message(
            ADMIN_USER_ID,
            "⚠️ <b>VIP invite creation failed</b>\n\n"
            f"Payment confirmed for Telegram user <code>{user_id}</code>, "
            "but the bot could not create an invite link.\n\n"
            "Please check bot admin permissions in the VIP channel."
        )

        await send_message(
            user_id,
            "✅ Your payment is confirmed.\n\n"
            "Please contact the administrator for your VIP invitation."
        )


async def process_payhero_callback(payload):
    data = payload.get("response", payload)

    external_ref = data.get("ExternalReference")

    if not external_ref:
        log.warning("PayHero callback has no ExternalReference")
        return

    with connect_db() as con:
        order = con.execute(
            "SELECT * FROM orders WHERE external_ref=?",
            (external_ref,)
        ).fetchone()

    if not order:
        log.warning("Unknown payment reference: %s", external_ref)
        return

    if order["status"] == "PAID":
        return

    reference = order["payhero_reference"]

    if not reference:
        log.warning("Order has no PayHero transaction reference")
        return

    # Do not trust the callback alone. Verify with PayHero.
    verified = await verify_payhero_transaction(reference)

    status = str(verified.get("status", "")).upper()
    success = verified.get("success") is True

    if status != "SUCCESS" and not success:
        if status == "FAILED":
            with connect_db() as con:
                con.execute(
                    "UPDATE orders SET status='FAILED' WHERE external_ref=?",
                    (external_ref,)
                )
        return

    callback_amount = data.get("Amount")

    if callback_amount is not None:
        try:
            if int(float(callback_amount)) != int(order["amount"]):
                log.error("Payment amount mismatch for %s", external_ref)
                return
        except (ValueError, TypeError):
            log.error("Invalid callback amount for %s", external_ref)
            return

    with connect_db() as con:
        con.execute(
            "UPDATE orders SET status='PAID' WHERE external_ref=?",
            (external_ref,)
        )

    await give_vip_access(
        int(order["user_id"]),
        order["plan"]
    )

    await send_message(
        ADMIN_USER_ID,
        "✅ <b>NEW VIP PAYMENT</b>\n\n"
        f"🆔 Customer: <code>{order['user_id']}</code>\n"
        f"💼 Plan: <b>{html_escape(order['plan'].title())}</b>\n"
        f"💰 Amount: <b>KES {order['amount']}</b>\n"
        f"🔗 Ref: <code>{html_escape(external_ref)}</code>"
    )


# --------------------------------------------------
# VIP PROMOTIONAL POST
# --------------------------------------------------

async def publish_vip_post():
    me = await telegram("getMe", {})
    username = me["username"]

    join_url = f"https://t.me/{username}?start=vip"

    # `style: primary` requires Bot API 9.0+ (renders blue on modern
    # Telegram clients; safely ignored by older clients).
    keyboard = {
        "inline_keyboard": [[{
            "text": "👑 JOIN VIP NOW",
            "url": join_url,
            "style": "primary",
        }]]
    }

    with connect_db() as con:
        buttons = con.execute(
            "SELECT * FROM custom_buttons ORDER BY id"
        ).fetchall()

    for button in buttons:
        keyboard["inline_keyboard"].append([{
            "text": button["label"],
            "url": button["url"]
        }])

    message = await telegram("sendMessage", {
        "chat_id": CHANNEL_ID,
        "parse_mode": "HTML",
        "text": (
            "👑 <b>JOHN TIPSTER VIP PREDICTIONS</b>\n\n"
            "⚽ Want access to our <b>premium football predictions</b>?\n\n"
            "💎 Choose your VIP membership plan and pay securely through "
            "<b>M-Pesa</b>.\n\n"
            "🚀 Your membership invitation is sent <b>automatically</b> "
            "after successful payment.\n\n"
            "👇 <b>TAP BELOW TO JOIN VIP</b>"
        ),
        "reply_markup": keyboard
    })

    try:
        await telegram("pinChatMessage", {
            "chat_id": CHANNEL_ID,
            "message_id": message["message_id"],
            "disable_notification": True
        })
    except Exception as e:
        # We log the pin error, but don't crash if it fails (e.g. missing permission)
        log.error(f"Pinning failed: {e}")

    return message


# --------------------------------------------------
# ADMIN REPORTS
# --------------------------------------------------

async def show_prices(chat_id):
    with connect_db() as con:
        plans = con.execute(
            "SELECT * FROM plans ORDER BY price"
        ).fetchall()

    text = "💰 <b>CURRENT VIP PRICES</b>\n\n"

    for p in plans:
        text += (
            f"• <b>{html_escape(p['name'].title())}</b> — "
            f"KES {p['price']}\n"
        )

    text += "\n<i>Change prices with:</i>\n"
    text += "<code>/setprice daily 100</code>\n"
    text += "<code>/setprice weekly 300</code>\n"
    text += "<code>/setprice monthly 1000</code>"

    await send_message(chat_id, text)


async def show_stats(chat_id):
    with connect_db() as con:
        active = con.execute(
            "SELECT COUNT(*) AS n FROM subscriptions WHERE status='ACTIVE'"
        ).fetchone()["n"]

        paid = con.execute(
            "SELECT COUNT(*) AS n FROM orders WHERE status='PAID'"
        ).fetchone()["n"]

        pending = con.execute(
            "SELECT COUNT(*) AS n FROM orders WHERE status='PENDING'"
        ).fetchone()["n"]

        revenue = con.execute(
            "SELECT COALESCE(SUM(amount),0) AS n FROM orders WHERE status='PAID'"
        ).fetchone()["n"]

    await send_message(
        chat_id,
        "📊 <b>JOHN TIPSTER VIP STATISTICS</b>\n\n"
        f"👥 Active VIP members: <b>{active}</b>\n"
        f"✅ Successful payments: <b>{paid}</b>\n"
        f"⏳ Pending payments: <b>{pending}</b>\n"
        f"💰 Recorded revenue: <b>KES {revenue}</b>"
    )


async def show_members(chat_id):
    with connect_db() as con:
        members = con.execute("""
            SELECT * FROM subscriptions
            WHERE status='ACTIVE'
            ORDER BY expires_at DESC
            LIMIT 30
        """).fetchall()

    if not members:
        await send_message(chat_id, "ℹ️ No active VIP members yet.")
        return

    text = "👥 <b>ACTIVE VIP MEMBERS</b>\n\n"

    for m in members:
        text += (
            f"🆔 <code>{m['user_id']}</code>\n"
            f"💼 Plan: <b>{html_escape(m['plan'].title())}</b>\n"
            f"📅 Expires: {m['expires_at']}\n\n"
    

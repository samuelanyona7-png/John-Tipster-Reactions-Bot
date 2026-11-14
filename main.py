import os
import re
import asyncio
import secrets
import sqlite3
import logging
from contextlib import asynccontextmanager, contextmanager
from html import escape as html_escape
from datetime import datetime, timedelta, timezone

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
EAT = timezone(timedelta(hours=3))
STK_COOLDOWN_SECONDS = 45

bot_username = None


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

    if not str(ADMIN_USER_ID).isdigit():
        raise RuntimeError("ADMIN_USER_ID must be a numeric Telegram user ID")

    if not str(PAYHERO_CHANNEL_ID).isdigit():
        raise RuntimeError("PAYHERO_CHANNEL_ID must be numeric")


def utcnow():
    return datetime.now(timezone.utc)


def format_eat(dt_or_iso):
    if isinstance(dt_or_iso, str):
        dt = datetime.fromisoformat(dt_or_iso)
    else:
        dt = dt_or_iso
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(EAT).strftime("%d %b %Y, %I:%M %p EAT")


@contextmanager
def connect_db():
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


def ensure_column(con, table, column, definition):
    cols = [row["name"] for row in con.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


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
                mpesa_receipt TEXT,
                created_at TEXT NOT NULL
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id INTEGER PRIMARY KEY,
                plan TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                status TEXT NOT NULL,
                reminded INTEGER NOT NULL DEFAULT 0
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS custom_buttons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                url TEXT NOT NULL
            )
        """)

        # Safe migrations for existing databases
        ensure_column(con, "orders", "mpesa_receipt", "TEXT")
        ensure_column(con, "subscriptions", "reminded", "INTEGER NOT NULL DEFAULT 0")

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
        plans = con.execute("SELECT name,price FROM plans ORDER BY price").fetchall()

    rows = []
    for plan in plans:
        name = plan["name"]
        price = plan["price"]
        rows.append([{
            "text": f"👑 {name.title()} VIP — KES {price}",
            "callback_data": f"plan:{name}",
            "style": "primary",
        }])
    return {"inline_keyboard": rows}


def admin_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "💰 Prices", "callback_data": "admin:prices"},
                {"text": "📊 Statistics", "callback_data": "admin:stats"},
            ],
            [
                {"text": "👥 VIP Members", "callback_data": "admin:members"},
                {"text": "🧾 Payments", "callback_data": "admin:payments"},
            ],
            [
                {
                    "text": "📢 Publish VIP Advert",
                    "callback_data": "admin:post",
                    "style": "success",
                }
            ],
            [
                {"text": "🔘 Button Commands", "callback_data": "admin:buttons"},
            ],
        ]
    }


# --------------------------------------------------
# PAYMENT HELPERS
# --------------------------------------------------

def payhero_headers():
    token = PAYHERO_AUTH_TOKEN.strip()
    authorization = token if token.lower().startswith("basic ") else f"Basic {token}"
    return {
        "Authorization": authorization,
        "Content-Type": "application/json",
    }


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
    with connect_db() as con:
        return con.execute("SELECT * FROM plans WHERE name=?", (plan_name,)).fetchone()


def mark_order_failed(external_ref):
    with connect_db() as con:
        con.execute(
            "UPDATE orders SET status='FAILED' WHERE external_ref=? AND status='PENDING'",
            (external_ref,)
        )


def recent_stk_cooldown_remaining(user_id):
    with connect_db() as con:
        last = con.execute(
            "SELECT created_at FROM orders WHERE user_id=? ORDER BY created_at DESC LIMIT 1",
            (user_id,)
        ).fetchone()
    if not last:
        return 0
    elapsed = (utcnow() - datetime.fromisoformat(last["created_at"])).total_seconds()
    remaining = int(STK_COOLDOWN_SECONDS - elapsed)
    return max(0, remaining)


async def start_payment(user_id, plan_name, phone):
    plan = get_plan(plan_name)
    if not plan:
        await send_message(user_id, "⚠️ That VIP plan is unavailable.")
        return

    wait_sec = recent_stk_cooldown_remaining(user_id)
    if wait_sec > 0:
        await send_message(
            user_id,
            f"⏳ Please wait <b>{wait_sec} seconds</b> before requesting another M-Pesa prompt."
        )
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
            utcnow().isoformat(),
        ))

    payload = {
        "amount": amount,
        "phone_number": phone,
        "channel_id": int(PAYHERO_CHANNEL_ID),
        "provider": "m-pesa",
        "external_reference": external_ref,
        "callback_url": PUBLIC_URL.rstrip("/") + "/payhero/callback",
    }

    try:
        async with httpx.AsyncClient(timeout=40) as client:
            response = await client.post(
                PAYHERO_URL + "/payments",
                headers=payhero_headers(),
                json=payload,
            )

        result = response.json()

        if response.status_code not in (200, 201) or not result.get("success"):
            log.error("PayHero payment error (%s): %s", response.status_code, result)
            mark_order_failed(external_ref)
            await send_message(
                user_id,
                "❌ <b>Payment request could not be started.</b>\n\n"
                "Please try again in a minute or contact the administrator."
            )
            return

        reference = result.get("reference")
        checkout_id = result.get("CheckoutRequestID")

        if not reference:
            mark_order_failed(external_ref)
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
            "✅ Your VIP link will arrive automatically—or tap <b>Verify Payment</b> "
            "below once you receive the M-Pesa SMS.",
            {
                "inline_keyboard": [[{
                    "text": "🔄 I Have Paid — Verify Payment",
                    "callback_data": f"verify:{external_ref}",
                    "style": "success",
                }]]
            }
        )

    except Exception:
        log.exception("Payment initiation failed")
        mark_order_failed(external_ref)
        await send_message(
            user_id,
            "⚠️ We could not start your payment.\n\n"
            "Please try again or contact the administrator."
        )


async def verify_payhero_transaction(reference):
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            PAYHERO_URL + "/transaction-status",
            headers=payhero_headers(),
            params={"reference": reference},
        )
    response.raise_for_status()
    return response.json()


# --------------------------------------------------
# VIP MEMBERSHIP
# --------------------------------------------------

async def give_vip_access(user_id, plan_name, days_override=None, receipt=None):
    if days_override is not None:
        days = int(days_override)
        display_plan = plan_name or f"{days}-day"
    else:
        plan = get_plan(plan_name)
        if not plan:
            return
        days = int(plan["days"])
        display_plan = plan_name

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

    expiry = start + timedelta(days=days)

    with connect_db() as con:
        con.execute("""
            INSERT INTO subscriptions(user_id,plan,expires_at,status,reminded)
            VALUES(?,?,?,'ACTIVE',0)
            ON CONFLICT(user_id) DO UPDATE SET
                plan=excluded.plan,
                expires_at=excluded.expires_at,
                status='ACTIVE',
                reminded=0
        """, (
            user_id,
            display_plan,
            expiry.isoformat(),
        ))

    # Ensure a previously expired member is not still banned
    try:
        await telegram("unbanChatMember", {
            "chat_id": VIP_CHANNEL_ID,
            "user_id": user_id,
            "only_if_banned": True,
        })
    except Exception:
        log.warning("Pre-invite unban skipped/failed for user %s", user_id)

    try:
        invite = await telegram("createChatInviteLink", {
            "chat_id": VIP_CHANNEL_ID,
            "expire_date": int(expiry.timestamp()),
            "member_limit": 1,
            "name": f"VIP {user_id}",
        })

        receipt_line = f"🧾 Receipt: <code>{html_escape(receipt)}</code>\n" if receipt else ""

        await send_message(
            user_id,
            "🎉 <b>VIP ACCESS ACTIVATED!</b>\n\n"
            "👑 <b>Welcome to JOHN TIPSTER VIP</b>\n\n"
            f"💼 Plan: <b>{html_escape(display_plan.title())} VIP</b>\n"
            f"{receipt_line}"
            f"📅 Expires: <b>{format_eat(expiry)}</b>\n\n"
            "👇 Tap your private single-use link below to join:",
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
            f"VIP activated for user <code>{user_id}</code>, "
            "but the bot could not create an invite link.\n"
            "Check bot admin permissions in the VIP channel."
        )
        await send_message(
            user_id,
            "✅ Your VIP membership is active, but the invite link failed.\n\n"
            "Please contact the administrator."
        )


async def finalize_paid_order(order, verified, callback_data=None):
    """
    Shared by both PayHero callback and manual 'Verify Payment' button.
    Returns: 'PAID', 'ALREADY_PAID', 'FAILED', or 'PENDING'
    """
    external_ref = order["external_ref"]
    status = str(verified.get("status", "")).upper()

    if status == "FAILED":
        with connect_db() as con:
            cur = con.execute(
                "UPDATE orders SET status='FAILED' WHERE external_ref=? AND status='PENDING'",
                (external_ref,)
            )
            changed = cur.rowcount

        if changed > 0:
            try:
                await send_message(
                    int(order["user_id"]),
                    "❌ <b>Your M-Pesa payment was cancelled or failed.</b>\n\n"
                    "Send /start whenever you are ready to try again."
                )
            except Exception:
                log.exception("Could not notify user %s of failed payment", order["user_id"])
        return "FAILED"

    if status != "SUCCESS":
        return "PENDING"

    if callback_data:
        callback_amount = callback_data.get("Amount")
        if callback_amount is not None:
            try:
                if int(float(callback_amount)) != int(order["amount"]):
                    log.error("Payment amount mismatch for %s", external_ref)
                    return "PENDING"
            except (ValueError, TypeError):
                log.error("Invalid callback amount for %s", external_ref)
                return "PENDING"

    receipt = (
        (callback_data or {}).get("MpesaReceiptNumber")
        or verified.get("provider_reference")
        or verified.get("mpesa_receipt")
    )

    # Atomic lock: only the single request that flips PENDING -> PAID grants VIP
    with connect_db() as con:
        cur = con.execute(
            """
            UPDATE orders
            SET status='PAID', mpesa_receipt=COALESCE(?, mpesa_receipt)
            WHERE external_ref=? AND status='PENDING'
            """,
            (receipt, external_ref)
        )
        updated = cur.rowcount

    if updated == 0:
        return "ALREADY_PAID"

    await give_vip_access(
        int(order["user_id"]),
        order["plan"],
        receipt=receipt,
    )

    receipt_line = f"🧾 M-Pesa: <code>{html_escape(receipt)}</code>\n" if receipt else ""
    await send_message(
        ADMIN_USER_ID,
        "✅ <b>NEW VIP PAYMENT</b>\n\n"
        f"🆔 Customer: <code>{order['user_id']}</code>\n"
        f"💼 Plan: <b>{html_escape(order['plan'].title())}</b>\n"
        f"💰 Amount: <b>KES {order['amount']}</b>\n"
        f"{receipt_line}"
        f"🔗 Ref: <code>{html_escape(external_ref)}</code>"
    )
    return "PAID"


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
        log.warning("Order %s has no PayHero transaction reference", external_ref)
        return

    verified = await verify_payhero_transaction(reference)
    await finalize_paid_order(order, verified, callback_data=data)


# --------------------------------------------------
# VIP PROMOTIONAL POST
# --------------------------------------------------

async def publish_vip_post():
    global bot_username
    if not bot_username:
        me = await telegram("getMe", {})
        bot_username = me["username"]

    join_url = f"https://t.me/{bot_username}?start=vip"

    keyboard = {
        "inline_keyboard": [[{
            "text": "👑 JOIN VIP NOW",
            "url": join_url,
            "style": "primary",
        }]]
    }

    with connect_db() as con:
        buttons = con.execute("SELECT * FROM custom_buttons ORDER BY id").fetchall()

    for button in buttons:
        keyboard["inline_keyboard"].append([{
            "text": button["label"],
            "url": button["url"],
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
        "reply_markup": keyboard,
    })

    try:
        await telegram("pinChatMessage", {
            "chat_id": CHANNEL_ID,
            "message_id": message["message_id"],
            "disable_notification": True,
        })
    except Exception as e:
        log.error("Pinning failed: %s", e)

    return message


# --------------------------------------------------
# ADMIN REPORTS
# --------------------------------------------------

async def show_prices(chat_id):
    with connect_db() as con:
        plans = con.execute("SELECT * FROM plans ORDER BY price").fetchall()

    text = "💰 <b>CURRENT VIP PRICES</b>\n\n"
    for p in plans:
        text += f"• <b>{html_escape(p['name'].title())}</b> — KES {p['price']}\n"

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
            f"📅 Expires: {format_eat(m['expires_at'])}\n\n"
        )

    await send_message(chat_id, text)


async def show_payments(chat_id):
    with connect_db() as con:
        payments = con.execute("""
            SELECT * FROM orders
            ORDER BY created_at DESC
            LIMIT 15
        """).fetchall()

    if not payments:
        await send_message(chat_id, "ℹ️ No payments recorded yet.")
        return

    text = "🧾 <b>RECENT PAYMENTS</b>\n\n"
    for p in payments:
        receipt_line = f"🧾 M-Pesa: <code>{html_escape(p['mpesa_receipt'])}</code>\n" if p["mpesa_receipt"] else ""
        text += (
            f"💼 <b>{html_escape(p['plan'].title())}</b> — KES {p['amount']}\n"
            f"📌 Status: <b>{html_escape(p['status'])}</b>\n"
            f"🆔 Customer: <code>{p['user_id']}</code>\n"
            f"{receipt_line}"
            f"🔗 Ref: <code>{html_escape(p['external_ref'])}</code>\n\n"
        )

    await send_message(chat_id, text)


# --------------------------------------------------
# COMMAND HANDLING
# --------------------------------------------------

HELP_TEXT = (
    "ℹ️ <b>JOHN TIPSTER VIP — HELP</b>\n\n"
    "▶️ /start — View VIP plans & subscribe\n"
    "📅 /status — Check your VIP expiry\n"
    "🛑 /cancel — Cancel a pending payment\n\n"
    "<i>Admin commands:</i>\n"
    "🔐 /admin — Open the admin panel\n"
    "💰 /setprice &lt;plan&gt; &lt;amount&gt;\n"
    "🎁 /grant &lt;user_id&gt; &lt;days&gt;\n"
    "🚫 /revoke &lt;user_id&gt;\n"
    "➕ /addbutton Label|https://url\n"
    "🗑 /removebuttons\n"
    "📢 /postvip\n"
    "📊 /stats · 👥 /members · 🧾 /payments"
)


async def handle_message(message):
    user = message.get("from", {})
    user_id = user.get("id")
    chat_id = message.get("chat", {}).get("id")
    text = (message.get("text") or "").strip()

    if not user_id or not chat_id:
        return

    if text.startswith("/"):
        cmd = text.split()[0].split("@")[0].lower()

        if cmd == "/start":
            await send_message(
                chat_id,
                "👑 <b>WELCOME TO JOHN TIPSTER VIP!</b>\n\n"
                "🌟 <i>Premium football predictions, delivered privately.</i>\n\n"
                "Choose your membership plan below 👇\n\n"
                "🔐 After payment is confirmed, your VIP invitation is sent "
                "to you automatically.",
                plans_keyboard()
            )
            return

        if cmd == "/help":
            await send_message(chat_id, HELP_TEXT)
            return

        if cmd == "/status":
            with connect_db() as con:
                sub = con.execute(
                    "SELECT * FROM subscriptions WHERE user_id=?",
                    (user_id,)
                ).fetchone()

            if sub and sub["status"] == "ACTIVE":
                await send_message(
                    chat_id,
                    "👑 <b>YOUR VIP STATUS: ACTIVE</b>\n\n"
                    f"💼 Plan: <b>{html_escape(sub['plan'].title())} VIP</b>\n"
                    f"📅 Expires: <b>{format_eat(sub['expires_at'])}</b>"
                )
            else:
                await send_message(
                    chat_id,
                    "ℹ️ You do not have an active VIP subscription.\n\n"
                    "Choose a plan below to join 👇",
                    plans_keyboard()
                )
            return

        if cmd == "/cancel":
            with connect_db() as con:
                con.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            await send_message(chat_id, "🛑 Payment process cancelled.")
            return

        if cmd == "/admin":
            if not is_admin(user_id):
                await send_message(chat_id, "⛔ You are not authorized.")
                return
            await send_message(
                chat_id,
                "👑 <b>JOHN TIPSTER ADMIN PANEL</b>\n\n"
                "Choose an action below.\n\n"
                "<i>Manual commands:</i> <code>/grant</code>, <code>/revoke</code>, <code>/setprice</code>",
                admin_keyboard()
            )
            return

        if cmd == "/setprice":
            if not is_admin(user_id):
                await send_message(chat_id, "⛔ You are not authorized.")
                return

            parts = text.split()
            if len(parts) != 3:
                await send_message(
                    chat_id,
                    "Usage: <code>/setprice daily 100</code>\n"
                    "Available plans: daily, weekly, monthly"
                )
                return

            plan_name = parts[1].lower()
            try:
                price = int(parts[2])
            except ValueError:
                await send_message(chat_id, "⚠️ Enter a whole-number KES price.")
                return

            if plan_name not in ("daily", "weekly", "monthly") or price < 1:
                await send_message(chat_id, "⚠️ Invalid plan or price.")
                return

            with connect_db() as con:
                con.execute("UPDATE plans SET price=? WHERE name=?", (price, plan_name))

            await send_message(
                chat_id,
                f"✅ <b>{html_escape(plan_name.title())} VIP</b> price updated to "
                f"<b>KES {price}</b>."
            )
            return

        if cmd == "/grant":
            if not is_admin(user_id):
                await send_message(chat_id, "⛔ You are not authorized.")
                return

            parts = text.split()
            if len(parts) != 3:
                await send_message(
                    chat_id,
                    "Usage: <code>/grant &lt;telegram_user_id&gt; &lt;days&gt;</code>\n"
                    "Example: <code>/grant 123456789 7</code>"
                )
                return

            try:
                target_id = int(parts[1])
                days = int(parts[2])
                if target_id <= 0 or not (1 <= days <= 3650):
                    raise ValueError
            except ValueError:
                await send_message(chat_id, "⚠️ Invalid user ID or number of days (1–3650).")
                return

            await give_vip_access(target_id, f"{days}-day", days_override=days)
            await send_message(
                chat_id,
                f"✅ Granted <b>{days} day(s)</b> of VIP access to <code>{target_id}</code>."
            )
            return

        if cmd == "/revoke":
            if not is_admin(user_id):
                await send_message(chat_id, "⛔ You are not authorized.")
                return

            parts = text.split()
            if len(parts) != 2:
                await send_message(chat_id, "Usage: <code>/revoke &lt;telegram_user_id&gt;</code>")
                return

            try:
                target_id = int(parts[1])
            except ValueError:
                await send_message(chat_id, "⚠️ Invalid Telegram user ID.")
                return

            try:
                await telegram("banChatMember", {
                    "chat_id": VIP_CHANNEL_ID,
                    "user_id": target_id,
                })
                await telegram("unbanChatMember", {
                    "chat_id": VIP_CHANNEL_ID,
                    "user_id": target_id,
                    "only_if_banned": True,
                })
            except Exception as e:
                log.warning("Channel removal failed during revoke for %s: %s", target_id, e)

            with connect_db() as con:
                con.execute(
                    "UPDATE subscriptions SET status='EXPIRED' WHERE user_id=?",
                    (target_id,)
                )

            await send_message(chat_id, f"🚫 VIP access revoked for <code>{target_id}</code>.")
            return

        if cmd == "/addbutton":
            if not is_admin(user_id):
                await send_message(chat_id, "⛔ You are not authorized.")
                return

            value = text.split(None, 1)[1].strip() if len(text.split(None, 1)) > 1 else ""
            if "|" not in value:
                await send_message(
                    chat_id,
                    "Usage:\n<code>/addbutton Website|https://example.com</code>"
                )
                return

            label, url = [x.strip() for x in value.split("|", 1)]
            if not label or not url.startswith("https://"):
                await send_message(
                    chat_id,
                    "⚠️ Please provide a button label and a valid HTTPS URL."
                )
                return

            with connect_db() as con:
                con.execute(
                    "INSERT INTO custom_buttons(label,url) VALUES(?,?)",
                    (label[:50], url)
                )

            await send_message(
                chat_id,
                "✅ Button added.\n\nUse /postvip to publish the updated advert."
            )
            return

        if cmd == "/removebuttons":
            if not is_admin(user_id):
                await send_message(chat_id, "⛔ You are not authorized.")
                return
            with connect_db() as con:
                con.execute("DELETE FROM custom_buttons")
            await send_message(chat_id, "🗑 All custom buttons removed.")
            return

        if cmd == "/postvip":
            if not is_admin(user_id):
                await send_message(chat_id, "⛔ You are not authorized.")
                return
            try:
                await publish_vip_post()
                await send_message(chat_id, "✅ VIP advert published to your channel.")
            except Exception as e:
                log.exception("Could not publish VIP post")
                await send_message(
                    chat_id,
                    f"⚠️ <b>Could not publish.</b>\n\n"
                    f"<b>Error Details:</b>\n<code>{html_escape(str(e))}</code>\n\n"
                    "Check that the bot is an admin with permission to post in your channel."
                )
            return

        if cmd == "/stats":
            if is_admin(user_id):
                await show_stats(chat_id)
            else:
                await send_message(chat_id, "⛔ You are not authorized.")
            return

        if cmd == "/members":
            if is_admin(user_id):
                await show_members(chat_id)
            else:
                await send_message(chat_id, "⛔ You are not authorized.")
            return

        if cmd == "/payments":
            if is_admin(user_id):
                await show_payments(chat_id)
            else:
                await send_message(chat_id, "⛔ You are not authorized.")
            return

        await send_message(
            chat_id,
            "❓ Unknown command.\n\nUse /start to see VIP plans or /help for help."
        )
        return

    # ---- Non-command text: M-Pesa phone entry if a session exists ----
    with connect_db() as con:
        session = con.execute(
            "SELECT * FROM sessions WHERE user_id=?",
            (user_id,)
        ).fetchone()

    if session:
        phone = normalize_phone(text)
        if not phone:
            await send_message(
                chat_id,
                "⚠️ Enter a valid Kenyan number.\n\n"
                "Example: <code>0712345678</code>\n\n"
                "Send /cancel to stop."
            )
            return

        with connect_db() as con:
            con.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))

        await start_payment(user_id, session["plan"], phone)
        return

    await send_message(
        chat_id,
        "👋 Send /start to see VIP plans, /status to check your VIP, or /help for instructions."
    )


# --------------------------------------------------
# BUTTON HANDLING
# --------------------------------------------------

async def handle_callback(query):
    callback_id = query["id"]
    user_id = query["from"]["id"]
    data = query.get("data", "")
    chat_id = query.get("message", {}).get("chat", {}).get("id") or user_id

    if data.startswith("plan:"):
        plan_name = data.split(":", 1)[1]
        if not get_plan(plan_name):
            await answer_callback(callback_id, "Plan unavailable")
            return

        with connect_db() as con:
            con.execute("""
                INSERT INTO sessions(user_id,plan)
                VALUES(?,?)
                ON CONFLICT(user_id) DO UPDATE SET plan=excluded.plan
            """, (user_id, plan_name))

        await answer_callback(callback_id, "Plan selected")
        await send_message(
            user_id,
            f"✅ <b>Plan selected:</b> {html_escape(plan_name.title())} VIP\n\n"
            "📱 Enter your <b>Kenyan M-Pesa phone number</b>.\n\n"
            "Example: <code>0712345678</code>\n\n"
            "Send /cancel to stop."
        )
        return

    if data.startswith("verify:"):
        external_ref = data.split(":", 1)[1]

        with connect_db() as con:
            order = con.execute(
                "SELECT * FROM orders WHERE external_ref=? AND user_id=?",
                (external_ref, user_id)
            ).fetchone()

        if not order:
            await answer_callback(callback_id, "Payment reference not found")
            return

        if order["status"] == "PAID":
            await answer_callback(callback_id, "Already confirmed!")
            await send_message(user_id, "✅ This payment is already confirmed. Check your VIP link above or use /status.")
            return

        if order["status"] == "FAILED":
            await answer_callback(callback_id, "Payment failed")
            await send_message(user_id, "❌ That payment failed or was cancelled. Send /start to try again.")
            return

        reference = order["payhero_reference"]
        if not reference:
            await answer_callback(callback_id, "Still processing...")
            return

        await answer_callback(callback_id, "Checking with M-Pesa...")

        try:
            verified = await verify_payhero_transaction(reference)
            outcome = await finalize_paid_order(order, verified)

            if outcome == "PENDING":
                await send_message(
                    user_id,
                    "⏳ <b>Payment is still pending.</b>\n\n"
                    "If you already entered your M-Pesa PIN, wait 10–15 seconds and tap "
                    "<b>Verify Payment</b> again."
                )
            elif outcome == "ALREADY_PAID":
                await send_message(user_id, "✅ Payment confirmed! Check your VIP invitation link above.")
        except Exception:
            log.exception("Manual verification failed for %s", external_ref)
            await send_message(
                user_id,
                "⚠️ Could not check payment status right now. Please try again in a few seconds."
            )
        return

    if data.startswith("admin:"):
        if not is_admin(user_id):
            await answer_callback(callback_id, "Not authorized")
            return

        action = data.split(":", 1)[1]
        await answer_callback(callback_id)

        if action == "prices":
            await show_prices(chat_id)
        elif action == "stats":
            await show_stats(chat_id)
        elif action == "members":
            await show_members(chat_id)
        elif action == "payments":
            await show_payments(chat_id)
        elif action == "post":
            try:
                await publish_vip_post()
                await send_message(chat_id, "✅ VIP advert published.")
            except Exception as e:
                log.exception("Admin post failed")
                await send_message(
                    chat_id,
                    f"⚠️ <b>Could not publish the VIP advert.</b>\n\n"
                    f"<b>Error Details:</b>\n<code>{html_escape(str(e))}</code>"
                )
        elif action == "buttons":
            await send_message(
                chat_id,
                "🔘 <b>BUTTON MANAGEMENT</b>\n\n"
                "<b>Add a button:</b>\n"
                "<code>/addbutton Website|https://example.com</code>\n\n"
                "<b>Remove all custom buttons:</b>\n"
                "<code>/removebuttons</code>\n\n"
                "<b>Publish updated buttons:</b>\n"
                "<code>/postvip</code>"
            )
        return


# --------------------------------------------------
# AUTOMATIC EXPIRY & REMINDERS
# --------------------------------------------------

async def expire_members():
    now = utcnow()
    now_iso = now.isoformat()
    remind_cutoff = (now + timedelta(hours=12)).isoformat()

    # 1. Send 12-hour pre-expiry renewal reminders
    with connect_db() as con:
        to_remind = con.execute("""
            SELECT * FROM subscriptions
            WHERE status='ACTIVE'
              AND reminded=0
              AND expires_at > ?
              AND expires_at <= ?
        """, (now_iso, remind_cutoff)).fetchall()

    for member in to_remind:
        user_id = member["user_id"]
        with connect_db() as con:
            con.execute("UPDATE subscriptions SET reminded=1 WHERE user_id=?", (user_id,))
        try:
            await send_message(
                user_id,
                "⏳ <b>VIP EXPIRY REMINDER</b>\n\n"
                f"Your <b>{html_escape(member['plan'].title())} VIP</b> expires on "
                f"<b>{format_eat(member['expires_at'])}</b>.\n\n"
                "👇 Tap a plan below to renew now without losing access:",
                plans_keyboard()
            )
        except Exception:
            log.exception("Could not send expiry reminder to %s", user_id)

    # 2. Remove expired members
    with connect_db() as con:
        expired = con.execute("""
            SELECT * FROM subscriptions
            WHERE status='ACTIVE' AND expires_at <= ?
        """, (now_iso,)).fetchall()

    for member in expired:
        user_id = member["user_id"]

        try:
            await telegram("banChatMember", {
                "chat_id": VIP_CHANNEL_ID,
                "user_id": user_id,
            })
            await telegram("unbanChatMember", {
                "chat_id": VIP_CHANNEL_ID,
                "user_id": user_id,
                "only_if_banned": True,
            })
        except Exception:
            log.exception("Could not remove expired user %s from VIP channel", user_id)

        with connect_db() as con:
            con.execute(
                "UPDATE subscriptions SET status='EXPIRED' WHERE user_id=?",
                (user_id,)
            )

        try:
            await send_message(
                user_id,
                "⏰ <b>Your JOHN TIPSTER VIP subscription has expired.</b>\n\n"
                "Choose a plan below to renew and rejoin immediately 👇",
                plans_keyboard()
            )
        except Exception:
            log.exception("Could not notify expired user %s", user_id)

        log.info("Expired VIP member %s", user_id)


async def expiry_worker():
    while True:
        try:
            await expire_members()
        except Exception:
            log.exception("Expiry check failed")
        await asyncio.sleep(60)


# --------------------------------------------------
# FASTAPI LIFESPAN & WEBHOOKS
# --------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_username

    check_settings()
    init_db()

    me = await telegram("getMe", {})
    bot_username = me["username"]

    await telegram("setWebhook", {
        "url": PUBLIC_URL.rstrip("/") + "/telegram",
        "secret_token": WEBHOOK_SECRET,
        "allowed_updates": ["message", "callback_query"],
    })

    expiry_task = asyncio.create_task(expiry_worker())
    log.info("John Tipster VIP bot started as @%s", bot_username)

    try:
        yield
    finally:
        expiry_task.cancel()
        try:
            await expiry_task
        except asyncio.CancelledError:
            pass


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def home():
    return {"status": "John Tipster VIP Bot is running"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


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
        log.exception("PayHero callback processing failed")
        raise HTTPException(status_code=503, detail="Payment verification failed")

    return {"ok": True}

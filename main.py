
import os
import re
import json
import time
import asyncio
import secrets
import sqlite3
import logging
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
        "RENDER_EXTERNAL_URL": PUBLIC_URL,
    }

    missing = [k for k, v in settings.items() if not v]

    if missing:
        raise RuntimeError(
            "Missing environment variables: " + ", ".join(missing)
        )

    if not ADMIN_USER_ID.isdigit():
        raise RuntimeError("ADMIN_USER_ID must be a numeric Telegram user ID")

    if not PAYHERO_CHANNEL_ID.isdigit():
        raise RuntimeError("PAYHERO_CHANNEL_ID must be numeric")


def utcnow():
    return datetime.now(timezone.utc)


def connect_db():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return con


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
            f"Telegram {method} failed: {result}"
        )

    return result.get("result")


async def send_message(chat_id, text, keyboard=None):
    data = {
        "chat_id": chat_id,
        "text": text,
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
            "text": f"👑 {name.title()} VIP - KES {price}",
            "callback_data": f"plan:{name}"
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
                {"text": "📢 Publish VIP Advert", "callback_data": "admin:post"}
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
        await send_message(user_id, "That VIP plan is unavailable.")
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
                "Payment request could not be started. Please try again later."
            )
            return

        if not result.get("success"):
            raise RuntimeError(str(result))

        reference = result.get("reference")
        checkout_id = result.get("CheckoutRequestID")

        if not reference:
            raise RuntimeError("PayHero did not return a transaction reference")

        with connect_db() as con:
            con.execute("""
                UPDATE orders
                SET payhero_reference=?, checkout_id=?
                WHERE external_ref=?
            """, (reference, checkout_id, external_ref))

        await send_message(
            user_id,
            "📲 M-Pesa STK Push sent!\n\n"
            f"Plan: {plan_name.title()} VIP\n"
            f"Amount: KES {amount}\n"
            f"Phone: {phone}\n\n"
            "Enter your M-Pesa PIN on your phone to complete payment.\n"
            "Your VIP access will be sent after payment is verified."
        )

    except Exception:
        log.exception("Payment initiation failed")

        await send_message(
            user_id,
            "We could not start your payment. Please contact the administrator."
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
            "🎉 PAYMENT CONFIRMED!\n\n"
            "Welcome to JOHN TIPSTER VIP.\n\n"
            f"Your plan: {plan_name.title()} VIP\n"
            f"Expires: {expiry.strftime('%d %b %Y %H:%M UTC')}\n\n"
            "Tap your private invitation link below to join:",
            {
                "inline_keyboard": [[{
                    "text": "👑 JOIN VIP CHANNEL",
                    "url": invite["invite_link"]
                }]]
            }
        )

    except Exception:
        log.exception("Could not create or send VIP invitation")

        await send_message(
            ADMIN_USER_ID,
            f"Payment confirmed for Telegram user {user_id}, "
            "but VIP invitation creation failed. Please check bot permissions."
        )

        await send_message(
            user_id,
            "Your payment is confirmed. Please contact the administrator "
            "for your VIP invitation."
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
        "✅ NEW VIP PAYMENT\n\n"
        f"Customer Telegram ID: {order['user_id']}\n"
        f"Plan: {order['plan']}\n"
        f"Amount: KES {order['amount']}\n"
        f"Reference: {external_ref}"
    )


# --------------------------------------------------
# VIP PROMOTIONAL POST
# --------------------------------------------------

async def publish_vip_post():
    me = await telegram("getMe", {})
    username = me["username"]

    join_url = f"https://t.me/{username}?start=vip"

    keyboard = {
        "inline_keyboard": [[{
            "text": "👑 JOIN VIP NOW",
            "url": join_url
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
        "text": (
            "👑 JOHN TIPSTER VIP PREDICTIONS\n\n"
            "Want access to our premium football predictions?\n\n"
            "Choose your VIP membership plan and pay securely "
            "through M-Pesa.\n\n"
            "Your membership invitation is sent automatically "
            "after successful payment.\n\n"
            "👇 TAP BELOW TO JOIN VIP"
        ),
        "reply_markup": keyboard
    })

    try:
        await telegram("pinChatMessage", {
            "chat_id": CHANNEL_ID,
            "message_id": message["message_id"],
            "disable_notification": True
        })
    except Exception:
        log.warning("VIP advert posted but could not be pinned")

    return message


# --------------------------------------------------
# ADMIN REPORTS
# --------------------------------------------------

async def show_prices(chat_id):
    with connect_db() as con:
        plans = con.execute(
            "SELECT * FROM plans ORDER BY price"
        ).fetchall()

    text = "💰 CURRENT VIP PRICES\n\n"

    for p in plans:
        text += f"{p['name'].title()}: KES {p['price']}\n"

    text += "\nChange prices with:\n"
    text += "/setprice daily 100\n"
    text += "/setprice weekly 300\n"
    text += "/setprice monthly 1000"

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
        "📊 JOHN TIPSTER VIP STATISTICS\n\n"
        f"Active VIP members: {active}\n"
        f"Successful payments: {paid}\n"
        f"Pending payments: {pending}\n"
        f"Recorded revenue: KES {revenue}"
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
        await send_message(chat_id, "No active VIP members yet.")
        return

    text = "👥 ACTIVE VIP MEMBERS\n\n"

    for m in members:
        text += (
            f"ID: {m['user_id']}\n"
            f"Plan: {m['plan']}\n"
            f"Expires: {m['expires_at']}\n\n"
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
        await send_message(chat_id, "No payments recorded yet.")
        return

    text = "🧾 RECENT PAYMENTS\n\n"

    for p in payments:
        text += (
            f"{p['plan'].title()} - KES {p['amount']}\n"
            f"Status: {p['status']}\n"
            f"Customer: {p['user_id']}\n"
            f"Ref: {p['external_ref']}\n\n"
        )

    await send_message(chat_id, text)


# --------------------------------------------------
# COMMAND HANDLING
# --------------------------------------------------

async def handle_message(message):
    user = message.get("from", {})
    user_id = user.get("id")
    chat_id = message.get("chat", {}).get("id")
    text = (message.get("text") or "").strip()

    if not user_id or not chat_id:
        return

    if text.startswith("/start"):
        await send_message(
            chat_id,
            "👑 WELCOME TO JOHN TIPSTER VIP!\n\n"
            "Choose your membership plan below.\n"
            "After payment is confirmed, your VIP invitation "
            "will be sent automatically.",
            plans_keyboard()
        )
        return

    if text.startswith("/cancel"):
        with connect_db() as con:
            con.execute(
                "DELETE FROM sessions WHERE user_id=?",
                (user_id,)
            )

        await send_message(chat_id, "Payment process cancelled.")
        return

    if text.startswith("/admin"):
        if not is_admin(user_id):
            await send_message(chat_id, "You are not authorized.")
            return

        await send_message(
            chat_id,
            "👑 JOHN TIPSTER ADMIN PANEL\n\n"
            "Choose an action below.\n\n"
            "You can also use commands such as /setprice.",
            admin_keyboard()
        )
        return

    if text.startswith("/setprice"):
        if not is_admin(user_id):
            await send_message(chat_id, "You are not authorized.")
            return

        parts = text.split()

        if len(parts) != 3:
            await send_message(
                chat_id,
                "Usage: /setprice daily 100\n"
                "Available plans: daily, weekly, monthly"
            )
            return

        plan_name = parts[1].lower()

        try:
            price = int(parts[2])
        except ValueError:
            await send_message(chat_id, "Enter a whole-number KES price.")
            return

        if plan_name not in ("daily", "weekly", "monthly") or price < 1:
            await send_message(chat_id, "Invalid plan or price.")
            return

        with connect_db() as con:
            con.execute(
                "UPDATE plans SET price=? WHERE name=?",
                (price, plan_name)
            )

        await send_message(
            chat_id,
            f"✅ {plan_name.title()} VIP price updated to KES {price}."
        )
        return

    if text.startswith("/addbutton"):
        if not is_admin(user_id):
            await send_message(chat_id, "You are not authorized.")
            return

        value = text[len("/addbutton"):].strip()

        if "|" not in value:
            await send_message(
                chat_id,
                "Usage:\n/addbutton Website|https://example.com"
            )
            return

        label, url = [x.strip() for x in value.split("|", 1)]

        if not label or not url.startswith("https://"):
            await send_message(
                chat_id,
                "Please provide a button label and a valid HTTPS URL."
            )
            return

        with connect_db() as con:
            con.execute(
                "INSERT INTO custom_buttons(label,url) VALUES(?,?)",
                (label[:50], url)
            )

        await send_message(
            chat_id,
            "✅ Button added. Use /postvip to publish the updated advert."
        )
        return

    if text.startswith("/removebuttons"):
        if not is_admin(user_id):
            await send_message(chat_id, "You are not authorized.")
            return

        with connect_db() as con:
            con.execute("DELETE FROM custom_buttons")

        await send_message(chat_id, "All custom buttons removed.")
        return

    if text.startswith("/postvip"):
        if not is_admin(user_id):
            await send_message(chat_id, "You are not authorized.")
            return

        try:
            await publish_vip_post()
            await send_message(chat_id, "✅ VIP advert published to your channel.")
        except Exception:
            log.exception("Could not publish VIP post")
            await send_message(
                chat_id,
                "Could not publish. Check that the bot is an admin "
                "with permission to post in your channel."
            )
        return

    if text.startswith("/stats"):
        if is_admin(user_id):
            await show_stats(chat_id)
        else:
            await send_message(chat_id, "You are not authorized.")
        return

    if text.startswith("/members"):
        if is_admin(user_id):
            await show_members(chat_id)
        else:
            await send_message(chat_id, "You are not authorized.")
        return

    if text.startswith("/payments"):
        if is_admin(user_id):
            await show_payments(chat_id)
        else:
            await send_message(chat_id, "You are not authorized.")
        return

    # If the user is entering a phone number for payment:
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
                "Enter a valid Kenyan number, for example 0712345678."
            )
            return

        with connect_db() as con:
            con.execute(
                "DELETE FROM sessions WHERE user_id=?",
                (user_id,)
            )

        await start_payment(user_id, session["plan"], phone)
        return

    if text.startswith("/"):
        await send_message(
            chat_id,
            "Use /start to see VIP plans or /help for instructions."
        )


# --------------------------------------------------
# BUTTON HANDLING
# --------------------------------------------------

async def handle_callback(query):
    callback_id = query["id"]
    user_id = query["from"]["id"]
    data = query.get("data", "")
    chat_id = query.get("message", {}).get("chat", {}).get("id")

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
            f"You selected {plan_name.title()} VIP.\n\n"
            "Enter your Kenyan M-Pesa phone number.\n"
            "Example: 0712345678\n\n"
            "Send /cancel to stop."
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
                await send_message(chat_id, "VIP advert published.")
            except Exception:
                log.exception("Admin post failed")
                await send_message(chat_id, "Could not publish the VIP advert.")

        elif action == "buttons":
            await send_message(
                chat_id,
                "🔘 BUTTON MANAGEMENT\n\n"
                "Add a button:\n"
                "/addbutton Website|https://example.com\n\n"
                "Remove all custom buttons:\n"
                "/removebuttons\n\n"
                "Publish updated buttons:\n"
                "/postvip"
            )
        return


# --------------------------------------------------
# AUTOMATIC SUBSCRIPTION EXPIRY
# --------------------------------------------------

async def expire_members():
    now = utcnow().isoformat()

    with connect_db() as con:
        expired = con.execute("""
            SELECT * FROM subscriptions
            WHERE status='ACTIVE' AND expires_at<=?
        """, (now,)).fetchall()

    for member in expired:
        user_id = member["user_id"]

        try:
            await telegram("banChatMember", {
                "chat_id": VIP_CHANNEL_ID,
                "user_id": user_id
            })

            await telegram("unbanChatMember", {
                "chat_id": VIP_CHANNEL_ID,
                "user_id": user_id,
                "only_if_banned": True
            })

            with connect_db() as con:
                con.execute(
                    "UPDATE subscriptions SET status='EXPIRED' WHERE user_id=?",
                    (user_id,)
                )

            await send_message(
                user_id,
                "Your John Tipster VIP subscription has expired. "
                "Use /start to renew your membership."
            )

            log.info("Expired VIP member %s", user_id)

        except Exception:
            log.exception("Could not remove expired member %s", user_id)


async def expiry_worker():
    while True:
        try:
            await expire_members()
        except Exception:
            log.exception("Expiry check failed")

        await asyncio.sleep(60)


# --------------------------------------------------
# FASTAPI AND WEBHOOKS
# --------------------------------------------------

@app.on_event("startup")
async def startup():
    global bot_username, expiry_task

    check_settings()
    init_db()

    me = await telegram("getMe", {})
    bot_username = me["username"]

    await telegram("setWebhook", {
        "url": PUBLIC_URL.rstrip("/") + "/telegram",
        "secret_token": WEBHOOK_SECRET,
        "allowed_updates": ["message", "callback_query"]
    })

    expiry_task = asyncio.create_task(expiry_worker())

    log.info("John Tipster VIP bot started as @%s", bot_username)


@app.on_event("shutdown")
async def shutdown():
    global expiry_task

    if expiry_task:
        expiry_task.cancel()


@app.get("/")
async def home():
    return {"status": "John Tipster VIP Bot is running"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post("/telegram")
async def telegram_webhook(request: Request):
    secret = request.headers.get(
        "X-Telegram-Bot-Api-Secret-Token", ""
    )

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

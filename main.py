
import os
import logging
import secrets
import httpx

from fastapi import FastAPI, Request, HTTPException

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("john-tipster")

# Environment variables
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET")

# Render supplies this URL automatically
PUBLIC_URL = os.environ.get("RENDER_EXTERNAL_URL")

# Check required settings
required = {
    "TELEGRAM_BOT_TOKEN": BOT_TOKEN,
    "CHANNEL_ID": CHANNEL_ID,
    "WEBHOOK_SECRET": WEBHOOK_SECRET,
    "RENDER_EXTERNAL_URL": PUBLIC_URL,
}

missing = [key for key, value in required.items() if not value]

if missing:
    raise RuntimeError(
        "Missing Render environment variables: " + ", ".join(missing)
    )

PUBLIC_URL = PUBLIC_URL.rstrip("/")
API = f"https://api.telegram.org/bot{BOT_TOKEN}"

REACTIONS = ["🔥", "❤️", "👍"]

app = FastAPI()


@app.on_event("startup")
async def register_webhook():
    logger.info("Registering Telegram webhook...")

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{API}/setWebhook",
            json={
                "url": f"{PUBLIC_URL}/webhook",
                "secret_token": WEBHOOK_SECRET,
                "allowed_updates": ["channel_post"]
            }
        )

        result = response.json()

        if not result.get("ok"):
            raise RuntimeError(
                f"Telegram webhook registration failed: {result}"
            )

    logger.info("Telegram webhook registered successfully.")


@app.get("/")
async def home():
    return {
        "status": "John Tipster Reactions Bot is running"
    }


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post("/webhook")
async def telegram_webhook(request: Request):

    received_secret = request.headers.get(
        "X-Telegram-Bot-Api-Secret-Token", ""
    )

    if not secrets.compare_digest(received_secret, WEBHOOK_SECRET):
        raise HTTPException(status_code=403, detail="Unauthorized")

    update = await request.json()

    post = update.get("channel_post")

    if not post:
        return {"ok": True}

    if str(post["chat"]["id"]) != str(CHANNEL_ID):
        return {"ok": True}

    message_id = post["message_id"]

    reaction = REACTIONS[message_id % len(REACTIONS)]

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{API}/setMessageReaction",
            json={
                "chat_id": CHANNEL_ID,
                "message_id": message_id,
                "reaction": [
                    {
                        "type": "emoji",
                        "emoji": reaction
                    }
                ]
            }
        )

        result = response.json()

        if not result.get("ok"):
            logger.error("Reaction failed: %s", result)
        else:
            logger.info(
                "Reaction %s added to message %s",
                reaction,
                message_id
            )

    return {"ok": True}

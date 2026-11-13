
import os
import httpx
from fastapi import FastAPI, Request, HTTPException

app = FastAPI()

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
PUBLIC_URL = os.environ["PUBLIC_URL"].rstrip("/")

API = f"https://api.telegram.org/bot{BOT_TOKEN}"

REACTIONS = ["🔥", "❤️", "👍"]


@app.on_event("startup")
async def setup_webhook():
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{API}/setWebhook",
            json={
                "url": f"{PUBLIC_URL}/webhook",
                "secret_token": WEBHOOK_SECRET,
                "allowed_updates": ["channel_post"]
            }
        )
        response.raise_for_status()


@app.get("/")
async def home():
    return {"status": "John Tipster Reactions Bot is running"}


@app.post("/webhook")
async def webhook(request: Request):
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Unauthorized")

    update = await request.json()
    post = update.get("channel_post")

    if post and str(post["chat"]["id"]) == str(CHANNEL_ID):
        reaction = REACTIONS[post["message_id"] % len(REACTIONS)]

        async with httpx.AsyncClient() as client:
            await client.post(
                f"{API}/setMessageReaction",
                json={
                    "chat_id": CHANNEL_ID,
                    "message_id": post["message_id"],
                    "reaction": [{"type": "emoji", "emoji": reaction}]
                }
            )

    return {"ok": True}

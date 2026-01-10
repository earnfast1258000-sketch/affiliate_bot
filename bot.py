import os
from datetime import datetime, date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)
from pymongo import MongoClient
from bson import ObjectId

from flask import Flask, request
import threading

# ========= CONFIG =========
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

ADMIN_ID = os.getenv("ADMIN_ID")
if not ADMIN_ID:
    raise Exception("ADMIN_ID not set")
ADMIN_ID = int(ADMIN_ID)

# ========= POSTBACK SERVER CONFIG =========
app_flask = Flask(__name__)

@app_flask.before_request
def log_request():
    print("POSTBACK HIT:", request.url)

POSTBACK_SECRET = os.getenv("POSTBACK_SECRET", "mysecret123")

# ========= DB =========
client = MongoClient(MONGO_URI)
db = client["affiliate_bot"]

users = db["users"]
withdraws = db["withdraws"]
campaigns = db["campaigns"]
campaign_stats = db["campaign_stats"]

# ========= HELPERS =========
def get_user(user):
    u = users.find_one({"telegram_id": user.id})
    if not u:
        users.insert_one({
            "telegram_id": user.id,
            "wallet": 0,
            "total_earned": 0,
            "last_withdraw_date": None
        })
        u = users.find_one({"telegram_id": user.id})
    else:
        if "last_withdraw_date" not in u:
            users.update_one(
                {"telegram_id": user.id},
                {"$set": {"last_withdraw_date": None}}
            )
            u["last_withdraw_date"] = None
    return u


def can_credit(campaign_name, user_id, daily_cap, user_cap):
    today = date.today().isoformat()

    day_count = campaign_stats.count_documents({
        "campaign": campaign_name,
        "date": today
    })

    user_count = campaign_stats.count_documents({
        "campaign": campaign_name,
        "date": today,
        "user_id": user_id
    })

    if daily_cap != "∞" and day_count >= daily_cap:
        return False

    if user_cap != "∞" and user_count >= user_cap:
        return False

    return True


def credit_user_for_campaign(user_id, campaign_name, payout):
    campaign = campaigns.find_one({"name": campaign_name, "status": "active"})
    if not campaign:
        return False, "Campaign not active"

    daily_cap = campaign.get("daily_cap", "∞")
    user_cap = campaign.get("user_cap", "∞")

    if not can_credit(campaign_name, user_id, daily_cap, user_cap):
        return False, "Cap reached"

    users.update_one(
        {"telegram_id": user_id},
        {"$inc": {"wallet": payout, "total_earned": payout}}
    )

    campaign_stats.insert_one({
        "campaign": campaign_name,
        "user_id": user_id,
        "date": date.today().isoformat()
    })

    return True, "Credited"


@app_flask.route("/postback", methods=["GET"])
def postback():
    print("ARGS:", dict(request.args))

    secret = request.args.get("secret")
    user_id = request.args.get("p1") or request.args.get("user_id")
    campaign = request.args.get("campaign")

    if not secret or not user_id or not campaign:
        return f"missing params: {dict(request.args)}", 400

    if secret != POSTBACK_SECRET:
        return f"unauthorized: expected={POSTBACK_SECRET}", 403

    try:
        user_id = int(user_id)
    except:
        return "invalid user id", 400

    camp = campaigns.find_one({"name": campaign, "status": "active"})
    if not camp:
        return "campaign not found", 404

    ok, msg = credit_user_for_campaign(user_id, campaign, camp["payout"])

    return "ok" if ok else f"blocked: {msg}"


# ========= START =========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_user(update.effective_user)

    kb = [
        [InlineKeyboardButton("📊 Dashboard", callback_data="dashboard")],
        [InlineKeyboardButton("📢 Campaigns", callback_data="campaigns")],
        [InlineKeyboardButton("💰 Wallet", callback_data="wallet")],
        [InlineKeyboardButton("🏦 Withdraw", callback_data="withdraw")],
        [InlineKeyboardButton("📜 Withdraw History", callback_data="history")]
    ]

    await update.message.reply_text(
        "Welcome to Affiliate Bot 👋",
        reply_markup=InlineKeyboardMarkup(kb)
    )


async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = get_user(q.from_user)

    if q.data == "dashboard":
        await q.edit_message_text(
            f"📊 Dashboard\n\n💰 Wallet: ₹{user['wallet']}\n🏆 Total Earned: ₹{user['total_earned']}"
        )

    elif q.data == "wallet":
        await q.edit_message_text(f"💰 Wallet Balance\n\n₹{user['wallet']}")

    elif q.data == "campaigns":
        user_id = q.from_user.id
        text = "📣 Campaigns\n\n"
        found = False

        for c in campaigns.find({"status": "active"}):
            base_link = c.get("link", "")
            if not base_link:
                continue

            tracking_link = f"{base_link}&p1={user_id}"

            found = True
            text += (
                f"🔥 {c['name']}\n"
                f"💰 ₹{c['payout']} ({c['type']})\n"
                f"👉 {tracking_link}\n\n"
            )

        await q.message.reply_text(text if found else "❌ No campaigns available")


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pass


async def admin_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pass


async def admin_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pass


# ========= RUN =========
app = ApplicationBuilder().token(BOT_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(buttons))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

print("Bot is running...")


def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app_flask.run(host="0.0.0.0", port=port, use_reloader=False)


if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    app.run_polling()
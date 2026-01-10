import os
import threading
from datetime import datetime, date
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)
from pymongo import MongoClient
from bson import ObjectId

# ========= CONFIG =========
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
POSTBACK_SECRET = os.getenv("POSTBACK_SECRET", "mysecret123")

# ========= DB =========
client = MongoClient(MONGO_URI)
db = client["affiliate_bot"]
users = db["users"]
withdraws = db["withdraws"]
campaigns = db["campaigns"]
campaign_stats = db["campaign_stats"]

# ========= HELPERS =========
def get_user(user_obj):
    u = users.find_one({"telegram_id": user_obj.id})
    if not u:
        users.insert_one({
            "telegram_id": user_obj.id,
            "wallet": 0,
            "total_earned": 0,
            "last_withdraw_date": None
        })
        u = users.find_one({"telegram_id": user_obj.id})
    return u

def credit_user_for_campaign(user_id, campaign_name, payout):
    # Campaign check
    camp = campaigns.find_one({"name": campaign_name, "status": "active"})
    if not camp:
        return False, "Campaign not active"

    # Wallet credit
    users.update_one(
        {"telegram_id": user_id},
        {"$inc": {"wallet": payout, "total_earned": payout}}
    )
    # Stats record
    campaign_stats.insert_one({
        "campaign": campaign_name,
        "user_id": user_id,
        "date": date.today().isoformat()
    })
    return True, "Credited"

# ========= POSTBACK SERVER =========
app_flask = Flask(__name__)

@app_flask.route("/postback", methods=["GET"])
def postback():
    args = dict(request.args)
    print(f"DEBUG: Postback hit with {args}") # Railway logs ke liye

    secret = request.args.get("secret")
    user_id = request.args.get("p1") or request.args.get("user_id")
    campaign = request.args.get("campaign")

    # Error checking for 400 Bad Request
    if not secret or not user_id or not campaign:
        return f"Missing Params: {args}", 400

    if secret != POSTBACK_SECRET:
        return "Unauthorized Secret", 403

    try:
        u_id = int(user_id)
        # DB se campaign check karke credit karna
        camp = campaigns.find_one({"name": campaign, "status": "active"})
        if not camp:
            return f"Campaign {campaign} not found in DB", 404
            
        ok, msg = credit_user_for_campaign(u_id, campaign, camp["payout"])
        return "ok" if ok else f"blocked: {msg}"
    except Exception as e:
        return f"Error: {str(e)}", 500

# ========= HANDLERS (Same as your original) =========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_user(update.effective_user)
    kb = [[InlineKeyboardButton("📊 Dashboard", callback_data="dashboard")],
          [InlineKeyboardButton("📢 Campaigns", callback_data="campaigns")]]
    await update.message.reply_text("Bot restarted successfully!", reply_markup=InlineKeyboardMarkup(kb))

# (Add your other handlers: buttons, text_handler, admin_actions here)

# ========= RUN =========
def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app_flask.run(host="0.0.0.0", port=port, use_reloader=False)

if __name__ == "__main__":
    # Flask in background
    threading.Thread(target=run_flask, daemon=True).start()

    # Telegram Bot
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Conflict fix: Bot start hone se pehle purane webhook/session clear karega
    app.add_handler(CommandHandler("start", start))
    # (Baaki handlers register karein)

    print("Starting bot...")
    # drop_pending_updates=True se purane 'Conflict' khatam ho jayenge
    app.run_polling(drop_pending_updates=True)
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
    camp = campaigns.find_one({"name": campaign_name, "status": "active"})
    if not camp:
        return False, "Campaign not active or not found"

    # Wallet Update
    users.update_one(
        {"telegram_id": user_id},
        {"$inc": {"wallet": payout, "total_earned": payout}}
    )
    # Stats Update
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
    # Railway Logs mein parameters check karne ke liye
    args = dict(request.args)
    print(f"--- POSTBACK ATTEMPT ---")
    print(f"Data Received: {args}")

    secret = request.args.get("secret")
    user_id = request.args.get("p1") or request.args.get("user_id")
    campaign = request.args.get("campaign")

    # 1. Parameter Check
    if not secret or not user_id or not campaign:
        print(f"RESULT: Failed - Missing Params")
        return f"Missing Params: got {list(args.keys())}", 400

    # 2. Secret Check
    if secret != POSTBACK_SECRET:
        print(f"RESULT: Failed - Secret Mismatch (Got: {secret}, Expected: {POSTBACK_SECRET})")
        return "Unauthorized: Invalid Secret", 403

    # 3. Process Credit
    try:
        u_id = int(user_id)
        ok, msg = credit_user_for_campaign(u_id, campaign, 0) # Payout DB se aayega
        
        # NOTE: Humne payout 0 pass kiya hai kyunki credit_user function 
        # khud DB se payout fetch karega agar aap chahein.
        # Niche wala logic zyada safe hai:
        
        camp = campaigns.find_one({"name": campaign, "status": "active"})
        if not camp:
            return f"Campaign {campaign} not found", 404
            
        ok, msg = credit_user_for_campaign(u_id, campaign, camp["payout"])
        
        if ok:
            print(f"RESULT: Success - Credited {u_id}")
            return "ok"
        else:
            print(f"RESULT: Blocked - {msg}")
            return f"blocked: {msg}", 200
    except Exception as e:
        print(f"RESULT: Error - {str(e)}")
        return f"Error: {str(e)}", 500

# ========= BOT COMMANDS & BUTTONS (SAME AS YOURS) =========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_user(update.effective_user)
    kb = [[InlineKeyboardButton("📊 Dashboard", callback_data="dashboard")],
          [InlineKeyboardButton("📢 Campaigns", callback_data="campaigns")]]
    await update.message.reply_text("Bot is Active!", reply_markup=InlineKeyboardMarkup(kb))

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Aapka purana buttons logic yahan aayega
    pass

# ========= RUN =========
def run_flask():
    # Railway Port Handling
    port = int(os.environ.get("PORT", 8080))
    print(f"Flask starting on port {port}")
    app_flask.run(host="0.0.0.0", port=port, use_reloader=False)

if __name__ == "__main__":
    # Thread for Flask
    threading.Thread(target=run_flask, daemon=True).start()
    
    # Start Telegram Bot
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons))
    
    print("Bot Polling Started...")
    app.run_polling()
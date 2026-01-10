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
    return u

def can_credit(campaign_name, user_id, daily_cap, user_cap):
    today = date.today().isoformat()
    day_count = campaign_stats.count_documents({"campaign": campaign_name, "date": today})
    user_count = campaign_stats.count_documents({"campaign": campaign_name, "date": today, "user_id": user_id})

    if daily_cap != "∞" and day_count >= int(daily_cap): return False
    if user_cap != "∞" and user_count >= int(user_cap): return False
    return True

def credit_user_for_campaign(user_id, campaign_name, payout):
    campaign = campaigns.find_one({"name": campaign_name, "status": "active"})
    if not campaign: return False, "Campaign not active"

    daily_cap = campaign.get("daily_cap", "∞")
    user_cap = campaign.get("user_cap", "∞")

    if not can_credit(campaign_name, user_id, daily_cap, user_cap):
        return False, "Cap reached"

    users.update_one({"telegram_id": user_id}, {"$inc": {"wallet": payout, "total_earned": payout}})
    campaign_stats.insert_one({"campaign": campaign_name, "user_id": user_id, "date": date.today().isoformat()})
    return True, "Credited"

# ========= POSTBACK SERVER =========
app_flask = Flask(__name__)

@app_flask.route("/postback", methods=["GET"])
def postback():
    args = request.args
    print(f"DEBUG LOG: Received params: {dict(args)}") # Railway logs me dikhega

    secret = args.get("secret")
    user_id = args.get("p1") or args.get("user_id")
    campaign_name = args.get("campaign")

    # Error checking
    if not secret: return "Missing: secret", 400
    if not user_id: return "Missing: p1 (user_id)", 400
    if not campaign_name: return "Missing: campaign", 400

    if secret != POSTBACK_SECRET:
        return f"Unauthorized: Secret mismatch (Expected: {POSTBACK_SECRET})", 403

    camp = campaigns.find_one({"name": campaign_name, "status": "active"})
    if not camp:
        return f"Error: Campaign '{campaign_name}' not found or inactive in DB", 404

    try:
        u_id = int(user_id)
        ok, msg = credit_user_for_campaign(u_id, campaign_name, camp["payout"])
        if ok:
            return "ok", 200
        else:
            return f"Blocked: {msg}", 200 # 200 so network doesn't retry blocked ones
    except Exception as e:
        return f"Error: {str(e)}", 500

# ========= TELEGRAM HANDLERS =========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_user(update.effective_user)
    kb = [[InlineKeyboardButton("📊 Dashboard", callback_data="dashboard")],
          [InlineKeyboardButton("📢 Campaigns", callback_data="campaigns")],
          [InlineKeyboardButton("💰 Wallet", callback_data="wallet")],
          [InlineKeyboardButton("🏦 Withdraw", callback_data="withdraw")]]
    await update.message.reply_text("Welcome to Affiliate Bot 👋", reply_markup=InlineKeyboardMarkup(kb))

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = get_user(q.from_user)

    if q.data == "dashboard":
        await q.edit_message_text(f"📊 Dashboard\n\n💰 Wallet: ₹{user['wallet']}\n🏆 Total: ₹{user['total_earned']}")
    elif q.data == "campaigns":
        text = "📣 Active Campaigns:\n\n"
        found = False
        for c in campaigns.find({"status": "active"}):
            found = True
            link = f"{c['link']}&p1={q.from_user.id}"
            text += f"🔥 {c['name']} - ₹{c['payout']}\n👉 {link}\n\n"
        await q.message.reply_text(text if found else "❌ No campaigns")

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # (Withdrawal logic remains same as your original code)
    pass

# ========= ADMIN COMMANDS =========
async def addcampaign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        name, ctype, payout, link = context.args[0], context.args[1], int(context.args[2]), context.args[3]
        campaigns.insert_one({"name": name, "type": ctype, "payout": payout, "link": link, "daily_cap": 1000, "user_cap": 10, "status": "active"})
        await update.message.reply_text(f"✅ Campaign {name} added!")
    except:
        await update.message.reply_text("Usage: /addcampaign name CPI 50 link")

# ========= RUN =========
def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app_flask.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("addcampaign", addcampaign))
    application.add_handler(CallbackQueryHandler(buttons))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    threading.Thread(target=run_flask, daemon=True).start()
    print("Bot & Postback Server Started...")
    application.run_polling()
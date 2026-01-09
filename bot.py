import os
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from pymongo import MongoClient
from datetime import datetime

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN") or "8516452076:AAFxCygUUCksJaIe7bOWBwKdLICApe-RW5A"
MONGO_URI = os.getenv("MONGO_URI") or "mongodb+srv://botuser:bot97941923@cluster0.cnvz6ta.mongodb.net/?appName=Cluster0"
ADMIN_ID = int(os.getenv("ADMIN_ID") or 7731384448)
# ========================================

client = MongoClient(MONGO_URI)
db = client["affiliate_bot"]

users = db["users"]
withdraws = db["withdraws"]

# ================= HELPERS =================
def get_user(user):
    u = users.find_one({"telegram_id": user.id})
    if not u:
        users.insert_one({
            "telegram_id": user.id,
            "username": user.username,
            "wallet": 0,
            "total_earned": 0,
            "created_at": datetime.utcnow()
        })
        u = users.find_one({"telegram_id": user.id})
    return u
# ===========================================

# ================= COMMANDS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_user)

    keyboard = [
        [InlineKeyboardButton("📊 Dashboard", callback_data="dashboard")],
        [InlineKeyboardButton("💰 Wallet", callback_data="wallet")],
        [InlineKeyboardButton("📢 Campaigns", callback_data="campaigns")],
        [InlineKeyboardButton("🏦 Withdraw", callback_data="withdraw")],
    ]
    await update.message.reply_text(
        "Welcome to Affiliate Bot 👋",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = get_user(q.from_user)

    if q.data == "dashboard":
        await q.edit_message_text(
            f"📊 Dashboard\n\n💰 Wallet: ₹{user['wallet']}\n💸 Total Earned: ₹{user['total_earned']}"
        )

    elif q.data == "wallet":
        await q.edit_message_text(f"💰 Wallet Balance\n\n₹{user['wallet']}")

    elif q.data == "campaigns":
        await q.edit_message_text(
            "📢 Campaigns\n\n"
            "1️⃣ App Install – ₹50\n"
            "2️⃣ Signup – ₹100"
        )

    elif q.data == "withdraw":
        if user["wallet"] < 100:
            await q.edit_message_text("❌ Minimum withdraw ₹100")
            return

        amount = user["wallet"]

        # LOCK BALANCE
        users.update_one(
            {"telegram_id": user["telegram_id"]},
            {"$set": {"wallet": 0}}
        )

        wid = withdraws.insert_one({
            "user_id": user["telegram_id"],
            "amount": amount,
            "status": "pending",
            "created_at": datetime.utcnow()
        }).inserted_id

        await q.edit_message_text(
            f"✅ Withdraw Request Sent\n\nAmount: ₹{amount}\nID: `{wid}`",
            parse_mode="Markdown"
        )

        await context.bot.send_message(
            ADMIN_ID,
            f"🆕 Withdraw Request\n\nID: {wid}\nUser: {user['telegram_id']}\nAmount: ₹{amount}",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Approve", callback_data=f"approve_{wid}"),
                    InlineKeyboardButton("❌ Reject", callback_data=f"reject_{wid}")
                ]
            ])
        )

# ================= ADMIN =================
async def admin_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    action, wid = q.data.split("_", 1)
    req = withdraws.find_one({"_id": withdraws.codec_options.document_class(wid)})

    # fallback if ObjectId parsing fails
    req = withdraws.find_one({"_id": wid})

    if not req or req["status"] != "pending":
        await q.edit_message_text("❌ Invalid withdraw ID")
        return

    if action == "approve":
        withdraws.update_one(
            {"_id": req["_id"]},
            {"$set": {"status": "approved", "approved_at": datetime.utcnow()}}
        )
        await q.edit_message_text("✅ Withdraw Approved")

    elif action == "reject":
        users.update_one(
            {"telegram_id": req["user_id"]},
            {"$inc": {"wallet": req["amount"]}}
        )
        withdraws.update_one(
            {"_id": req["_id"]},
            {"$set": {"status": "rejected", "rejected_at": datetime.utcnow()}}
        )
        await q.edit_message_text("❌ Withdraw Rejected & Amount Refunded")

# ================= MAIN =================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(admin_actions, pattern="^(approve|reject)_"))
    app.add_handler(CallbackQueryHandler(buttons))

    print("🤖 Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()

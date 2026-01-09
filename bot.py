import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)
from pymongo import MongoClient
from bson import ObjectId

# ============ CONFIG ============
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
# ================================

client = MongoClient(MONGO_URI)
db = client["affiliate_bot"]
users = db["users"]
withdraws = db["withdraws"]

# ---------- START ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not users.find_one({"telegram_id": user.id}):
        users.insert_one({
            "telegram_id": user.id,
            "wallet": 0
        })

    kb = [
        [InlineKeyboardButton("💰 Wallet", callback_data="wallet")],
        [InlineKeyboardButton("🏦 Withdraw", callback_data="withdraw")]
    ]
    await update.message.reply_text(
        "Welcome 👋",
        reply_markup=InlineKeyboardMarkup(kb)
    )

# ---------- BUTTONS ----------
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = users.find_one({"telegram_id": q.from_user.id})

    if q.data == "wallet":
        await q.edit_message_text(f"Wallet: ₹{user['wallet']}")

    elif q.data == "withdraw":
        context.user_data["withdraw_step"] = "amount"
        await q.edit_message_text("Enter withdraw amount (min ₹100):")

# ---------- TEXT HANDLER ----------
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    user = users.find_one({"telegram_id": uid})

    # STEP 1: amount
    if context.user_data.get("withdraw_step") == "amount":
        if not text.isdigit():
            await update.message.reply_text("Enter valid amount")
            return

        amount = int(text)
        if amount < 100 or user["wallet"] < amount:
            await update.message.reply_text("Invalid or insufficient balance")
            context.user_data.clear()
            return

        context.user_data["amount"] = amount
        context.user_data["withdraw_step"] = "upi"
        await update.message.reply_text("Enter your UPI ID:")

    # STEP 2: upi
    elif context.user_data.get("withdraw_step") == "upi":
        upi = text
        amount = context.user_data["amount"]

        # 🔥 deduct balance NOW
        users.update_one(
            {"telegram_id": uid},
            {"$inc": {"wallet": -amount}}
        )

        wid = withdraws.insert_one({
            "user_id": uid,
            "amount": amount,
            "upi": upi,
            "status": "pending"
        }).inserted_id

        context.user_data.clear()

        # notify admin
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"approve_{wid}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"reject_{wid}")
            ]
        ])
        await context.bot.send_message(
            ADMIN_ID,
            f"Withdraw Request\nUser: {uid}\nAmount: ₹{amount}\nUPI: {upi}",
            reply_markup=kb
        )

        await update.message.reply_text("Withdraw request submitted ⏳")

# ---------- ADMIN ACTION ----------
async def admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    action, wid = q.data.split("_")
    w = withdraws.find_one({"_id": ObjectId(wid)})

    if not w or w["status"] != "pending":
        await q.edit_message_text("Already processed")
        return

    if action == "approve":
        withdraws.update_one(
            {"_id": ObjectId(wid)},
            {"$set": {"status": "approved"}}
        )
        await q.edit_message_text("Approved ✅")
        await context.bot.send_message(w["user_id"], "Withdraw approved ✅")

    elif action == "reject":
        users.update_one(
            {"telegram_id": w["user_id"]},
            {"$inc": {"wallet": w["amount"]}}
        )
        withdraws.update_one(
            {"_id": ObjectId(wid)},
            {"$set": {"status": "rejected"}}
        )
        await q.edit_message_text("Rejected ❌")
        await context.bot.send_message(w["user_id"], "Withdraw rejected, amount refunded")

# ---------- RUN ----------
app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(admin_action, pattern="^(approve|reject)_"))
app.add_handler(CallbackQueryHandler(buttons))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

print("Bot is running...")
app.run_polling()
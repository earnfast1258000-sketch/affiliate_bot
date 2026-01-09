import os
from datetime import datetime, date
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
# ==========================

client = MongoClient(MONGO_URI)
db = client["affiliate_bot"]

users = db["users"]
withdraws = db["withdraws"]
campaigns = db["campaigns"]
applications = db["applications"]

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

# ========= BUTTONS =========
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = get_user(q.from_user)

    if q.data == "dashboard":
        await q.edit_message_text(
            f"📊 Dashboard\n\n"
            f"💰 Wallet: ₹{user['wallet']}\n"
            f"🏆 Total Earned: ₹{user['total_earned']}"
        )

    elif q.data == "wallet":
        await q.edit_message_text(f"💰 Wallet Balance\n\n₹{user['wallet']}")

    elif q.data == "campaigns":
    user_id = q.from_user.id
    text = "📣 Campaigns\n\n"

    found = False
    for c in campaigns.find():
        found = True

        # base affiliate link DB se
        base_link = c["link"]   # example: https://affiliatepanel.com/offer?id=123

        # tracking link with user_id (p1)
        tracking_link = f"{base_link}&p1={user_id}"

        text += (
            f"🔥 {c['name']}\n"
            f"💰 Payout: ₹{c['payout']} ({c['type']})\n"
            f"👉 {tracking_link}\n\n"
        )

    await q.edit_message_text(text if found else "No campaigns available")

    elif q.data == "withdraw":
        today = date.today().isoformat()
        if user["last_withdraw_date"] == today:
            await q.edit_message_text("❌ Daily withdraw limit reached")
            return
        context.user_data["withdraw_step"] = "amount"
        await q.edit_message_text("Enter withdraw amount (min ₹100):")

    elif q.data == "history":
        text = "📜 Withdraw History\n\n"
        found = False
        for w in withdraws.find({"user_id": user["telegram_id"]}).sort("_id", -1).limit(5):
            found = True
            text += f"₹{w['amount']} – {w['status'].upper()}\n"
        await q.edit_message_text(text if found else "No withdraw history")

# ========= TEXT =========
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    user = get_user(update.effective_user)

    # Withdraw amount
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

    # Withdraw UPI
    elif context.user_data.get("withdraw_step") == "upi":
        amount = context.user_data["amount"]
        upi = text

        users.update_one(
            {"telegram_id": uid},
            {
                "$inc": {"wallet": -amount},
                "$set": {"last_withdraw_date": date.today().isoformat()}
            }
        )

        wid = withdraws.insert_one({
            "user_id": uid,
            "amount": amount,
            "upi": upi,
            "status": "pending",
            "created_at": datetime.utcnow()
        }).inserted_id

        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"approve_{wid}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"reject_{wid}")
            ]
        ])

        await context.bot.send_message(
            ADMIN_ID,
            f"Withdraw Request\nUser: {uid}\n₹{amount}\nUPI: {upi}",
            reply_markup=kb
        )

        context.user_data.clear()
        await update.message.reply_text("Withdraw request submitted ⏳")

# ========= ADMIN =========
async def admin_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    action, wid = q.data.split("_")
    w = withdraws.find_one({"_id": ObjectId(wid)})

    if not w or w["status"] != "pending":
        await q.edit_message_text("Invalid request")
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

# ========= ADMIN ADD BALANCE =========
async def addbalance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        uid = int(context.args[0])
        amt = int(context.args[1])
    except:
        await update.message.reply_text("Usage: /addbalance user_id amount")
        return

    users.update_one(
        {"telegram_id": uid},
        {"$inc": {"wallet": amt, "total_earned": amt}}
    )
    await update.message.reply_text("Balance added ✅")

async def addcampaign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    try:
        name = context.args[0]
        ctype = context.args[1].upper()
        payout = int(context.args[2])
    except:
        await update.message.reply_text(
            "Usage:\n/addcampaign <name> <CPI/CPA> <amount>"
        )
        return

    campaigns.insert_one({
        "name": name,
        "type": ctype,
        "payout": payout,
        "status": "active"
    })

    await update.message.reply_text(
        f"✅ Campaign Added\n\n"
        f"Name: {name}\n"
        f"Type: {ctype}\n"
        f"Payout: ₹{payout}"
    )


# ========= RUN =========
app = ApplicationBuilder().token(BOT_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("addbalance", addbalance))
app.add_handler(CallbackQueryHandler(admin_actions, pattern="^(approve|reject)_"))
app.add_handler(CallbackQueryHandler(buttons))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
app.add_handler(CommandHandler("addcampaign", addcampaign))
app.add_handler(CallbackQueryHandler(button_handler))

print("Bot is running...")
app.run_polling()
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

ADMIN_ID = os.getenv("ADMIN_ID")
if not ADMIN_ID:
    raise Exception("ADMIN_ID not set")
ADMIN_ID = int(ADMIN_ID)

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

# ========= BUTTON HANDLER =========
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

        for c in campaigns.find({"status": "active"}):
            base_link = c.get("link", "")
            if not base_link:
                continue

            tracking_link = f"{base_link}&p1={user_id}"
            daily_cap = c.get("daily_cap", "∞")
            user_cap = c.get("user_cap", "∞")

            found = True
            text += (
                f"🔥 {c['name']}\n"
                f"💰 ₹{c['payout']} ({c['type']})\n"
                f"👤 User limit: {user_cap}\n"
                f"📆 Daily cap: {daily_cap}\n"
                f"👉 {tracking_link}\n\n"
            )

        await q.message.reply_text(
            text if found else "❌ No campaigns available",
            disable_web_page_preview=True
        )

    elif q.data == "withdraw":
        today = date.today().isoformat()
        if user.get("last_withdraw_date") == today:
            await q.message.reply_text("❌ Daily withdraw limit reached")
            return

        context.user_data.clear()
        context.user_data["withdraw_step"] = "amount"
        await q.message.reply_text("Enter withdraw amount (min ₹100):")

    elif q.data == "history":
        text = "📜 Withdraw History\n\n"
        found = False

        for w in withdraws.find(
            {"user_id": user["telegram_id"]}
        ).sort("_id", -1).limit(5):
            found = True
            text += f"₹{w['amount']} – {w['status'].upper()}\n"

        await q.message.reply_text(text if found else "No withdraw history")

# ========= TEXT HANDLER =========
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    user = get_user(update.effective_user)

    if context.user_data.get("withdraw_step") == "amount":
        if not text.isdigit():
            await update.message.reply_text("❌ Enter valid amount")
            return

        amount = int(text)
        if amount < 100 or user["wallet"] < amount:
            await update.message.reply_text("❌ Invalid or insufficient balance")
            context.user_data.clear()
            return

        context.user_data["amount"] = amount
        context.user_data["withdraw_step"] = "upi"
        await update.message.reply_text("Enter your UPI ID:")

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
            chat_id=ADMIN_ID,
            text=f"💸 New Withdraw Request\n\nUser ID: {uid}\nAmount: ₹{amount}\nUPI: {upi}",
            reply_markup=kb
        )

        context.user_data.clear()
        await update.message.reply_text("Withdraw request submitted ⏳")

# ========= ADMIN ACTIONS =========
async def admin_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    action, wid = q.data.split("_")
    w = withdraws.find_one({"_id": ObjectId(wid)})

    if not w or w["status"] != "pending":
        await q.edit_message_text("❌ Invalid request")
        return

    if action == "approve":
        withdraws.update_one(
            {"_id": ObjectId(wid)},
            {"$set": {"status": "approved"}}
        )
        await q.edit_message_text("✅ Approved")
        await context.bot.send_message(w["user_id"], "✅ Withdraw approved")

    elif action == "reject":
        users.update_one(
            {"telegram_id": w["user_id"]},
            {"$inc": {"wallet": w["amount"]}}
        )
        withdraws.update_one(
            {"_id": ObjectId(wid)},
            {"$set": {"status": "rejected"}}
        )
        await q.edit_message_text("❌ Rejected")
        await context.bot.send_message(
            w["user_id"],
            "❌ Withdraw rejected, amount refunded"
        )

# ========= ADMIN COMMAND =========
async def addcampaign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    if len(context.args) < 4:
        await update.message.reply_text(
            "Usage:\n/addcampaign <name> <CPI/CPA> <amount> <link>"
        )
        return

    name = context.args[0]
    ctype = context.args[1].upper()
    payout = int(context.args[2])
    link = context.args[3]

    campaigns.insert_one({
        "name": name,
        "type": ctype,
        "payout": payout,
        "link": link,
        "daily_cap": 100000,
        "user_cap": 1000,
        "status": "active",
        "created_at": datetime.utcnow()
    })

    await update.message.reply_text("✅ Campaign added")


async def pausecampaign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    if len(context.args) < 1:
        await update.message.reply_text("Usage: /pausecampaign <campaign_name>")
        return

    name = context.args[0]

    res = campaigns.update_one(
        {"name": name},
        {"$set": {"status": "paused"}}
    )

    if res.matched_count:
        await update.message.reply_text("⏸ Campaign paused")
    else:
        await update.message.reply_text("❌ Campaign not found")


async def resumecampaign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    if len(context.args) < 1:
        await update.message.reply_text("Usage: /resumecampaign <campaign_name>")
        return

    name = context.args[0]

    res = campaigns.update_one(
        {"name": name},
        {"$set": {"status": "active"}}
    )

    if res.matched_count:
        await update.message.reply_text("▶️ Campaign resumed")
    else:
        await update.message.reply_text("❌ Campaign not found")


async def listcampaigns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    text = "📋 All Campaigns:\n\n"
    found = False

    for c in campaigns.find():
        found = True
        text += (
            f"Name: {c['name']}\n"
            f"Type: {c['type']}\n"
            f"Payout: ₹{c['payout']}\n"
            f"Status: {c.get('status','active')}\n"
            f"Daily cap: {c.get('daily_cap','∞')}\n"
            f"User cap: {c.get('user_cap','∞')}\n\n"
        )

    await update.message.reply_text(text if found else "No campaigns found")


async def editcampaign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    if len(context.args) < 3:
        await update.message.reply_text(
            "Usage:\n/editcampaign <name> payout <amount>\n/editcampaign <name> link <new_link>"
        )
        return

    name = context.args[0]
    field = context.args[1].lower()
    value = context.args[2]

    if field == "payout":
        value = int(value)
        update_data = {"payout": value}
    elif field == "link":
        update_data = {"link": value}
    else:
        await update.message.reply_text("Field must be payout or link")
        return

    res = campaigns.update_one({"name": name}, {"$set": update_data})

    if res.matched_count:
        await update.message.reply_text("✅ Campaign updated")
    else:
        await update.message.reply_text("❌ Campaign not found")


# ========= RUN =========
app = ApplicationBuilder().token(BOT_TOKEN).build()

app.add_handler(CallbackQueryHandler(admin_actions, pattern="^(approve|reject)_"))
app.add_handler(CallbackQueryHandler(buttons))
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("addcampaign", addcampaign))
app.add_handler(CommandHandler("pausecampaign", pausecampaign))
app.add_handler(CommandHandler("resumecampaign", resumecampaign))
app.add_handler(CommandHandler("listcampaigns", listcampaigns))
app.add_handler(CommandHandler("editcampaign", editcampaign))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

print("Bot is running...")
app.run_polling()
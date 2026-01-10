import os
from datetime import datetime, date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)
from pymongo import MongoClient
from bson import ObjectId

# ✅ ADD THESE
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

    # wallet credit
    users.update_one(
        {"telegram_id": user_id},
        {"$inc": {"wallet": payout, "total_earned": payout}}
    )

    # save stats for cap tracking
    campaign_stats.insert_one({
        "campaign": campaign_name,
        "user_id": user_id,
        "date": date.today().isoformat()
    })

    return True, "Credited"


@app_flask.route("/postback", methods=["GET"])
def postback():
    args = dict(request.args)
    print("POSTBACK ARGS =", args)

    secret = args.get("secret")
    user_id = args.get("p1") or args.get("user_id")

    campaign = (
        args.get("campaign")
        or args.get("camp")
        or args.get("offer")
        or args.get("offer_name")
        or args.get("cid")
    )

    if not secret or not user_id or not campaign:
        return f"missing params: {args}", 400

    if secret != POSTBACK_SECRET:
        return f"unauthorized: {secret}", 403

    try:
        user_id = int(user_id)
    except:
        return "invalid user id", 400

    camp = campaigns.find_one({"name": campaign, "status": "active"})
    if not camp:
        return f"campaign not found: {campaign}", 404

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
            # Clear logic modified slightly to allow retry or cancel
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


async def testcredit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    if len(context.args) < 2:
        await update.message.reply_text("Usage: /credit user_id campaign_name")
        return

    user_id = int(context.args[0])
    campaign_name = context.args[1]

    campaign = campaigns.find_one({"name": campaign_name})
    if not campaign:
        await update.message.reply_text("Campaign not found")
        return

    ok, msg = credit_user_for_campaign(user_id, campaign_name, campaign["payout"])

    if ok:
        await update.message.reply_text("✅ Credited successfully")
    else:
        await update.message.reply_text(f"❌ Credit blocked: {msg}")


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


async def setdailycap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    if len(context.args) < 2:
        await update.message.reply_text("Usage: /setdailycap <campaign_name> <amount>")
        return

    name = context.args[0]
    cap = int(context.args[1])

    res = campaigns.update_one(
        {"name": name},
        {"$set": {"daily_cap": cap}}
    )

    if res.matched_count:
        await update.message.reply_text(f"✅ Daily cap updated to {cap}")
    else:
        await update.message.reply_text("❌ Campaign not found")


async def setusercap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    if len(context.args) < 2:
        await update.message.reply_text("Usage: /setusercap <campaign_name> <count>")
        return

    name = context.args[0]
    cap = int(context.args[1])

    res = campaigns.update_one(
        {"name": name},
        {"$set": {"user_cap": cap}}
    )

    if res.matched_count:
        await update.message.reply_text(f"✅ User cap updated to {cap}")
    else:
        await update.message.reply_text("❌ Campaign not found")


async def adminpanel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    kb = [
        [InlineKeyboardButton("➕ Add Campaign", callback_data="admin_add")],
        [InlineKeyboardButton("📋 List Campaigns", callback_data="admin_list")],
        [InlineKeyboardButton("✏ Edit Campaign", callback_data="admin_edit")],
        [InlineKeyboardButton("⏸ Pause Campaign", callback_data="admin_pause")],
        [InlineKeyboardButton("▶ Resume Campaign", callback_data="admin_resume")],
        [InlineKeyboardButton("🎯 Set Daily Cap", callback_data="admin_dailycap")],
        [InlineKeyboardButton("👤 Set User Cap", callback_data="admin_usercap")]
    ]

    await update.message.reply_text(
        "🛠 Admin Control Panel",
        reply_markup=InlineKeyboardMarkup(kb)
    )


async def admin_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    msg = {
        "admin_add": "Use: /addcampaign name CPI 50 link",
        "admin_list": "Use: /listcampaigns",
        "admin_edit": "Use: /editcampaign name payout link",
        "admin_pause": "Use: /pausecampaign name",
        "admin_resume": "Use: /resumecampaign name",
        "admin_dailycap": "Use: /setdailycap name amount",
        "admin_usercap": "Use: /setusercap name count"
    }

    await q.message.reply_text(msg.get(q.data, "Unknown option"))


# ========= RUN =========
app = ApplicationBuilder().token(BOT_TOKEN).build()

# commands
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("admin", adminpanel))
app.add_handler(CommandHandler("addcampaign", addcampaign))
app.add_handler(CommandHandler("credit", testcredit))
app.add_handler(CommandHandler("pausecampaign", pausecampaign))
app.add_handler(CommandHandler("resumecampaign", resumecampaign))
app.add_handler(CommandHandler("listcampaigns", listcampaigns))
app.add_handler(CommandHandler("editcampaign", editcampaign))
app.add_handler(CommandHandler("setdailycap", setdailycap))
app.add_handler(CommandHandler("setusercap", setusercap))

# callback queries (IMPORTANT ORDER)
app.add_handler(CallbackQueryHandler(admin_actions, pattern="^(approve|reject)_"))
app.add_handler(CallbackQueryHandler(admin_buttons, pattern="^admin_"))   # 👈 FIRST
app.add_handler(CallbackQueryHandler(buttons))                            # 👈 LAST

# messages
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

print("Bot is running...")

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app_flask.run(host="0.0.0.0", port=port, use_reloader=False)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    app.run_polling()

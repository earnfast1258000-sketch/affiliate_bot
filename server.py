import os
from flask import Flask, request
from pymongo import MongoClient
from telegram import Bot

# ===== CONFIG =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
SECRET_KEY = os.getenv("POSTBACK_SECRET")  # optional security
# ==================

bot = Bot(token=BOT_TOKEN)

client = MongoClient(MONGO_URI)
db = client["affiliate_bot"]
users = db["users"]
conversions = db["conversions"]  # to block duplicate

app = Flask(__name__)

@app.route("/postback", methods=["GET"])
def postback():
    # SECURITY (optional but recommended)
    secret = request.args.get("secret")
    if SECRET_KEY and secret != SECRET_KEY:
        return "unauthorized", 403

    subid = request.args.get("subid")
    amount = request.args.get("amount")

    if not subid or not amount:
        return "missing params", 400

    try:
        user_id = int(subid)
        amount = int(float(amount))
    except:
        return "invalid params", 400

    # BLOCK DUPLICATE CONVERSION
    if conversions.find_one({"subid": subid, "amount": amount}):
        return "duplicate", 200

    user = users.find_one({"telegram_id": user_id})
    if not user:
        return "user not found", 200

    # CREDIT WALLET
    users.update_one(
        {"telegram_id": user_id},
        {
            "$inc": {
                "wallet": amount,
                "total_earned": amount
            }
        }
    )

    conversions.insert_one({
        "subid": subid,
        "amount": amount
    })

    # NOTIFY USER
    bot.send_message(
        chat_id=user_id,
        text=f"🎉 Congratulations!\n₹{amount} credited from affiliate offer ✅"
    )

    return "ok", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
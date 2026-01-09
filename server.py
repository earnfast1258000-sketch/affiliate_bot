from flask import request
from pymongo import MongoClient

MONGO_URI = "PASTE_YOUR_MONGODB_URI_HERE"
client = MongoClient(MONGO_URI)
db = client["affiliate_bot"]
users = db["users"]
postbacks = db["postbacks"]

@app.route("/postback")
def postback():
    user_id = request.args.get("user_id")
    amount = request.args.get("amount")
    txid = request.args.get("txid")

    if not user_id or not amount or not txid:
        return "missing params", 400

    # duplicate check
    if postbacks.find_one({"txid": txid}):
        return "duplicate", 200

    amount = float(amount)

    users.update_one(
        {"telegram_id": int(user_id)},
        {
            "$inc": {
                "wallet": amount,
                "total_earned": amount
            }
        },
        upsert=True
    )

    postbacks.insert_one({
        "txid": txid,
        "user_id": int(user_id),
        "amount": amount
    })

    return "ok", 200
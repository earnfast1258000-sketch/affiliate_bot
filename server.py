import os
from flask import Flask, request
from pymongo import MongoClient

app = Flask(__name__)

# Mongo config
MONGO_URI = os.getenv("MONGO_URI")
client = MongoClient(MONGO_URI)
db = client["affiliate_bot"]
users = db["users"]
postbacks = db["postbacks"]

@app.route("/")
def home():
    return "Server running", 200

@app.route("/postback")
def postback():
    user_id = request.args.get("user_id")
    amount = request.args.get("amount")
    txid = request.args.get("txid")

    if not user_id or not amount or not txid:
        return "missing params", 400

    if postbacks.find_one({"txid": txid}):
        return "duplicate", 200

    try:
        user_id = int(user_id)
        amount = float(amount)
    except:
        return "invalid params", 400

    users.update_one(
        {"telegram_id": user_id},
        {"$inc": {"wallet": amount, "total_earned": amount}},
        upsert=True
    )

    postbacks.insert_one({
        "txid": txid,
        "user_id": user_id,
        "amount": amount
    })

    return "ok", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
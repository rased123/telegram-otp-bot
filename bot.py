from flask import Flask, request
import requests

app = Flask(__name__)

BOT_TOKEN = "8373802559:AAGP6Qmd7EIgKD40xkGwBFC_HwyME8BdKAo"
CHAT_ID = "-1002827157746"

@app.route("/")
def home():
    return "Telegram OTP Bot is Running!", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json or {}

    service = data.get("service", "Unknown")
    number = data.get("number", "Unknown")
    country = data.get("country", "Unknown")
    code = data.get("code", "Unknown")
    msg = data.get("message", "")
    time = data.get("time", "")

    text = f"""
🔔 {country} {service} Otp Code Received Successfully.

🕓 Time: {time}
📞 Number: {number}
🌍 Country: {country}
💬 Service: {service}
🔢 Otp Code: {code}
📝 Message:
`{msg}`
"""

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"})

    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)

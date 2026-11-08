import os
import json
import requests
import telebot

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# HOSHIWAR AI SETTINGS
BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
PORT = int(os.getenv("PORT", "10000"))
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing")

if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY is missing")

if not RENDER_URL:
    raise ValueError("RENDER_EXTERNAL_URL is missing")

bot = telebot.TeleBot(BOT_TOKEN)

WEBHOOK_PATH = "/telegram-webhook"
WEBHOOK_URL = RENDER_URL.rstrip("/") + WEBHOOK_PATH

# Telegram secret token
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

if not WEBHOOK_SECRET:
    raise ValueError("WEBHOOK_SECRET is missing")

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/"
    "models/gemini-2.5-flash:generateContent"
)

SYSTEM_PROMPT = """
تو هشیوار هستی؛ یک دستیار هوش مصنوعی فارسی‌زبان،
باهوش، مودب، صمیمی و دقیق.
به زبان کاربر پاسخ بده.
پاسخ‌هایت روشن، مفید و قابل فهم باشند.
از ایموجی‌های شیک و به‌اندازه استفاده کن.
تم بصری هشیوار آبی، نقره‌ای و سفید است.
"""


def ask_ai(message):
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY
    }

    data = {
        "systemInstruction": {
            "parts": [{"text": SYSTEM_PROMPT}]
        },
        "contents": [{
            "role": "user",
            "parts": [{"text": message}]
        }],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 2048
        }
    }

    response = requests.post(
        GEMINI_URL,
        headers=headers,
        json=data,
        timeout=60
    )

    response.raise_for_status()
    result = response.json()

    return result["candidates"][0]["content"]["parts"][0]["text"]


@bot.message_handler(commands=["start"])
def start(message):
    bot.reply_to(
        message,
        "سلام! 👋\n\n"
        "به هشیوار خوش اومدی. 💙\n"
        "دستیار هوش مصنوعی تو آماده‌ست.\n\n"
        "هر سؤالی داری بپرس؛\n"
        "با هم بهترین جواب رو پیدا می‌کنیم. ✨"
    )


@bot.message_handler(commands=["help"])
def help_command(message):
    bot.reply_to(
        message,
        "🤖 راهنمای هشیوار\n\n"
        "💬 سؤالت رو به‌صورت پیام بفرست.\n"
        "✨ هشیوار با هوش مصنوعی بهت پاسخ می‌ده.\n\n"
        "برای شروع از /start استفاده کن."
    )


@bot.message_handler(func=lambda message: bool(message.text))
def chat(message):
    waiting = bot.reply_to(
        message,
        "⏳ هشیوار در حال پردازش سؤالت..."
    )

    try:
        answer = ask_ai(message.text)

        for i in range(0, len(answer), 4000):
            bot.send_message(
                message.chat.id,
                answer[i:i + 4000]
            )

    except requests.exceptions.Timeout:
        bot.send_message(
            message.chat.id,
            "⏳ پاسخ کمی طول کشید. لطفاً دوباره تلاش کن."
        )

    except requests.exceptions.RequestException as error:
        print("GEMINI ERROR:", error)
        bot.send_message(
            message.chat.id,
            "⚠️ ارتباط با سرویس هوش مصنوعی برقرار نشد."
        )

    except Exception as error:
        print("ERROR:", error)
        bot.send_message(
            message.chat.id,
            "⚠️ مشکلی پیش اومد. لطفاً دوباره تلاش کن."
        )

    finally:
        try:
            bot.delete_message(
                message.chat.id,
                waiting.message_id
            )
        except Exception:
            pass


class WebhookHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Hoshiwar AI is running!")
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path != WEBHOOK_PATH:
            self.send_error(404)
            return

        received_secret = self.headers.get(
            "X-Telegram-Bot-Api-Secret-Token"
        )

        if received_secret != WEBHOOK_SECRET:
            self.send_error(403)
            return

        try:
            length = int(
                self.headers.get("Content-Length", 0)
            )

            body = self.rfile.read(length)
            data = json.loads(body.decode("utf-8"))

            update = telebot.types.Update.de_json(data)
            bot.process_new_updates([update])

            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

        except Exception as error:
            print("WEBHOOK ERROR:", error)
            self.send_error(500)

    def log_message(self, format, *args):
        print("WEB:", format % args)


if __name__ == "__main__":

    print("Starting Hoshiwar AI...")

    bot.set_webhook(
        url=WEBHOOK_URL,
        secret_token=WEBHOOK_SECRET
    )

    print("Webhook:", WEBHOOK_URL)

    server = ThreadingHTTPServer(
        ("0.0.0.0", PORT),
        WebhookHandler
    )

    print("Hoshiwar AI is running!")

    server.serve_forever()

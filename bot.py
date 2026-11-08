import os
import json
import re
import requests
import telebot

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# =================================
# HOSHIWAR AI CONFIGURATION
# =================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")
PORT = int(os.getenv("PORT", "10000"))

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing")

if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY is missing")

if not WEBHOOK_SECRET:
    raise ValueError("WEBHOOK_SECRET is missing")

if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", WEBHOOK_SECRET):
    raise ValueError("WEBHOOK_SECRET has illegal characters")

if not RENDER_URL:
    raise ValueError("RENDER_EXTERNAL_URL is missing")

bot = telebot.TeleBot(BOT_TOKEN)

WEBHOOK_PATH = "/telegram-webhook"
WEBHOOK_URL = RENDER_URL.rstrip("/") + WEBHOOK_PATH

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
"""

# =================================
# GEMINI AI
# =================================

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

    if not response.ok:
        print("GEMINI STATUS:", response.status_code)
        print("GEMINI RESPONSE:", response.text[:3000])
        response.raise_for_status()

    result = response.json()

    candidates = result.get("candidates", [])

    if not candidates:
        print("GEMINI EMPTY RESPONSE:", result)
        raise ValueError("Gemini returned no candidates")

    parts = candidates[0].get("content", {}).get("parts", [])

    answer = "".join(
        part.get("text", "")
        for part in parts
    ).strip()

    if not answer:
        raise ValueError("Gemini returned empty text")

    return answer


# =================================
# TELEGRAM COMMANDS
# =================================

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


# =================================
# AI CHAT
# =================================

@bot.message_handler(
    func=lambda message: bool(message.text)
)
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

        print("GEMINI ERROR: Request timed out")

        bot.send_message(
            message.chat.id,
            "⏳ پاسخ کمی طول کشید. دوباره تلاش کن."
        )

    except requests.exceptions.HTTPError as error:

        print("GEMINI HTTP ERROR:", error)

        status = error.response.status_code

        if status == 400:
            text = "⚠️ درخواست هوش مصنوعی نامعتبر بود."

        elif status == 401 or status == 403:
            text = "🔑 کلید API یا دسترسی هوش مصنوعی مشکل دارد."

        elif status == 429:
            text = "⏳ محدودیت درخواست هوش مصنوعی پر شده است."

        elif status >= 500:
            text = "🔧 سرور هوش مصنوعی موقتاً مشکل دارد."

        else:
            text = "⚠️ ارتباط با هوش مصنوعی برقرار نشد."

        bot.send_message(message.chat.id, text)

    except requests.exceptions.RequestException as error:

        print("GEMINI CONNECTION ERROR:", repr(error))

        bot.send_message(
            message.chat.id,
            "🌐 خطا در اتصال به سرویس هوش مصنوعی."
        )

    except Exception as error:

        print("CHAT ERROR:", repr(error))

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


# =================================
# WEBHOOK SERVER
# =================================

class WebhookHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        if self.path == "/":

            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                b"Hoshiwar AI is running!"
            )

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

            if length <= 0 or length > 1024 * 1024:
                self.send_error(400)
                return

            body = self.rfile.read(length)

            data = json.loads(body.decode("utf-8"))

            update = telebot.types.Update.de_json(data)

            bot.process_new_updates([update])

            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

        except Exception as error:

            print("WEBHOOK ERROR:", repr(error))
            self.send_error(500)

    def log_message(self, format, *args):
        print("WEB:", format % args)


# =================================
# RENDER START
# =================================

if __name__ == "__main__":

    print("Starting Hoshiwar AI...")

    server = ThreadingHTTPServer(
        ("0.0.0.0", PORT),
        WebhookHandler
    )

    bot.set_webhook(
        url=WEBHOOK_URL,
        secret_token=WEBHOOK_SECRET
    )

    print("Webhook:", WEBHOOK_URL)
    print("Hoshiwar AI is running!")

    try:
        server.serve_forever()

    finally:
        server.server_close()

import os
import json
import re
import threading
import requests
import telebot

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# =================================
# HOSHIVAR AI CONFIGURATION
# =================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")
PORT = int(os.getenv("PORT", "10000"))

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-2.5-flash"
)

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing")

if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY is missing")

if not WEBHOOK_SECRET:
    raise ValueError("WEBHOOK_SECRET is missing")

if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", WEBHOOK_SECRET):
    raise ValueError("Invalid WEBHOOK_SECRET")

if not RENDER_URL:
    raise ValueError("RENDER_EXTERNAL_URL is missing")

bot = telebot.TeleBot(BOT_TOKEN)

WEBHOOK_PATH = "/telegram-webhook"
WEBHOOK_URL = RENDER_URL.rstrip("/") + WEBHOOK_PATH

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/"
    f"models/{GEMINI_MODEL}:generateContent"
)

SYSTEM_PROMPT = """
تو هشیوار هستی؛ یک دستیار هوش مصنوعی فارسی‌زبان
باهوش، دقیق، مودب، صمیمی و حرفه‌ای.

قوانین تو:
- به زبان کاربر پاسخ بده.
- فارسی را روان و طبیعی بنویس.
- پاسخ دقیق و کاربردی بده.
- در صورت نیاز توضیح مرحله‌به‌مرحله ارائه کن.
- در برنامه‌نویسی، کد صحیح و قابل اجرا ارائه بده.
- اگر پاسخ چیزی را نمی‌دانی، صادقانه بگو.
- از ایموجی به‌اندازه استفاده کن.
- خودت را هشیوار معرفی کن.
"""

# =================================
# CHAT MEMORY
# =================================

chat_memory = {}
memory_lock = threading.Lock()

MAX_HISTORY = 12


def get_history(key):

    with memory_lock:
        return list(chat_memory.get(key, []))


def save_history(key, user_message, ai_answer):

    with memory_lock:

        history = chat_memory.get(key, [])

        history.append({
            "role": "user",
            "parts": [{"text": user_message}]
        })

        history.append({
            "role": "model",
            "parts": [{"text": ai_answer}]
        })

        chat_memory[key] = history[-MAX_HISTORY:]


def clear_history(key):

    with memory_lock:
        chat_memory.pop(key, None)


# =================================
# GEMINI ERROR
# =================================

class GeminiAPIError(Exception):

    def __init__(self, status, detail):

        self.status = status
        self.detail = detail

        super().__init__(
            f"Gemini API Error {status}: {detail}"
        )


# =================================
# GEMINI AI ENGINE
# =================================

def ask_ai(message, memory_key):

    history = get_history(memory_key)

    contents = history + [{
        "role": "user",
        "parts": [{"text": message}]
    }]

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY
    }

    data = {
        "systemInstruction": {
            "parts": [{"text": SYSTEM_PROMPT}]
        },
        "contents": contents,
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 2048
        }
    }

    try:

        response = requests.post(
            GEMINI_URL,
            headers=headers,
            json=data,
            timeout=(15, 90)
        )

    except requests.exceptions.RequestException as error:

        print("GEMINI NETWORK ERROR:", repr(error))
        raise

    if not response.ok:

        try:
            error_data = response.json()
            detail = error_data.get(
                "error", {}
            ).get("message", response.text[:2000])

        except ValueError:
            detail = response.text[:2000]

        print("GEMINI HTTP STATUS:", response.status_code)
        print("GEMINI ERROR DETAIL:", detail)

        raise GeminiAPIError(
            response.status_code,
            detail
        )

    try:
        result = response.json()

    except ValueError:

        raise ValueError("Gemini returned invalid JSON")

    candidates = result.get("candidates", [])

    if not candidates:

        print(
            "GEMINI EMPTY CANDIDATES:",
            json.dumps(result, ensure_ascii=False)[:3000]
        )

        block_reason = result.get(
            "promptFeedback", {}
        ).get("blockReason")

        if block_reason:
            raise ValueError(
                f"Gemini blocked prompt: {block_reason}"
            )

        raise ValueError("Gemini returned no candidates")

    candidate = candidates[0]

    parts = candidate.get(
        "content", {}
    ).get("parts", [])

    answer = "".join(
        part.get("text", "")
        for part in parts
    ).strip()

    if not answer:

        print(
            "GEMINI EMPTY TEXT:",
            json.dumps(result, ensure_ascii=False)[:3000]
        )

        raise ValueError("Gemini returned empty text")

    save_history(
        memory_key,
        message,
        answer
    )

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
        "با هم بهترین جواب رو پیدا می‌کنیم. ✨\n\n"
        "برای پاک کردن حافظه گفتگو:\n"
        "/reset"
    )


@bot.message_handler(commands=["help"])
def help_command(message):

    bot.reply_to(
        message,
        "🤖 راهنمای هشیوار\n\n"
        "💬 هر سؤالی داری بفرست.\n"
        "🧠 هشیوار پیام‌های اخیر گفتگو را به خاطر می‌سپارد.\n"
        "🔄 برای پاک کردن حافظه از /reset استفاده کن."
    )


@bot.message_handler(commands=["reset"])
def reset_command(message):

    key = (
        message.chat.id,
        message.from_user.id
    )

    clear_history(key)

    bot.reply_to(
        message,
        "🧠 حافظه این گفتگو پاک شد.\n"
        "از اینجا دوباره شروع می‌کنیم! 💙"
    )


# =================================
# TELEGRAM AI CHAT
# =================================

@bot.message_handler(
    content_types=["text"],
    func=lambda message: bool(message.text)
)
def chat(message):

    waiting = bot.reply_to(
        message,
        "⏳ هشیوار داره فکر می‌کنه..."
    )

    memory_key = (
        message.chat.id,
        message.from_user.id
    )

    try:

        answer = ask_ai(
            message.text,
            memory_key
        )

        for i in range(0, len(answer), 4000):

            bot.send_message(
                message.chat.id,
                answer[i:i + 4000]
            )

    except GeminiAPIError as error:

        status = error.status

        if status in (400, 401, 403, 404):

            text = (
                "🔑 مشکل در تنظیمات یا دسترسی هوش مصنوعی.\n"
                "مدیر ربات باید Logs را بررسی کند."
            )

        elif status == 429:

            text = (
                "⏳ سهمیه یا محدودیت درخواست Gemini پر شده.\n"
                "کمی بعد دوباره امتحان کن."
            )

        elif status >= 500:

            text = (
                "🔧 سرور هوش مصنوعی موقتاً مشکل دارد."
            )

        else:

            text = (
                "⚠️ خطای سرویس هوش مصنوعی."
            )

        print("CHAT GEMINI ERROR:", repr(error))

        bot.send_message(
            message.chat.id,
            text
        )

    except requests.exceptions.Timeout:

        print("GEMINI TIMEOUT")

        bot.send_message(
            message.chat.id,
            "⏳ پاسخ طول کشید. لطفاً دوباره تلاش کن."
        )

    except requests.exceptions.RequestException as error:

        print("GEMINI CONNECTION ERROR:", repr(error))

        bot.send_message(
            message.chat.id,
            "🌐 اتصال به سرویس هوش مصنوعی برقرار نشد."
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

            data = json.loads(
                body.decode("utf-8")
            )

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

    print("================================")
    print("STARTING HOSHIWAR AI")
    print("MODEL:", GEMINI_MODEL)
    print("================================")

    server = ThreadingHTTPServer(
        ("0.0.0.0", PORT),
        WebhookHandler
    )

    bot.set_webhook(
        url=WEBHOOK_URL,
        secret_token=WEBHOOK_SECRET
    )

    print("WEBHOOK:", WEBHOOK_URL)
    print("HOSHIWAR AI IS LIVE!")

    try:

        server.serve_forever()

    finally:

        server.server_close()

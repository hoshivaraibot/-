import os
import re
import json
import time
import logging
import threading
import requests
import telebot

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


# ==================================================
# HOSHIVAR AI - CONFIGURATION
# ==================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.8-flash"
)

PORT = int(os.getenv("PORT", "10000"))

RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

WEBHOOK_PATH = "/telegram-webhook"

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

WEBHOOK_URL = RENDER_URL.rstrip("/") + WEBHOOK_PATH

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/"
    f"models/{GEMINI_MODEL}:generateContent"
)


# ==================================================
# LOGGING
# ==================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

logger = logging.getLogger("HOSHIVAR")


# ==================================================
# TELEGRAM BOT
# ==================================================

bot = telebot.TeleBot(BOT_TOKEN)


# ==================================================
# HOSHIVAR PERSONALITY
# ==================================================

SYSTEM_PROMPT = """
تو هشیوار هستی؛ یک دستیار هوش مصنوعی فارسی‌زبان،
باهوش، دقیق، مودب، صمیمی و حرفه‌ای.

قوانین تو:

- به زبان کاربر پاسخ بده.
- فارسی را روان و طبیعی بنویس.
- پاسخ دقیق و کاربردی ارائه کن.
- در صورت نیاز توضیحات مرحله‌به‌مرحله بده.
- در برنامه‌نویسی کد صحیح و قابل اجرا ارائه کن.
- اگر چیزی را نمی‌دانی، صادقانه بگو.
- از ایموجی به اندازه استفاده کن.
- خودت را هشیوار معرفی کن.
- با کاربران محترمانه و دوستانه صحبت کن.
- از قلب قرمز ❤️ در موقعیت‌های مناسب استفاده کن.
"""


# ==================================================
# CHAT MEMORY
# ==================================================

chat_memory = {}
memory_lock = threading.Lock()

MAX_HISTORY = 10

chat_locks = {}
chat_locks_lock = threading.Lock()


def get_chat_lock(key):

    with chat_locks_lock:

        if key not in chat_locks:
            chat_locks[key] = threading.Lock()

        return chat_locks[key]


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

        chat_memory[key] = history[-MAX_HISTORY * 2:]


def clear_history(key):

    with memory_lock:
        chat_memory.pop(key, None)


# ==================================================
# GEMINI ERROR HANDLING
# ==================================================

class GeminiAPIError(Exception):

    def __init__(self, status, detail):

        self.status = status
        self.detail = detail

        super().__init__(
            f"Gemini HTTP {status}: {detail}"
        )


# ==================================================
# GEMINI QUOTA PROTECTION
# ==================================================

quota_lock = threading.Lock()

gemini_blocked_until = 0.0


def get_retry_delay(detail):

    match = re.search(
        r"retry in (?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?",
        detail,
        re.IGNORECASE
    )

    if not match:
        return 60

    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = float(match.group(3) or 0)

    return max(
        60,
        int(hours * 3600 + minutes * 60 + seconds)
    )


def activate_quota_cooldown(detail):

    global gemini_blocked_until

    delay = get_retry_delay(detail)

    with quota_lock:

        gemini_blocked_until = max(
            gemini_blocked_until,
            time.time() + delay
        )

    logger.warning(
        "Gemini cooldown activated for %s seconds",
        delay
    )


def quota_remaining():

    with quota_lock:

        return max(
            0,
            int(gemini_blocked_until - time.time())
        )


def format_wait_time(seconds):

    hours, remainder = divmod(
        max(0, seconds),
        3600
    )

    minutes = remainder // 60

    if hours:
        return f"{hours} ساعت و {minutes} دقیقه"

    if minutes:
        return f"{minutes} دقیقه"

    return "کمتر از یک دقیقه"


# ==================================================
# GEMINI AI ENGINE
# ==================================================

def ask_ai(message, memory_key):

    # Stop requests while quota cooldown is active.
    remaining = quota_remaining()

    if remaining > 0:

        raise GeminiAPIError(
            429,
            "Gemini quota cooldown is active."
        )

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY
    }

    history = get_history(memory_key)

    contents = history + [{
        "role": "user",
        "parts": [{"text": message}]
    }]

    payload = {

        "systemInstruction": {
            "parts": [
                {"text": SYSTEM_PROMPT}
            ]
        },

        "contents": contents,

        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 1024
        }
    }

    # Only one retry for temporary errors.
    # Never retry HTTP 429.

    max_attempts = 2

    response = None

    for attempt in range(max_attempts):

        try:

            logger.info(
                "Gemini request attempt %s/%s",
                attempt + 1,
                max_attempts
            )

            logger.info(
                "Gemini model: %s",
                GEMINI_MODEL
            )

            response = requests.post(
                GEMINI_URL,
                headers=headers,
                json=payload,
                timeout=(20, 90)
            )

            # Quota or rate limit error.
            if response.status_code == 429:

                try:

                    error_data = response.json()

                    detail = error_data.get(
                        "error", {}
                    ).get(
                        "message",
                        response.text[:2000]
                    )

                except ValueError:

                    detail = response.text[:2000]

                activate_quota_cooldown(detail)

                logger.error(
                    "GEMINI HTTP 429: %s",
                    detail
                )

                raise GeminiAPIError(
                    429,
                    detail
                )

            # Temporary server errors.
            if response.status_code in (
                500,
                502,
                503,
                504
            ):

                if attempt + 1 < max_attempts:

                    logger.warning(
                        "Gemini temporary HTTP error: %s",
                        response.status_code
                    )

                    time.sleep(2)

                    continue

            break

        except GeminiAPIError:
            raise

        except (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError
        ):

            logger.exception(
                "GEMINI CONNECTION/TIMEOUT ERROR"
            )

            if attempt + 1 >= max_attempts:
                raise

            logger.warning(
                "Retrying Gemini once..."
            )

            time.sleep(2)

        except requests.exceptions.RequestException:

            logger.exception(
                "GEMINI NETWORK ERROR"
            )

            raise

    if response is None:

        raise RuntimeError(
            "Gemini request failed"
        )

    logger.info(
        "GEMINI HTTP STATUS: %s",
        response.status_code
    )

    # Handle other API errors.
    if not response.ok:

        try:

            error_data = response.json()

            detail = error_data.get(
                "error", {}
            ).get(
                "message",
                response.text[:2000]
            )

        except ValueError:

            detail = response.text[:2000]

        logger.error(
            "GEMINI ERROR DETAIL: %s",
            detail
        )

        raise GeminiAPIError(
            response.status_code,
            detail
        )

    # Parse Gemini response.
    try:

        result = response.json()

    except ValueError:

        raise ValueError(
            "Invalid Gemini JSON"
        )

    candidates = result.get(
        "candidates",
        []
    )

    if not candidates:

        block_reason = result.get(
            "promptFeedback", {}
        ).get("blockReason")

        logger.error(
            "GEMINI EMPTY CANDIDATES: %s",
            json.dumps(
                result,
                ensure_ascii=False
            )[:3000]
        )

        raise ValueError(
            f"Gemini returned no candidates: {block_reason}"
        )

    candidate = candidates[0]

    parts = candidate.get(
        "content", {}
    ).get(
        "parts", []
    )

    answer = "".join(
        part.get("text", "")
        for part in parts
    ).strip()

    if not answer:

        raise ValueError(
            "Gemini returned empty text"
        )

    # Save conversation only after success.
    save_history(
        memory_key,
        message,
        answer
    )

    logger.info(
        "Gemini response received successfully"
    )

    return answer


# ==================================================
# TELEGRAM MESSAGE SENDER
# ==================================================

def send_long_message(chat_id, text):

    max_length = 4000

    for start in range(
        0,
        len(text),
        max_length
    ):

        chunk = text[
            start:start + max_length
        ]

        bot.send_message(
            chat_id,
            chunk
        )


# ==================================================
# TELEGRAM COMMANDS
# ==================================================

@bot.message_handler(commands=["start"])
def start(message):

    bot.reply_to(
        message,

        "سلام! 👋\n\n"
        "به هشیوار خوش اومدی. ❤️\n\n"
        "من دستیار هوش مصنوعی تو هستم.\n"
        "هر سؤالی داری بپرس؛ با هم بهترین جواب رو پیدا می‌کنیم. ✨\n\n"
        "برای راهنما: /help\n"
        "برای پاک کردن حافظه: /reset"
    )


@bot.message_handler(commands=["help"])
def help_command(message):

    bot.reply_to(
        message,

        "🤖 راهنمای هشیوار\n\n"
        "💬 هر سؤالی داری بفرست.\n"
        "🧠 حافظه مکالمات فعال است.\n"
        "🔄 برای پاک کردن حافظه از /reset استفاده کن."
    )


@bot.message_handler(commands=["reset"])
def reset_command(message):

    memory_key = str(
        message.chat.id
    )

    clear_history(memory_key)

    bot.reply_to(
        message,
        "🔄 حافظه گفتگو پاک شد. ❤️"
    )


# ==================================================
# TELEGRAM AI CHAT
# ==================================================

@bot.message_handler(content_types=["text"])
def chat(message):

    memory_key = str(
        message.chat.id
    )

    waiting = bot.send_message(
        message.chat.id,
        "⏳ هشیوار داره فکر می‌کنه..."
    )

    try:

        with get_chat_lock(memory_key):

            answer = ask_ai(
                message.text,
                memory_key
            )

        send_long_message(
            message.chat.id,
            answer
        )

    except GeminiAPIError as error:

        logger.exception(
            "CHAT GEMINI ERROR"
        )

        status = error.status

        if status in (400, 401, 403, 404):

            text = (
                "⚠️ خطای دسترسی یا درخواست نامعتبر به هوش مصنوعی.\n"
                "مدیر ربات باید تنظیمات API را بررسی کند."
            )

        elif status == 429:

            remaining = quota_remaining()

            if remaining > 0:

                text = (
                    "⏳ سهمیه رایگان Gemini هشیوار فعلاً تمام شده.\n"
                    f"زمان تقریبی بازگشت پاسخ‌گویی: "
                    f"{format_wait_time(remaining)} دیگر.\n"
                    "تا آن زمان درخواست اضافی ارسال نمی‌کنم. ❤️"
                )

            else:

                text = (
                    "⏳ Gemini موقتاً محدودیت درخواست اعمال کرده.\n"
                    "لطفاً یک دقیقه بعد دوباره تلاش کن. ❤️"
                )

        elif status >= 500:

            text = (
                "🔥 سرویس Gemini با مشکل روبه‌رو شده.\n"
                "لطفاً کمی بعد تلاش کن."
            )

        else:

            text = (
                "⚠️ خطایی در سرویس هوش مصنوعی رخ داده است."
            )

        bot.send_message(
            message.chat.id,
            text
        )

    except requests.exceptions.Timeout:

        logger.exception(
            "GEMINI TIMEOUT"
        )

        bot.send_message(
            message.chat.id,

            "⏳ پاسخ هوش مصنوعی بیش از حد طول کشید.\n"
            "لطفاً کمی بعد دوباره تلاش کن. ❤️"
        )

    except requests.exceptions.RequestException:

        logger.exception(
            "GEMINI CONNECTION ERROR"
        )

        bot.send_message(
            message.chat.id,

            "🌐 اتصال به سرویس هوش مصنوعی برقرار نشد."
        )

    except Exception:

        logger.exception(
            "CHAT ERROR"
        )

        bot.send_message(
            message.chat.id,

            "⚠️ خطای غیرمنتظره‌ای رخ داد.\n"
            "لطفاً دوباره تلاش کن."
        )

    finally:

        try:

            bot.delete_message(
                message.chat.id,
                waiting.message_id
            )

        except Exception:

            pass


# ==================================================
# WEBHOOK SERVER
# ==================================================

class WebhookHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        if self.path == "/":

            self.send_response(200)

            self.send_header(
                "Content-Type",
                "text/plain; charset=utf-8"
            )

            self.end_headers()

            self.wfile.write(
                b"Hoshivar AI Bot is active and running!"
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

            logger.warning(
                "Invalid webhook secret"
            )

            self.send_error(403)

            return

        try:

            content_length = int(
                self.headers.get(
                    "Content-Length",
                    0
                )
            )

            if (
                content_length <= 0
                or content_length > 1_000_000
            ):

                self.send_error(400)

                return

            post_data = self.rfile.read(
                content_length
            )

            data = post_data.decode(
                "utf-8"
            )

            update = telebot.types.Update.de_json(
                data
            )

            logger.info(
                "Telegram update received"
            )

            bot.process_new_updates(
                [update]
            )

            self.send_response(200)

            self.end_headers()

            self.wfile.write(
                b"OK"
            )

        except Exception:

            logger.exception(
                "WEBHOOK ERROR"
            )

            self.send_error(500)

    def log_message(
        self,
        format,
        *app_args
    ):

        logger.info(
            "WEBHOOK SERVER: %s",
            format % app_args
        )


# ==================================================
# START HOSHIVAR ON RENDER
# ==================================================

if __name__ == "__main__":

    logger.info(
        "================================"
    )

    logger.info(
        "STARTING HOSHIVAR AI"
    )

    logger.info(
        "MODEL: %s",
        GEMINI_MODEL
    )

    logger.info(
        "================================"
    )

    server = ThreadingHTTPServer(
        ("0.0.0.0", PORT),
        WebhookHandler
    )

    try:

        webhook_result = bot.set_webhook(
            url=WEBHOOK_URL,
            secret_token=WEBHOOK_SECRET,
            allowed_updates=["message"]
        )

        if not webhook_result:

            raise RuntimeError(
                "Telegram webhook setup failed"
            )

        logger.info(
            "TELEGRAM WEBHOOK SET SUCCESSFULLY"
        )

        logger.info(
            "WEBHOOK URL: %s",
            WEBHOOK_URL
        )

        logger.info(
            "HOSHIVAR AI IS LIVE!"
        )

        server.serve_forever()

    except Exception:

        logger.exception(
            "APPLICATION STARTUP ERROR"
        )

        raise

    finally:

        server.server_close()

        logger.info(
            "HOSHIVAR SERVER STOPPED"
        )

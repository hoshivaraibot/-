import os
import re
import json
import time
import logging
import threading
import requests
import telebot

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


# =========================================================
# HOSHIVAR AI - CONFIGURATION
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROK_API_KEY = os.getenv("GROK_API_KEY")

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
GROK_MODEL = os.getenv("GROK_MODEL", "grok-4.7")

PORT = int(os.getenv("PORT", "10000"))
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

WEBHOOK_PATH = "/telegram-webhook"

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing")

if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY is missing")

if not GROK_API_KEY:
    raise ValueError("GROK_API_KEY is missing")

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
GROK_URL = "https://api.x.ai/v1/responses"


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("HOSHIVAR")


# =========================================================
# TELEGRAM BOT
# =========================================================

bot = telebot.TeleBot(BOT_TOKEN)


# =========================================================
# HOSHIVAR PERSONALITY
# =========================================================

SYSTEM_PROMPT = """
تو هشیوار هستی؛ یک دستیار هوش مصنوعی فارسی‌زبان،
باهوش، دقیق، مودب، صمیمی و حرفه‌ای.

قوانین:
- به زبان کاربر پاسخ بده.
- فارسی را روان و طبیعی بنویس.
- پاسخ دقیق، کاربردی و منظم ارائه کن.
- در صورت نیاز توضیحات مرحله‌به‌مرحله بده.
- در برنامه‌نویسی کد صحیح و قابل اجرا ارائه کن.
- اگر چیزی را نمی‌دانی، صادقانه بگو و حدس را جای واقعیت نگذار.
- از ایموجی به اندازه استفاده کن.
- خودت را هشیوار معرفی کن.
- نام سرویس‌ها و مدل‌های پشتیبان را برای کاربر مطرح نکن.
- با کاربران محترمانه و دوستانه صحبت کن.
- از قلب قرمز ❤️ در موقعیت‌های مناسب استفاده کن.
"""


# =========================================================
# CHAT MEMORY AND LOCKS
# =========================================================

chat_memory = {}
memory_lock = threading.Lock()

chat_locks = {}
chat_locks_lock = threading.Lock()

MAX_HISTORY = 10


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


# =========================================================
# PROVIDER ERRORS AND GEMINI QUOTA COOLDOWN
# =========================================================

class ProviderAPIError(Exception):
    def __init__(self, provider, status, detail):
        self.provider = provider
        self.status = status
        self.detail = detail
        super().__init__(f"{provider} HTTP {status}: {detail}")


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

    return max(60, int(hours * 3600 + minutes * 60 + seconds))


def activate_gemini_cooldown(detail):
    global gemini_blocked_until
    delay = get_retry_delay(detail)

    with quota_lock:
        gemini_blocked_until = max(
            gemini_blocked_until,
            time.time() + delay
        )

    logger.warning("Primary provider cooldown activated for %s seconds", delay)


def gemini_cooldown_remaining():
    with quota_lock:
        return max(0, int(gemini_blocked_until - time.time()))


def extract_error_detail(response):
    try:
        data = response.json()
        error = data.get("error", {})
        if isinstance(error, dict):
            return str(error.get("message") or response.text[:1500])
        return response.text[:1500]
    except (ValueError, AttributeError):
        return response.text[:1500]


# =========================================================
# GEMINI - PRIMARY ENGINE
# =========================================================

def ask_gemini(message, memory_key):
    remaining = gemini_cooldown_remaining()
    if remaining > 0:
        raise ProviderAPIError(
            "primary",
            429,
            f"Quota cooldown active for {remaining} seconds"
        )

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY
    }

    contents = get_history(memory_key) + [{
        "role": "user",
        "parts": [{"text": message}]
    }]

    payload = {
        "systemInstruction": {
            "parts": [{"text": SYSTEM_PROMPT}]
        },
        "contents": contents,
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 2048
        }
    }

    response = None

    # Retry once only for transient server/network errors.
    for attempt in range(2):
        try:
            response = requests.post(
                GEMINI_URL,
                headers=headers,
                json=payload,
                timeout=(20, 90)
            )
        except (requests.exceptions.Timeout,
                requests.exceptions.ConnectionError):
            logger.exception("Primary AI connection/timeout error")
            if attempt == 0:
                time.sleep(2)
                continue
            raise

        if response.status_code == 429:
            detail = extract_error_detail(response)
            activate_gemini_cooldown(detail)
            raise ProviderAPIError("primary", 429, detail)

        if response.status_code in (500, 502, 503, 504) and attempt == 0:
            logger.warning("Primary AI temporary server error: %s",
                           response.status_code)
            time.sleep(2)
            continue

        break

    if response is None:
        raise RuntimeError("Primary AI returned no HTTP response")

    if not response.ok:
        detail = extract_error_detail(response)
        logger.error("Primary AI API error: %s", detail)
        raise ProviderAPIError("primary", response.status_code, detail)

    try:
        result = response.json()
    except ValueError:
        raise RuntimeError("Primary AI returned invalid JSON")

    candidates = result.get("candidates", [])
    if not candidates:
        block_reason = result.get("promptFeedback", {}).get("blockReason")
        raise RuntimeError(f"Primary AI returned no candidates: {block_reason}")

    parts = candidates[0].get("content", {}).get("parts", [])
    answer = "".join(
        part.get("text", "")
        for part in parts
        if isinstance(part, dict)
    ).strip()

    if not answer:
        raise RuntimeError("Primary AI returned empty text")

    logger.info("Primary AI response received")
    return answer


# =========================================================
# GROK - AUTOMATIC BACKUP ENGINE
# =========================================================

def ask_grok(message, memory_key):
    headers = {
        "Authorization": f"Bearer {GROK_API_KEY}",
        "Content-Type": "application/json"
    }

    grok_input = [{
        "role": "system",
        "content": SYSTEM_PROMPT
    }]

    for item in get_history(memory_key):
        role = "assistant" if item.get("role") == "model" else "user"
        content = "".join(
            part.get("text", "")
            for part in item.get("parts", [])
            if isinstance(part, dict)
        ).strip()

        if content:
            grok_input.append({
                "role": role,
                "content": content
            })

    grok_input.append({
        "role": "user",
        "content": message
    })

    payload = {
        "model": GROK_MODEL,
        "input": grok_input,
        "temperature": 0.7,
        "max_output_tokens": 2048
    }

    logger.warning("Automatic backup engine activated")

    response = requests.post(
        GROK_URL,
        headers=headers,
        json=payload,
        timeout=(20, 120)
    )

    if not response.ok:
        detail = extract_error_detail(response)
        logger.error("Backup AI API error: %s", detail)
        raise ProviderAPIError("backup", response.status_code, detail)

    try:
        result = response.json()
    except ValueError:
        raise RuntimeError("Backup AI returned invalid JSON")

    answer_parts = []
    for item in result.get("output", []):
        if item.get("type") != "message":
            continue
        for part in item.get("content", []):
            if part.get("type") == "output_text":
                answer_parts.append(part.get("text", ""))

    answer = "".join(answer_parts).strip()

    if not answer:
        logger.error(
            "Backup AI returned empty response: %s",
            json.dumps(result, ensure_ascii=False)[:1500]
        )
        raise RuntimeError("Backup AI returned empty text")

    logger.info("Backup AI response received")
    return answer


# =========================================================
# SMART FAILOVER - USERS SEE ONLY HOSHIVAR
# =========================================================

def ask_ai(message, memory_key):
    try:
        answer = ask_gemini(message, memory_key)

    except ProviderAPIError as error:
        # Switch only for quota/rate limits or temporary service failures.
        fallback_allowed = error.status in (408, 429, 500, 502, 503, 504)

        if not fallback_allowed:
            raise

        logger.warning(
            "Primary AI unavailable (HTTP %s); trying backup",
            error.status
        )
        answer = ask_grok(message, memory_key)

    except (requests.exceptions.Timeout,
            requests.exceptions.ConnectionError):
        logger.warning("Primary AI network unavailable; trying backup")
        answer = ask_grok(message, memory_key)

    save_history(memory_key, message, answer)
    return answer


# =========================================================
# TELEGRAM MESSAGE UTILITIES
# =========================================================

def send_long_message(chat_id, message_text):
    max_length = 3900

    for start in range(0, len(message_text), max_length):
        bot.send_message(
            chat_id,
            message_text[start:start + max_length]
        )


def send_generic_error(chat_id):
    bot.send_message(
        chat_id,
        "⚠️ هشیوار فعلاً نتونست پاسخ رو آماده کنه.\n"
        "لطفاً کمی بعد دوباره تلاش کن. ❤️"
    )


# =========================================================
# TELEGRAM COMMANDS
# =========================================================

@bot.message_handler(commands=["start"])
def start(message):
    bot.reply_to(
        message,
        "سلام! 👋\n\n"
        "به هشیوار خوش اومدی. ❤️\n\n"
        "من دستیار هوش مصنوعی تو هستم.\n"
        "هر سؤالی داری بپرس؛ با هم بهترین جواب رو پیدا می‌کنیم. ✨\n\n"
        "برای راهنما: /help\n"
        "برای پاک کردن حافظه گفتگو: /reset"
    )


@bot.message_handler(commands=["help"])
def help_command(message):
    bot.reply_to(
        message,
        "🤖 راهنمای هشیوار\n\n"
        "💬 هر سؤالی داری بفرست.\n"
        "🧠 حافظه گفتگو در زمان فعال بودن ربات نگهداری می‌شود.\n"
        "🔄 برای پاک کردن حافظه از /reset استفاده کن."
    )


@bot.message_handler(commands=["reset"])
def reset_command(message):
    clear_history(str(message.chat.id))
    bot.reply_to(message, "🔄 حافظه این گفتگو پاک شد. ❤️")


# =========================================================
# TELEGRAM AI CHAT
# =========================================================

@bot.message_handler(content_types=["text"])
def chat(message):
    memory_key = str(message.chat.id)
    waiting = bot.send_message(
        message.chat.id,
        "⏳ هشیوار داره فکر می‌کنه..."
    )

    try:
        with get_chat_lock(memory_key):
            answer = ask_ai(message.text, memory_key)

        send_long_message(message.chat.id, answer)

    except ProviderAPIError as error:
        logger.exception(
            "Both AI engines could not complete the request; last provider=%s",
            error.provider
        )
        send_generic_error(message.chat.id)

    except (requests.exceptions.Timeout,
            requests.exceptions.ConnectionError):
        logger.exception("AI network failure")
        send_generic_error(message.chat.id)

    except Exception:
        logger.exception("Unexpected chat handler error")
        send_generic_error(message.chat.id)

    finally:
        try:
            bot.delete_message(
                message.chat.id,
                waiting.message_id
            )
        except Exception:
            pass


# =========================================================
# WEBHOOK SERVER
# =========================================================

class WebhookHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path in ("/", "/health"):
            self.send_response(200)
            self.send_header(
                "Content-Type",
                "text/plain; charset=utf-8"
            )
            self.end_headers()
            self.wfile.write(b"Hoshiwar AI Bot is running")
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
            logger.warning("Rejected webhook with invalid secret")
            self.send_error(403)
            return

        try:
            content_length = int(
                self.headers.get("Content-Length", "0")
            )

            if content_length <= 0 or content_length > 1_000_000:
                self.send_error(400)
                return

            raw_data = self.rfile.read(content_length)
            update = telebot.types.Update.de_json(
                raw_data.decode("utf-8")
            )

            bot.process_new_updates([update])

            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

        except Exception:
            logger.exception("Webhook processing error")
            self.send_error(500)

    def log_message(self, format, *args):
        logger.info("WEBHOOK: %s", format % args)


# =========================================================
# START HOSHIVAR ON RENDER
# =========================================================

if __name__ == "__main__":
    logger.info("================================")
    logger.info("STARTING HOSHIVAR AI")
    logger.info("Primary model configured: %s", GEMINI_MODEL)
    logger.info("Automatic backup model configured: %s", GROK_MODEL)
    logger.info("================================")

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
            raise RuntimeError("Telegram webhook setup failed")

        logger.info("Telegram webhook configured successfully")
        logger.info("Hoshiwar is live")

        server.serve_forever()

    except Exception:
        logger.exception("Application startup error")
        raise

    finally:
        server.server_close()
        logger.info("Hoshiwar server stopped")

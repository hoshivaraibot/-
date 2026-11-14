import os
import re
import time
import logging
import threading
import requests
import telebot

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from telebot.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)

# =====================================================
# HOSHIVAR AI - CONFIGURATION
# =====================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROK_API_KEY = os.getenv("GROK_API_KEY")

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
GROK_MODEL = os.getenv("GROK_MODEL", "grok-4.7")

PORT = int(os.getenv("PORT", "10000"))
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

WEBHOOK_PATH = "/telegram-webhook"

MAX_WAIT = 45
MAX_HISTORY = 10
MAX_MESSAGE_LENGTH = 3900

for name, value in [
    ("BOT_TOKEN", BOT_TOKEN),
    ("GEMINI_API_KEY", GEMINI_API_KEY),
    ("GROK_API_KEY", GROK_API_KEY),
    ("WEBHOOK_SECRET", WEBHOOK_SECRET),
    ("RENDER_EXTERNAL_URL", RENDER_URL)
]:
    if not value:
        raise ValueError(f"{name} is missing")

if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", WEBHOOK_SECRET):
    raise ValueError("Invalid WEBHOOK_SECRET")

WEBHOOK_URL = RENDER_URL.rstrip("/") + WEBHOOK_PATH

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/"
    f"models/{GEMINI_MODEL}:generateContent"
)

GROK_URL = "https://api.x.ai/v1/responses"

# =====================================================
# LOGGING
# =====================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

logger = logging.getLogger("HOSHIVAR")

# =====================================================
# TELEGRAM
# =====================================================

bot = telebot.TeleBot(BOT_TOKEN)

# =====================================================
# HOSHIVAR PERSONALITY
# =====================================================

SYSTEM_PROMPT = """
تو هشیوار هستی؛ یک دستیار هوش مصنوعی فارسی‌زبان،
باهوش، دقیق، مودب، صمیمی و حرفه‌ای.

قوانین:
- به زبان کاربر پاسخ بده.
- فارسی را روان و طبیعی بنویس.
- پاسخ دقیق، کاربردی و منظم ارائه کن.
- در صورت نیاز توضیحات مرحله‌به‌مرحله بده.
- در برنامه‌نویسی کد صحیح و قابل اجرا ارائه کن.
- اگر چیزی را نمی‌دانی، صادقانه بگو.
- از ایموجی به اندازه استفاده کن.
- خودت را هشیوار معرفی کن.
- نام مدل‌ها و سرویس‌های پشتیبان را مطرح نکن.
- با کاربران محترمانه و دوستانه صحبت کن.
- از قلب قرمز ❤️ در موقعیت‌های مناسب استفاده کن.
"""

# =====================================================
# MENU BUTTONS
# =====================================================

BTN_CHAT = "💬 گفتگوی جدید"
BTN_IMAGE = "🎨 ساخت تصویر"
BTN_ACCOUNT = "👤 حساب کاربری"
BTN_HELP = "ℹ️ راهنما"
BTN_SETTINGS = "⚙️ تنظیمات"


def main_keyboard():
    keyboard = ReplyKeyboardMarkup(
        resize_keyboard=True,
        row_width=2,
        is_persistent=True
    )

    keyboard.row(
        KeyboardButton(BTN_CHAT),
        KeyboardButton(BTN_IMAGE)
    )

    keyboard.row(
        KeyboardButton(BTN_ACCOUNT),
        KeyboardButton(BTN_HELP)
    )

    keyboard.row(
        KeyboardButton(BTN_SETTINGS)
    )

    return keyboard


def main_inline_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)

    keyboard.row(
        InlineKeyboardButton(
            "💬 شروع گفتگو",
            callback_data="new_chat"
        )
    )

    keyboard.row(
        InlineKeyboardButton(
            "🎨 ساخت تصویر",
            callback_data="image"
        ),
        InlineKeyboardButton(
            "👤 حساب کاربری",
            callback_data="account"
        )
    )

    keyboard.row(
        InlineKeyboardButton(
            "ℹ️ راهنما",
            callback_data="help"
        ),
        InlineKeyboardButton(
            "⚙️ تنظیمات",
            callback_data="settings"
        )
    )

    return keyboard


def settings_keyboard():
    keyboard = InlineKeyboardMarkup()

    keyboard.add(
        InlineKeyboardButton(
            "🧹 پاک کردن حافظه گفتگو",
            callback_data="reset_memory"
        )
    )

    keyboard.add(
        InlineKeyboardButton(
            "🔙 بازگشت به منوی اصلی",
            callback_data="home"
        )
    )

    return keyboard


def send_home(chat_id):
    bot.send_message(
        chat_id,
        "سلام دوست من! 👋\n\n"
        "به هشیوار خوش اومدی. ❤️\n\n"
        "من دستیار هوش مصنوعی تو هستم.\n"
        "هر سؤالی داری بپرس؛ با هم بهترین جواب رو پیدا می‌کنیم. ✨",
        reply_markup=main_keyboard()
    )

    bot.send_message(
        chat_id,
        "از دکمه‌های شیشه‌ای زیر هم می‌تونی استفاده کنی:",
        reply_markup=main_inline_keyboard()
    )


# =====================================================
# CHAT MEMORY
# =====================================================

chat_memory = {}
memory_lock = threading.Lock()

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


# =====================================================
# RESPONSE DEADLINE
# =====================================================

class DeadlineExceeded(Exception):
    pass


def request_timeout(deadline):
    remaining = deadline - time.monotonic()

    if remaining <= 1:
        raise DeadlineExceeded("45 second deadline exceeded")

    connect_timeout = min(4.0, remaining / 4)
    read_timeout = max(1.0, remaining - connect_timeout - 0.5)

    return (connect_timeout, read_timeout)


def check_deadline(deadline):
    if time.monotonic() >= deadline:
        raise DeadlineExceeded("Response deadline exceeded")


# =====================================================
# API ERRORS AND GEMINI COOLDOWN
# =====================================================

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

    return max(
        60,
        int(hours * 3600 + minutes * 60 + seconds)
    )


def activate_gemini_cooldown(detail):
    global gemini_blocked_until

    delay = get_retry_delay(detail)

    with quota_lock:
        gemini_blocked_until = max(
            gemini_blocked_until,
            time.time() + delay
        )

    logger.warning("Gemini cooldown: %s seconds", delay)


def gemini_cooldown_remaining():
    with quota_lock:
        return max(
            0,
            int(gemini_blocked_until - time.time())
        )


def extract_error_detail(response):
    try:
        data = response.json()
        error = data.get("error", {})

        if isinstance(error, dict):
            return str(
                error.get("message") or response.text[:1500]
            )

        return response.text[:1500]

    except (ValueError, AttributeError):
        return response.text[:1500]


# =====================================================
# GEMINI - PRIMARY ENGINE
# =====================================================

def ask_gemini(message, memory_key, deadline):

    remaining = gemini_cooldown_remaining()

    if remaining > 0:
        raise ProviderAPIError(
            "primary",
            429,
            f"Quota cooldown: {remaining} seconds"
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

    response = requests.post(
        GEMINI_URL,
        headers=headers,
        json=payload,
        timeout=request_timeout(deadline)
    )

    check_deadline(deadline)

    if response.status_code == 429:
        detail = extract_error_detail(response)
        activate_gemini_cooldown(detail)

        raise ProviderAPIError(
            "primary",
            429,
            detail
        )

    if not response.ok:
        raise ProviderAPIError(
            "primary",
            response.status_code,
            extract_error_detail(response)
        )

    result = response.json()

    candidates = result.get("candidates", [])

    if not candidates:
        raise RuntimeError("Gemini returned no candidates")

    parts = candidates[0].get(
        "content", {}
    ).get("parts", [])

    answer = "".join(
        part.get("text", "")
        for part in parts
        if isinstance(part, dict)
    ).strip()

    if not answer:
        raise RuntimeError("Gemini returned empty text")

    logger.info("Primary response received")

    return answer


# =====================================================
# GROK - AUTOMATIC BACKUP ENGINE
# =====================================================

def ask_grok(message, memory_key, deadline):

    headers = {
        "Authorization": f"Bearer {GROK_API_KEY}",
        "Content-Type": "application/json"
    }

    grok_input = [{
        "role": "system",
        "content": SYSTEM_PROMPT
    }]

    for item in get_history(memory_key):

        role = (
            "assistant"
            if item.get("role") == "model"
            else "user"
        )

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

    logger.warning("Automatic backup activated")

    response = requests.post(
        GROK_URL,
        headers=headers,
        json=payload,
        timeout=request_timeout(deadline)
    )

    check_deadline(deadline)

    if not response.ok:
        raise ProviderAPIError(
            "backup",
            response.status_code,
            extract_error_detail(response)
        )

    result = response.json()

    answer_parts = []

    for item in result.get("output", []):

        if item.get("type") != "message":
            continue

        for part in item.get("content", []):

            if part.get("type") == "output_text":
                answer_parts.append(part.get("text", ""))

    answer = "".join(answer_parts).strip()

    if not answer:
        raise RuntimeError("Grok returned empty text")

    logger.info("Backup response received")

    return answer


# =====================================================
# AUTOMATIC AI - HIDDEN MODEL SELECTION
# =====================================================

def ask_ai(message, memory_key, deadline):

    try:
        answer = ask_gemini(
            message,
            memory_key,
            deadline
        )

    except DeadlineExceeded:
        raise

    except Exception:
        logger.exception(
            "Primary engine failed; trying automatic backup"
        )

        check_deadline(deadline)

        answer = ask_grok(
            message,
            memory_key,
            deadline
        )

    check_deadline(deadline)

    save_history(
        memory_key,
        message,
        answer
    )

    return answer


# =====================================================
# TELEGRAM MESSAGE UTILITIES
# =====================================================

def send_long_message(chat_id, text):
    for start in range(0, len(text), MAX_MESSAGE_LENGTH):
        bot.send_message(
            chat_id,
            text[start:start + MAX_MESSAGE_LENGTH]
        )


def send_generic_error(chat_id):
    bot.send_message(
        chat_id,
        "⚠️ هشیوار فعلاً نتونست پاسخ رو آماده کنه.\n"
        "لطفاً کمی بعد دوباره تلاش کن. ❤️",
        reply_markup=main_keyboard()
    )


def show_help(chat_id):
    bot.send_message(
        chat_id,
        "🤖 راهنمای هشیوار\n\n"
        "💬 هر سؤالی داری بفرست تا پاسخ بدم.\n"
        "🧠 حافظه گفتگو در زمان فعال بودن ربات نگهداری می‌شه.\n"
        "🔄 برای شروع گفتگوی تازه از دکمه گفتگوی جدید استفاده کن.\n"
        "🧹 برای پاک کردن حافظه هم وارد تنظیمات شو. ❤️",
        reply_markup=main_keyboard()
    )


def show_settings(chat_id):
    bot.send_message(
        chat_id,
        "⚙️ تنظیمات هشیوار\n\n"
        "از گزینه زیر برای مدیریت حافظه گفتگو استفاده کن.",
        reply_markup=settings_keyboard()
    )


def show_account(message):
    bot.send_message(
        message.chat.id,
        "👤 حساب کاربری\n\n"
        f"شناسه کاربری: {message.from_user.id}\n"
        f"نام: {message.from_user.first_name or 'کاربر'}\n\n"
        "از همراهی تو با هشیوار خوشحالیم. ❤️",
        reply_markup=main_keyboard()
    )


def show_image_notice(chat_id):
    bot.send_message(
        chat_id,
        "🎨 قابلیت ساخت تصویر هشیوار هنوز فعال نشده.\n\n"
        "به‌زودی این قابلیت هم به امکانات ما اضافه می‌شه. ❤️",
        reply_markup=main_keyboard()
    )


# =====================================================
# TELEGRAM COMMANDS
# =====================================================

@bot.message_handler(commands=["start"])
def start(message):
    send_home(message.chat.id)


@bot.message_handler(commands=["help"])
def help_command(message):
    show_help(message.chat.id)


@bot.message_handler(commands=["reset"])
def reset_command(message):
    clear_history(str(message.chat.id))

    bot.send_message(
        message.chat.id,
        "🔄 حافظه این گفتگو پاک شد. ❤️",
        reply_markup=main_keyboard()
    )


# =====================================================
# INLINE BUTTON CALLBACKS
# =====================================================

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):

    try:
        bot.answer_callback_query(call.id)

        chat_id = call.message.chat.id
        key = str(chat_id)
        action = call.data

        if action in ("home", "new_chat"):
            if action == "new_chat":
                clear_history(key)

            send_home(chat_id)

        elif action == "help":
            show_help(chat_id)

        elif action == "settings":
            show_settings(chat_id)

        elif action == "account":
            bot.send_message(
                chat_id,
                f"👤 شناسه کاربری: {call.from_user.id}\n"
                f"نام: {call.from_user.first_name or 'کاربر'}",
                reply_markup=main_keyboard()
            )

        elif action == "image":
            show_image_notice(chat_id)

        elif action == "reset_memory":
            clear_history(key)

            bot.send_message(
                chat_id,
                "🧹 حافظه گفتگو با موفقیت پاک شد. ❤️",
                reply_markup=main_keyboard()
            )

        else:
            bot.answer_callback_query(
                call.id,
                "این گزینه در دسترس نیست."
            )

    except Exception:
        logger.exception("Callback handler error")


# =====================================================
# TEXT BUTTONS + AI CHAT
# =====================================================

@bot.message_handler(content_types=["text"])
def chat(message):

    chat_id = message.chat.id
    key = str(chat_id)
    user_text = (message.text or "").strip()

    if not user_text:
        return

    # Main keyboard actions

    if user_text == BTN_CHAT:
        clear_history(key)

        bot.send_message(
            chat_id,
            "💬 گفتگوی تازه شروع شد!\n"
            "هر چیزی دوست داری بپرس. ❤️",
            reply_markup=main_keyboard()
        )
        return

    if user_text == BTN_IMAGE:
        show_image_notice(chat_id)
        return

    if user_text == BTN_ACCOUNT:
        show_account(message)
        return

    if user_text == BTN_HELP:
        show_help(chat_id)
        return

    if user_text == BTN_SETTINGS:
        show_settings(chat_id)
        return

    # Normal AI conversation

    try:
        waiting = bot.send_message(
            chat_id,
            "⏳ هشیوار داره فکر می‌کنه..."
        )

    except Exception:
        logger.exception("Could not send waiting message")
        return

    deadline = time.monotonic() + MAX_WAIT

    try:
        with get_chat_lock(key):

            check_deadline(deadline)

            answer = ask_ai(
                user_text,
                key,
                deadline
            )

        check_deadline(deadline)

        try:
            bot.delete_message(
                chat_id,
                waiting.message_id
            )
        except Exception:
            pass

        send_long_message(chat_id, answer)

    except Exception:
        logger.exception(
            "AI request failed or exceeded response deadline"
        )

        try:
            bot.delete_message(
                chat_id,
                waiting.message_id
            )
        except Exception:
            pass

        send_generic_error(chat_id)


# =====================================================
# WEBHOOK SERVER
# =====================================================

class WebhookHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        if self.path in ("/", "/health"):

            self.send_response(200)

            self.send_header(
                "Content-Type",
                "text/plain; charset=utf-8"
            )

            self.end_headers()

            self.wfile.write(
                b"Hoshiwar AI Bot is running"
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
            logger.warning("Invalid webhook secret")
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

            # TeleBot processes updates using its worker pool.
            # The webhook can acknowledge without waiting for AI.
            bot.process_new_updates([update])

            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

        except Exception:
            logger.exception("Webhook processing error")
            self.send_error(500)

    def log_message(self, format, *args):
        logger.info("WEBHOOK: %s", format % args)


# =====================================================
# START HOSHIVAR
# =====================================================

if __name__ == "__main__":

    logger.info("================================")
    logger.info("STARTING HOSHIVAR AI")
    logger.info("Primary engine: %s", GEMINI_MODEL)
    logger.info("Backup engine: %s", GROK_MODEL)
    logger.info("Maximum AI response time: %s seconds", MAX_WAIT)
    logger.info("================================")

    server = ThreadingHTTPServer(
        ("0.0.0.0", PORT),
        WebhookHandler
    )

    server.daemon_threads = True

    try:

        webhook_result = bot.set_webhook(
            url=WEBHOOK_URL,
            secret_token=WEBHOOK_SECRET,
            allowed_updates=["message", "callback_query"]
        )

        if not webhook_result:
            raise RuntimeError(
                "Telegram webhook setup failed"
            )

        logger.info("Webhook configured successfully")
        logger.info("Hoshiwar is live")

        server.serve_forever()

    except Exception:
        logger.exception("Application startup error")
        raise

    finally:
        server.server_close()
        logger.info("Hoshiwar server stopped")

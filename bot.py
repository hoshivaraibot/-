import os
import re
import io
import time
import base64
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

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.8-flash"
)

# CLOUDFLARE
CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID")
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN")

CLOUDFLARE_TEXT_MODEL = os.getenv(
    "CLOUDFLARE_TEXT_MODEL",
    "@cf/meta/llama-3.1-8b-instruct-fast"
)

CLOUDFLARE_IMAGE_MODEL = os.getenv(
    "CLOUDFLARE_IMAGE_MODEL",
    "@cf/black-forest-labs/flux-1-schnell"
)

PORT = int(os.getenv("PORT", "10000"))
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

WEBHOOK_PATH = "/telegram-webhook"

MAX_WAIT = 45
IMAGE_WAIT = 90
MAX_HISTORY = 10
MAX_MESSAGE_LENGTH = 3900
MAX_CACHED_CHATS = 5000

for name, value in [
    ("BOT_TOKEN", BOT_TOKEN),
    ("GEMINI_API_KEY", GEMINI_API_KEY),
    ("WEBHOOK_SECRET", WEBHOOK_SECRET),
    ("RENDER_EXTERNAL_URL", RENDER_URL)
]:
    if not value:
        raise ValueError(f"{name} is missing")

if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", WEBHOOK_SECRET):
    raise ValueError("Invalid WEBHOOK_SECRET")

if bool(CLOUDFLARE_ACCOUNT_ID) != bool(CLOUDFLARE_API_TOKEN):
    raise ValueError(
        "Both CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN are required together"
    )

WEBHOOK_URL = RENDER_URL.rstrip("/") + WEBHOOK_PATH

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/"
    f"models/{GEMINI_MODEL}:generateContent"
)

CLOUDFLARE_BASE_URL = (
    "https://api.cloudflare.com/client/v4/accounts/"
    f"{CLOUDFLARE_ACCOUNT_ID}/ai/run"
) if CLOUDFLARE_ACCOUNT_ID else None

# =====================================================
# LOGGING
# =====================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

logger = logging.getLogger("HOSHIVAR")

# =====================================================
# HTTP SESSION
# =====================================================

http_session = requests.Session()

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

    keyboard.row(KeyboardButton(BTN_SETTINGS))

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

awaiting_images = set()
image_state_lock = threading.Lock()


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

        if len(chat_memory) >= MAX_CACHED_CHATS and key not in chat_memory:
            oldest_key = next(iter(chat_memory))
            chat_memory.pop(oldest_key, None)

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


def set_image_mode(key):
    with image_state_lock:
        awaiting_images.add(key)


def is_image_mode(key):
    with image_state_lock:
        return key in awaiting_images


def clear_image_mode(key):
    with image_state_lock:
        awaiting_images.discard(key)


# =====================================================
# RESPONSE DEADLINE
# =====================================================

class DeadlineExceeded(Exception):
    pass


class ProviderAPIError(Exception):
    def __init__(self, provider, status, detail):
        self.provider = provider
        self.status = status
        self.detail = detail

        super().__init__(
            f"{provider} HTTP {status}: {detail}"
        )


def request_timeout(deadline):
    remaining = deadline - time.monotonic()

    if remaining <= 1:
        raise DeadlineExceeded("Response deadline exceeded")

    connect_timeout = min(4.0, remaining / 4)

    read_timeout = max(
        1.0,
        remaining - connect_timeout - 0.5
    )

    return (connect_timeout, read_timeout)


def check_deadline(deadline):
    if time.monotonic() >= deadline:
        raise DeadlineExceeded("Response deadline exceeded")


# =====================================================
# GEMINI COOLDOWN
# =====================================================

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
# GEMINI - PRIMARY TEXT ENGINE
# =====================================================

def ask_gemini(message, memory_key, deadline):

    remaining = gemini_cooldown_remaining()

    if remaining > 0:
        raise ProviderAPIError(
            "Gemini",
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

    response = http_session.post(
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
            "Gemini",
            429,
            detail
        )

    if not response.ok:
        raise ProviderAPIError(
            "Gemini",
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

    logger.info("Gemini response received")

    return answer


# =====================================================
# CLOUDFLARE - BACKUP TEXT ENGINE
# =====================================================

def cloudflare_request(model, payload, deadline):

    if not CLOUDFLARE_ACCOUNT_ID or not CLOUDFLARE_API_TOKEN:
        raise RuntimeError("Cloudflare credentials are missing")

    url = f"{CLOUDFLARE_BASE_URL}/{model}"

    headers = {
        "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}",
        "Content-Type": "application/json"
    }

    response = http_session.post(
        url,
        headers=headers,
        json=payload,
        timeout=request_timeout(deadline)
    )

    check_deadline(deadline)

    if not response.ok:
        raise ProviderAPIError(
            "Cloudflare",
            response.status_code,
            extract_error_detail(response)
        )

    try:
        result = response.json()
    except ValueError:
        raise RuntimeError("Cloudflare returned invalid JSON")

    if result.get("success") is False:
        raise RuntimeError(
            str(result.get("errors") or "Cloudflare model failed")
        )

    return result.get("result", result)


def ask_cloudflare(message, memory_key, deadline):

    history = get_history(memory_key)

    messages = [{
        "role": "system",
        "content": SYSTEM_PROMPT
    }]

    for item in history:

        role = item.get("role")

        if role == "user":
            target_role = "user"
        elif role == "model":
            target_role = "assistant"
        else:
            continue

        content = "".join(
            part.get("text", "")
            for part in item.get("parts", [])
            if isinstance(part, dict)
        )

        if content:
            messages.append({
                "role": target_role,
                "content": content
            })

    messages.append({
        "role": "user",
        "content": message
    })

    payload = {
        "messages": messages,
        "max_tokens": 2048,
        "temperature": 0.7
    }

    result = cloudflare_request(
        CLOUDFLARE_TEXT_MODEL,
        payload,
        deadline
    )

    answer = result.get("response", "")

    if not answer and isinstance(result.get("choices"), list):
        choices = result["choices"]

        if choices:
            answer = (
                choices[0]
                .get("message", {})
                .get("content", "")
            )

    if not isinstance(answer, str) or not answer.strip():
        raise RuntimeError("Cloudflare returned empty text")

    logger.info("Cloudflare backup response received")

    return answer.strip()


# =====================================================
# AI ENGINE - GEMINI + CLOUDFLARE
# =====================================================

def ask_ai(message, memory_key, deadline):

    try:

        answer = ask_gemini(
            message,
            memory_key,
            deadline
        )

        logger.info("AI provider: Gemini")

    except Exception as gemini_error:

        logger.warning(
            "Gemini failed; switching to Cloudflare: %s",
            gemini_error
        )

        check_deadline(deadline)

        answer = ask_cloudflare(
            message,
            memory_key,
            deadline
        )

        logger.info("AI provider: Cloudflare")

    check_deadline(deadline)

    save_history(
        memory_key,
        message,
        answer
    )

    return answer


# =====================================================
# CLOUDFLARE - IMAGE GENERATION
# =====================================================

def generate_image(prompt, deadline):

    if not CLOUDFLARE_ACCOUNT_ID or not CLOUDFLARE_API_TOKEN:
        raise RuntimeError("Cloudflare image service is not configured")

    if len(prompt) > 1800:
        raise ValueError("Image prompt is too long")

    payload = {
        "prompt": prompt,
        "steps": 4
    }

    result = cloudflare_request(
        CLOUDFLARE_IMAGE_MODEL,
        payload,
        deadline
    )

    image_data = result.get("image")

    if not image_data:
        raise RuntimeError("Cloudflare returned no image")

    try:
        image_bytes = base64.b64decode(
            image_data,
            validate=True
        )
    except Exception as exc:
        raise RuntimeError("Invalid generated image data") from exc

    if not image_bytes:
        raise RuntimeError("Generated image is empty")

    logger.info("Cloudflare image generated successfully")

    return image_bytes


def handle_image_request(chat_id, prompt):

    waiting = bot.send_message(
        chat_id,
        "🎨 هشیوار داره تصویرت رو می‌سازه...\n"
        "ممکنه کمی زمان ببره. ❤️"
    )

    deadline = time.monotonic() + IMAGE_WAIT

    try:

        image_bytes = generate_image(
            prompt,
            deadline
        )

        check_deadline(deadline)

        image_file = io.BytesIO(image_bytes)
        image_file.name = "hoshivar-image.jpg"

        try:
            bot.delete_message(
                chat_id,
                waiting.message_id
            )
        except Exception:
            pass

        bot.send_photo(
            chat_id,
            image_file,
            caption="✨ تصویرت آماده شد!\nساخته‌شده با هشیوار ❤️",
            reply_markup=main_keyboard(),
            timeout=30
        )

    except Exception:

        logger.exception("Image generation failed")

        try:
            bot.delete_message(
                chat_id,
                waiting.message_id
            )
        except Exception:
            pass

        bot.send_message(
            chat_id,
            "⚠️ متأسفانه ساخت تصویر انجام نشد.\n"
            "لطفاً دوباره تلاش کن. ❤️",
            reply_markup=main_keyboard()
        )


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
        "🎨 برای ساخت تصویر، روی دکمه ساخت تصویر بزن و توضیحت رو ارسال کن.\n"
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
    if not CLOUDFLARE_API_TOKEN or not CLOUDFLARE_ACCOUNT_ID:
        bot.send_message(
            chat_id,
            "⚠️ سرویس ساخت تصویر هنوز تنظیم نشده است.",
            reply_markup=main_keyboard()
        )
        return

    set_image_mode(str(chat_id))

    bot.send_message(
        chat_id,
        "🎨 به بخش ساخت تصویر هشیوار خوش اومدی!\n\n"
        "توضیح تصویری که می‌خوای بسازم رو برام بنویس.\n\n"
        "مثال:\n"
        "یک منظره طبیعی از جنگل‌های شمال ایران در غروب، "
        "واقع‌گرایانه و با کیفیت بالا 🌅\n\n"
        "برای لغو، روی «گفتگوی جدید» بزن.",
        reply_markup=main_keyboard()
    )


# =====================================================
# TELEGRAM COMMANDS
# =====================================================

@bot.message_handler(commands=["start"])
def start(message):
    clear_image_mode(str(message.chat.id))
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

            clear_image_mode(key)

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

    if user_text == BTN_CHAT:

        clear_image_mode(key)
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
        clear_image_mode(key)
        show_account(message)
        return

    if user_text == BTN_HELP:
        clear_image_mode(key)
        show_help(chat_id)
        return

    if user_text == BTN_SETTINGS:
        clear_image_mode(key)
        show_settings(chat_id)
        return

    # IMAGE MODE
    if is_image_mode(key):

        clear_image_mode(key)

        handle_image_request(
            chat_id,
            user_text
        )

        return

    # NORMAL AI CHAT
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

        send_long_message(
            chat_id,
            answer
        )

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

            bot.process_new_updates([update])

            self.send_response(200)

            self.end_headers()

            self.wfile.write(b"OK")

        except Exception:

            logger.exception("Webhook processing error")

            self.send_error(500)

    def log_message(self, format, *args):
        logger.info(
            "WEBHOOK: %s",
            format % args
        )


# =====================================================
# START HOSHIVAR
# =====================================================

if __name__ == "__main__":

    logger.info("================================")
    logger.info("STARTING HOSHIVAR AI")
    logger.info("Primary engine: Gemini - %s", GEMINI_MODEL)

    if CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID:
        logger.info(
            "Backup engine: Cloudflare - %s",
            CLOUDFLARE_TEXT_MODEL
        )
        logger.info(
            "Image engine: Cloudflare - %s",
            CLOUDFLARE_IMAGE_MODEL
        )
    else:
        logger.warning("Cloudflare is not configured")

    logger.info("Grok engine: Removed")
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

    except KeyboardInterrupt:
        logger.info("Hoshiwar is shutting down")

    except Exception:
        logger.exception("Application startup failed")
        raise

    finally:
        server.server_close()
        http_session.close()

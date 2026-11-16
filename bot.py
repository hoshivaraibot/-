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

# اگر GEMINI_MODEL در Render تعریف شده باشد، همان مقدار استفاده می‌شود.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

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
# HTTP SESSION / TELEGRAM
# =====================================================

http_session = requests.Session()
bot = telebot.TeleBot(BOT_TOKEN)

# =====================================================
# HOSHIVAR PERSONALITY
# =====================================================

SYSTEM_PROMPT = """
تو هشیوار هستی؛ یک دستیار هوش مصنوعی فارسی‌زبان، زرنگ، سریع،
باهوش، دقیق، صمیمی و حرفه‌ای.

ویژگی‌های رفتاری:
- منظور کاربر را با دقت تشخیص بده و مستقیم سر اصل مطلب برو.
- پاسخ‌ها را تا حد ممکن سریع، دقیق، کاربردی و منظم ارائه کن.
- فارسی را روان، طبیعی و درست بنویس؛ مگر اینکه کاربر زبان دیگری بخواهد.
- لحن تو دوستانه و محترمانه است؛ نه خشک و ماشینی و نه بیش از حد خودمانی.
- در صورت نیاز توضیحات مرحله‌به‌مرحله بده.
- در برنامه‌نویسی کد صحیح، کامل و قابل اجرا ارائه کن.
- اگر چیزی را نمی‌دانی یا مطمئن نیستی، صادقانه بگو و اطلاعات جعلی نساز.
- از ایموجی به اندازه استفاده کن.
- خودت را هشیوار معرفی کن.
- نام مدل‌ها و سرویس‌های پشتیبان را مطرح نکن.
- بی‌دلیل عذرخواهی نکن و پاسخ را با جملات تکراری طولانی نکن.
- از قلب آبی 💙 و نماد برق ⚡ در موقعیت‌های مناسب استفاده کن.
"""

# =====================================================
# MENU BUTTONS
# =====================================================

BTN_CHAT = "💬 گفتگوی جدید"
BTN_IMAGE = "🎨 ساخت تصویر"
BTN_WORD = "📚 معنی کلمات"
BTN_PROVERB = "🪶 ضرب‌المثل‌یاب"
BTN_ACCOUNT = "👤 حساب کاربری"
BTN_HELP = "ℹ️ راهنما"
BTN_SETTINGS = "⚙️ تنظیمات"

def main_keyboard():
    keyboard = ReplyKeyboardMarkup(
        resize_keyboard=True,
        row_width=2,
        is_persistent=True
    )
    keyboard.row(KeyboardButton(BTN_CHAT), KeyboardButton(BTN_IMAGE))
    keyboard.row(KeyboardButton(BTN_WORD), KeyboardButton(BTN_PROVERB))
    keyboard.row(KeyboardButton(BTN_ACCOUNT), KeyboardButton(BTN_HELP))
    keyboard.row(KeyboardButton(BTN_SETTINGS))
    return keyboard


def main_inline_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)

    keyboard.row(
        InlineKeyboardButton("💬 شروع گفتگو", callback_data="new_chat")
    )
    keyboard.row(
        InlineKeyboardButton("🎨 ساخت تصویر", callback_data="image"),
        InlineKeyboardButton("📚 معنی کلمات", callback_data="word")
    )
    keyboard.row(
        InlineKeyboardButton("🪶 ضرب‌المثل‌یاب", callback_data="proverb"),
        InlineKeyboardButton("👤 حساب کاربری", callback_data="account")
    )
    keyboard.row(
        InlineKeyboardButton("ℹ️ راهنما", callback_data="help"),
        InlineKeyboardButton("⚙️ تنظیمات", callback_data="settings")
    )
    keyboard.row(
        InlineKeyboardButton("💙 درباره هشیوار", callback_data="about")
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
        "به هشیوار خوش اومدی؛ دستیار هوشمند، زرنگ و سریع تو. 💙\n\n"
        "هر سؤالی داری بپرس یا از قابلیت‌های منو استفاده کن. ⚡",
        reply_markup=main_keyboard()
    )
    bot.send_message(
        chat_id,
        "از منوی زیر هم می‌تونی استفاده کنی:",
        reply_markup=main_inline_keyboard()
    )


# =====================================================
# CHAT MEMORY AND USER MODES
# =====================================================

chat_memory = {}
memory_lock = threading.Lock()

chat_locks = {}
chat_locks_lock = threading.Lock()

awaiting_images = set()
awaiting_words = set()
awaiting_proverbs = set()
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


def clear_all_modes(key):
    with image_state_lock:
        awaiting_images.discard(key)
        awaiting_words.discard(key)
        awaiting_proverbs.discard(key)


def set_mode(mode_set, key):
    with image_state_lock:
        awaiting_images.discard(key)
        awaiting_words.discard(key)
        awaiting_proverbs.discard(key)
        mode_set.add(key)


def has_mode(mode_set, key):
    with image_state_lock:
        return key in mode_set


def clear_mode(mode_set, key):
    with image_state_lock:
        mode_set.discard(key)


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
        super().__init__(f"{provider} HTTP {status}: {detail}")


def request_timeout(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 1:
        raise DeadlineExceeded("Response deadline exceeded")

    connect_timeout = min(4.0, remaining / 4)
    read_timeout = max(1.0, remaining - connect_timeout - 0.5)
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
    return max(60, int(hours * 3600 + minutes * 60 + seconds))


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


# =====================================================
# GEMINI - PRIMARY TEXT ENGINE
# =====================================================

def ask_gemini(message, memory_key, deadline):
    remaining = gemini_cooldown_remaining()
    if remaining > 0:
        raise ProviderAPIError(
            "Gemini", 429, f"Quota cooldown: {remaining} seconds"
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
        raise ProviderAPIError("Gemini", 429, detail)

    if not response.ok:
        raise ProviderAPIError(
            "Gemini", response.status_code, extract_error_detail(response)
        )

    result = response.json()
    candidates = result.get("candidates", [])
    if not candidates:
        raise RuntimeError("Gemini returned no candidates")

    parts = candidates[0].get("content", {}).get("parts", [])
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
            "Cloudflare", response.status_code, extract_error_detail(response)
        )

    try:
        result = response.json()
    except ValueError as exc:
        raise RuntimeError("Cloudflare returned invalid JSON") from exc

    if result.get("success") is False:
        raise RuntimeError(str(result.get("errors") or "Cloudflare model failed"))

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
            messages.append({"role": target_role, "content": content})

    messages.append({"role": "user", "content": message})
    payload = {
        "messages": messages,
        "max_tokens": 2048,
        "temperature": 0.7
    }

    result = cloudflare_request(CLOUDFLARE_TEXT_MODEL, payload, deadline)
    answer = result.get("response", "")

    if not answer and isinstance(result.get("choices"), list):
        choices = result["choices"]
        if choices:
            answer = (
                choices[0].get("message", {}).get("content", "")
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
        answer = ask_gemini(message, memory_key, deadline)
        logger.info("AI provider: Gemini")
    except Exception as gemini_error:
        logger.warning(
            "Gemini failed; switching to Cloudflare: %s", gemini_error
        )
        check_deadline(deadline)
        answer = ask_cloudflare(message, memory_key, deadline)
        logger.info("AI provider: Cloudflare")

    check_deadline(deadline)
    save_history(memory_key, message, answer)
    return answer


# =====================================================
# IMAGE PROMPT TRANSLATION + IMAGE GENERATION
# =====================================================

def translate_image_prompt(prompt, deadline):
    """
    ترجمه پنهانی توضیح فارسی تصویر به پرامپت انگلیسی مناسب مدل تصویرساز.
    """
    if not CLOUDFLARE_ACCOUNT_ID or not CLOUDFLARE_API_TOKEN:
        raise RuntimeError("Cloudflare text service is not configured")

    messages = [
        {
            "role": "system",
            "content": (
                "You translate image-generation requests into concise, "
                "high-quality English prompts. Preserve the user's exact "
                "meaning, subjects, setting, composition, style and details. "
                "Return only the English image prompt. Do not add commentary."
            )
        },
        {
            "role": "user",
            "content": prompt
        }
    ]

    result = cloudflare_request(
        CLOUDFLARE_TEXT_MODEL,
        {
            "messages": messages,
            "max_tokens": 700,
            "temperature": 0.2
        },
        deadline
    )

    translated = result.get("response", "")
    if not translated and isinstance(result.get("choices"), list):
        choices = result["choices"]
        if choices:
            translated = (
                choices[0].get("message", {}).get("content", "")
            )

    translated = translated.strip() if isinstance(translated, str) else ""
    if not translated:
        raise RuntimeError("Image prompt translation returned empty text")

    return translated[:1800]


def generate_image(prompt, deadline):
    if not CLOUDFLARE_ACCOUNT_ID or not CLOUDFLARE_API_TOKEN:
        raise RuntimeError("Cloudflare image service is not configured")

    if len(prompt) > 1800:
        raise ValueError("Image prompt is too long")

    result = cloudflare_request(
        CLOUDFLARE_IMAGE_MODEL,
        {"prompt": prompt, "steps": 4},
        deadline
    )

    image_data = result.get("image")
    if not image_data:
        raise RuntimeError("Cloudflare returned no image")

    try:
        image_bytes = base64.b64decode(image_data, validate=True)
    except Exception as exc:
        raise RuntimeError("Invalid generated image data") from exc

    if not image_bytes:
        raise RuntimeError("Generated image is empty")

    logger.info("Cloudflare image generated successfully")
    return image_bytes


def handle_image_request(chat_id, prompt):
    waiting = bot.send_message(
        chat_id,
        "🎨 هشیوار در حال آماده‌سازی تصویرته...\n"
        "اول توضیحت رو برای تصویرساز آماده می‌کنم و بعد تصویر رو می‌سازم. ⚡"
    )

    deadline = time.monotonic() + IMAGE_WAIT

    try:
        english_prompt = translate_image_prompt(prompt, deadline)
        image_bytes = generate_image(english_prompt, deadline)
        check_deadline(deadline)

        image_file = io.BytesIO(image_bytes)
        image_file.name = "hoshivar-image.jpg"

        try:
            bot.delete_message(chat_id, waiting.message_id)
        except Exception:
            pass

        bot.send_photo(
            chat_id,
            image_file,
            caption="✨ تصویرت آماده شد!\nساخته‌شده با هشیوار 💙",
            reply_markup=main_keyboard(),
            timeout=30
        )

    except Exception:
        logger.exception("Image generation failed")
        try:
            bot.delete_message(chat_id, waiting.message_id)
        except Exception:
            pass

        bot.send_message(
            chat_id,
            "⚠️ متأسفانه ساخت تصویر انجام نشد.\n"
            "لطفاً دوباره تلاش کن. اگر مشکل ادامه داشت، ممکنه سرویس تصویر موقتاً در دسترس نباشه. 💙",
            reply_markup=main_keyboard()
        )


# =====================================================
# TELEGRAM MESSAGE UTILITIES / INFORMATION PAGES
# =====================================================

def send_long_message(chat_id, text):
    for start in range(0, len(text), MAX_MESSAGE_LENGTH):
        bot.send_message(chat_id, text[start:start + MAX_MESSAGE_LENGTH])


def send_generic_error(chat_id):
    bot.send_message(
        chat_id,
        "⚠️ هشیوار فعلاً نتونست پاسخ رو آماده کنه.\n"
        "لطفاً کمی بعد دوباره تلاش کن. 💙",
        reply_markup=main_keyboard()
    )


def show_help(chat_id):
    bot.send_message(
        chat_id,
        "🤖 راهنمای هشیوار\n\n"
        "💬 گفتگوی جدید: پرسیدن هر نوع سؤال\n"
        "🎨 ساخت تصویر: توضیح تصویر را به فارسی بنویس\n"
        "📚 معنی کلمات: واژه را بفرست تا معنی و کاربردش را بدانی\n"
        "🪶 ضرب‌المثل‌یاب: موضوع را بنویس تا ضرب‌المثل‌های مرتبط پیشنهاد شوند\n"
        "👤 حساب کاربری: مشاهده اطلاعات حساب\n"
        "⚙️ تنظیمات: مدیریت حافظه گفتگو\n\n"
        "برای شروع، یکی از دکمه‌های منو را انتخاب کن. 💙",
        reply_markup=main_keyboard()
    )


def show_about(chat_id):
    bot.send_message(
        chat_id,
        "💙 درباره هشیوار\n\n"
        "هشیوار یک دستیار هوش مصنوعی فارسی‌زبان است؛ "
        "زرنگ، سریع، دقیق و خوش‌برخورد.\n\n"
        "✨ گفت‌وگوی هوشمند\n"
        "🎨 ساخت تصویر با توضیح فارسی\n"
        "📚 معنی واژه‌ها و آشنایی با زبان فارسی\n"
        "🪶 پیدا کردن ضرب‌المثل بر اساس موضوع\n\n"
        "شعار هشیوار:\n"
        "«باهوش، زرنگ و همیشه آماده!» ⚡",
        reply_markup=main_keyboard()
    )


def show_settings(chat_id):
    bot.send_message(
        chat_id,
        "⚙️ تنظیمات هشیوار\n\n"
        "از گزینه زیر برای مدیریت حافظه گفتگوی فعلی استفاده کن.",
        reply_markup=settings_keyboard()
    )


def show_account(message):
    bot.send_message(
        message.chat.id,
        "👤 حساب کاربری\n\n"
        f"شناسه کاربری: {message.from_user.id}\n"
        f"نام: {message.from_user.first_name or 'کاربر'}\n\n"
        "از همراهی تو با هشیوار خوشحالیم. 💙",
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

    key = str(chat_id)
    set_mode(awaiting_images, key)

    bot.send_message(
        chat_id,
        "🎨 به استودیوی تصویرسازی هشیوار خوش اومدی!\n\n"
        "توضیح تصویری که می‌خوای بسازم رو به فارسی بنویس.\n"
        "من توضیحت رو آماده می‌کنم و تصویر رو می‌سازم.\n\n"
        "مثال: یک منظره طبیعی از جنگل‌های شمال ایران در غروب، "
        "واقع‌گرایانه و با کیفیت بالا 🌅\n\n"
        "برای لغو، روی «گفتگوی جدید» بزن.",
        reply_markup=main_keyboard()
    )


def show_word_notice(chat_id):
    set_mode(awaiting_words, str(chat_id))
    bot.send_message(
        chat_id,
        "📚 به واژه‌دان هشیوار خوش اومدی!\n\n"
        "کلمه یا عبارت فارسی موردنظرت رو بفرست.\n"
        "معنی ساده، مترادف، متضاد، ریشه واژه (در صورت اطمینان) "
        "و یک مثال کاربردی رو برات توضیح می‌دم.\n\n"
        "مثال: «فرزانگی»\n\n"
        "برای لغو، «گفتگوی جدید» رو بزن. 💙",
        reply_markup=main_keyboard()
    )


def show_proverb_notice(chat_id):
    set_mode(awaiting_proverbs, str(chat_id))
    bot.send_message(
        chat_id,
        "🪶 به ضرب‌المثل‌یاب هشیوار خوش اومدی!\n\n"
        "فقط موضوع موردنظرت رو بنویس؛ مثلاً:\n"
        "• رفاقت\n"
        "• صبر و شکیبایی\n"
        "• آدم دورو\n"
        "• تلاش و موفقیت\n\n"
        "۵ تا ۱۰ ضرب‌المثل مرتبط همراه با معنی برات می‌نویسم. "
        "برای لغو، «گفتگوی جدید» رو بزن. 💙",
        reply_markup=main_keyboard()
    )


# =====================================================
# SPECIAL FEATURE PROMPTS
# =====================================================

def build_word_request(user_text):
    return (
        "در نقش فرهنگ‌نویس دقیق زبان فارسی پاسخ بده. "
        "واژه یا عبارت مورد پرسش کاربر: "
        f"{user_text}\n\n"
        "پاسخ را به فارسی و با این ساختار ارائه کن:\n"
        "۱. معنی ساده و دقیق\n"
        "۲. مترادف‌ها (اگر وجود دارند)\n"
        "۳. متضادها (اگر وجود دارند)\n"
        "۴. ریشه و خاستگاه واژه فقط در صورت اطمینان؛ اگر مطمئن نیستی "
        "صریحاً بگو اطلاعات ریشه‌شناختی قطعی در دسترس نیست.\n"
        "۵. یک یا دو مثال طبیعی در جمله.\n"
        "اگر واژه چند معنی دارد، معنی‌های رایج را جدا کن. "
        "اطلاعات ساختگی نساز."
    )


def build_proverb_request(topic):
    return (
        "کاربر برای موضوع زیر ضرب‌المثل فارسی می‌خواهد:\n"
        f"موضوع: {topic}\n\n"
        "۵ تا ۱۰ ضرب‌المثل واقعی و مرتبط فارسی پیشنهاد کن. "
        "برای هر مورد، خود ضرب‌المثل و معنی کوتاه و روشن آن را بنویس. "
        "اگر درباره اصالت یا رایج بودن عبارتی مطمئن نیستی، آن را "
        "به‌عنوان ضرب‌المثل قطعی معرفی نکن. جمله‌های ساخته خودت را "
        "به‌جای ضرب‌المثل جا نزن. اگر تعداد ضرب‌المثل‌های معتبر برای "
        "موضوع کم است، تعداد کمتر اما درست ارائه کن. "
        "در پایان، در صورت مفید بودن یک ضرب‌المثل ادبی‌تر هم معرفی کن."
    )


# =====================================================
# TELEGRAM COMMANDS
# =====================================================

@bot.message_handler(commands=["start"])
def start(message):
    clear_all_modes(str(message.chat.id))
    send_home(message.chat.id)


@bot.message_handler(commands=["help"])
def help_command(message):
    show_help(message.chat.id)


@bot.message_handler(commands=["reset"])
def reset_command(message):
    key = str(message.chat.id)
    clear_history(key)
    clear_all_modes(key)
    bot.send_message(
        message.chat.id,
        "🔄 حافظه این گفتگو پاک شد. 💙",
        reply_markup=main_keyboard()
    )


# =====================================================
# INLINE BUTTON CALLBACKS
# =====================================================

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    # پاسخ callback را جداگانه مدیریت می‌کنیم تا منقضی‌شدن آن
    # مانع اجرای عملکرد دکمه نشود.
    try:
        bot.answer_callback_query(call.id)
    except Exception as exc:
        logger.warning("Callback acknowledgement failed: %s", exc)

    try:
        chat_id = call.message.chat.id
        key = str(chat_id)
        action = call.data

        if action in ("home", "new_chat"):
            clear_all_modes(key)
            if action == "new_chat":
                clear_history(key)
            send_home(chat_id)

        elif action == "help":
            show_help(chat_id)

        elif action == "about":
            show_about(chat_id)

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

        elif action == "word":
            show_word_notice(chat_id)

        elif action == "proverb":
            show_proverb_notice(chat_id)

        elif action == "reset_memory":
            clear_history(key)
            bot.send_message(
                chat_id,
                "🧹 حافظه گفتگو با موفقیت پاک شد. 💙",
                reply_markup=main_keyboard()
            )

        else:
            try:
                bot.answer_callback_query(
                    call.id,
                    "این گزینه در دسترس نیست."
                )
            except Exception:
                pass

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
        clear_all_modes(key)
        clear_history(key)
        bot.send_message(
            chat_id,
            "💬 گفتگوی تازه شروع شد!\n"
            "هر چیزی دوست داری بپرس. 💙",
            reply_markup=main_keyboard()
        )
        return

    if user_text == BTN_IMAGE:
        show_image_notice(chat_id)
        return

    if user_text == BTN_WORD:
        show_word_notice(chat_id)
        return

    if user_text == BTN_PROVERB:
        show_proverb_notice(chat_id)
        return

    if user_text == BTN_ACCOUNT:
        clear_all_modes(key)
        show_account(message)
        return

    if user_text == BTN_HELP:
        clear_all_modes(key)
        show_help(chat_id)
        return

    if user_text == BTN_SETTINGS:
        clear_all_modes(key)
        show_settings(chat_id)
        return

    # حالت ساخت تصویر
    if has_mode(awaiting_images, key):
        clear_mode(awaiting_images, key)
        handle_image_request(chat_id, user_text)
        return

    # حالت معنی واژه
    if has_mode(awaiting_words, key):
        clear_mode(awaiting_words, key)
        request_text = build_word_request(user_text)

    # حالت ضرب‌المثل‌یاب
    elif has_mode(awaiting_proverbs, key):
        clear_mode(awaiting_proverbs, key)
        request_text = build_proverb_request(user_text)

    else:
        request_text = user_text

    try:
        waiting = bot.send_message(
            chat_id,
            "🧠 هشیوار در حال اندیشیدن است...\n"
            "لطفاً چند لحظه صبر کن؛ بهترین پاسخ در راهه! ⚡"
        )
    except Exception:
        logger.exception("Could not send waiting message")
        return

    deadline = time.monotonic() + MAX_WAIT

    try:
        with get_chat_lock(key):
            check_deadline(deadline)
            answer = ask_ai(request_text, key, deadline)

        check_deadline(deadline)

        try:
            bot.delete_message(chat_id, waiting.message_id)
        except Exception:
            pass

        send_long_message(chat_id, answer)

    except Exception:
        logger.exception("AI request failed or exceeded response deadline")
        try:
            bot.delete_message(chat_id, waiting.message_id)
        except Exception:
            pass
        send_generic_error(chat_id)


# =====================================================
# WEBHOOK SERVER
# =====================================================

class WebhookHandler(BaseHTTPRequestHandler):

    def do_HEAD(self):
        if self.path in ("/", "/health"):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_error(404)

    def do_GET(self):
        if self.path in ("/", "/health"):
            body = b"Hoshiwar AI Bot is running"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
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
            content_length = int(self.headers.get("Content-Length", "0"))
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

    logger.info("Maximum AI response time: %s seconds", MAX_WAIT)
    logger.info("================================")

    server = ThreadingHTTPServer(("0.0.0.0", PORT), WebhookHandler)
    server.daemon_threads = True

    try:
        webhook_result = bot.set_webhook(
            url=WEBHOOK_URL,
            secret_token=WEBHOOK_SECRET,
            allowed_updates=["message", "callback_query"]
        )

        if not webhook_result:
            raise RuntimeError("Telegram webhook setup failed")

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

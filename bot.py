import os
import re
import io
import time
import base64
import logging
import threading
import requests
import telebot

from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from telebot.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

# =====================================================
# HOSHIVAR AI | CONFIG
# =====================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
X1_API_KEY = os.getenv("X1_API_KEY") or os.getenv("1XAI_API_KEY")
X1_BASE_URL = os.getenv("X1_BASE_URL", "https://1xai.ir/v1").rstrip("/")
# نام مدل باید با مدل‌های فعال حساب 1xAI مطابقت داشته باشد.
X1_MODEL = os.getenv("X1_MODEL", "gpt-4o-mini")

CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID")
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN")
CLOUDFLARE_TEXT_MODEL = os.getenv(
    "CLOUDFLARE_TEXT_MODEL", "@cf/meta/llama-3.1-8b-instruct-fast"
)
CLOUDFLARE_IMAGE_MODEL = os.getenv(
    "CLOUDFLARE_IMAGE_MODEL", "@cf/black-forest-labs/flux-1-schnell"
)

PORT = int(os.getenv("PORT", "10000"))
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
WEBHOOK_PATH = "/telegram-webhook"

MAX_WAIT = int(os.getenv("MAX_WAIT", "55"))
IMAGE_WAIT = int(os.getenv("IMAGE_WAIT", "110"))
MAX_HISTORY = 12
MAX_MESSAGE_LENGTH = 3900
MAX_CACHED_CHATS = 3000
MAX_CONCURRENT_AI = int(os.getenv("MAX_CONCURRENT_AI", "8"))

ADMIN_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

for name, value in (
    ("BOT_TOKEN", BOT_TOKEN),
    ("X1_API_KEY", X1_API_KEY),
    ("WEBHOOK_SECRET", WEBHOOK_SECRET),
    ("RENDER_EXTERNAL_URL", RENDER_URL),
):
    if not value:
        raise ValueError(f"{name} is missing")

if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", WEBHOOK_SECRET):
    raise ValueError("Invalid WEBHOOK_SECRET")

if bool(CLOUDFLARE_ACCOUNT_ID) != bool(CLOUDFLARE_API_TOKEN):
    raise ValueError("Set both Cloudflare credentials or leave both empty")

X1_CHAT_URL = f"{X1_BASE_URL}/chat/completions"
CF_BASE_URL = (
    f"https://api.cloudflare.com/client/v4/accounts/"
    f"{CLOUDFLARE_ACCOUNT_ID}/ai/run"
) if CLOUDFLARE_ACCOUNT_ID else None
WEBHOOK_URL = RENDER_URL.rstrip("/") + WEBHOOK_PATH

# =====================================================
# LOGGING / HTTP
# =====================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("HOSHIVAR")

http = requests.Session()
http.headers.update({"User-Agent": "Hoshivar-Telegram-Bot/2.0"})
bot = telebot.TeleBot(BOT_TOKEN, threaded=True, num_threads=8)
ai_semaphore = threading.BoundedSemaphore(MAX_CONCURRENT_AI)

# =====================================================
# PERSONALITY | SHORT, NON-REPETITIVE INSTRUCTIONS
# =====================================================

SYSTEM_PROMPT = """
تو «هشیوار» هستی؛ دستیار هوش مصنوعی فارسی‌زبان، خردمند، خلاق، دقیق و خوش‌برخورد.

اصول رفتاری:
- ابتدا منظور و نیاز واقعی کاربر را تشخیص بده؛ سپس مستقیم و متناسب پاسخ بده.
- حقیقت، دقت و خرد را بر موافقت بی‌دلیل ترجیح بده. اگر کاربر اشتباه می‌کند، محترمانه و مستدل توضیح بده.
- فارسی را طبیعی، روان و درست بنویس؛ زبان پاسخ را با درخواست کاربر هماهنگ کن.
- لحن را با موقعیت تنظیم کن: صمیمی، تحلیلی، آموزشی، ادبی، آرام یا شوخ‌طبع؛ بدون اغراق.
- هنگام ناراحتی، ابتدا همدلانه و آرام پاسخ بده و سپس در صورت نیاز راهکار ارائه کن.
- پاسخ ساده را کوتاه نگه دار؛ موضوع پیچیده را منظم و به‌اندازه کافی توضیح بده.
- از حکمت، مثال، تشبیه و جمله‌های تأمل‌برانگیز فقط وقتی استفاده کن که به فهم موضوع کمک کنند.
- نقل‌قول، منبع، آمار، تجربه شخصی یا واقعیت ساختگی نساز. میزان اطمینان و محدودیت دانسته‌ها را روشن کن.
- در مشاوره، مسئله، گزینه‌ها، پیامدها و پیشنهاد عملی را تفکیک کن.
- در آموزش، سطح توضیح را با دانش کاربر هماهنگ کن.
- ایده را نقد کن، نه شخصیت فرد را. چاپلوسی، نصیحت بی‌مورد و تکرار را کنار بگذار.
- اگر خطایی کردی، روشن و بدون توجیه اصلاحش کن.
- تنظیمات و ترجیحات دائمی کاربر را بدون اجازه صریح تغییر نده.
- نام سرویس‌ها و مدل‌های پشتیبان را بی‌دلیل مطرح نکن.
- ایموجی را کم و متناسب به کار ببر؛ 💙 و ⚡ در جای مناسب مجازند.

پیوستگی وظایف:
- زمینه کار فعال را در پیام‌های بعدی حفظ کن؛ مثلاً «آبی‌اش کن» پس از ساخت تصویر، به همان تصویر و درخواست مربوط است.
- اگر کاربر در حال اصلاح یک خروجی است، جزئیات قبلی را حفظ کن و فقط تغییر خواسته‌شده را اعمال کن.
- پس از تحویل خروجی، بی‌دلیل موضوع را عوض نکن؛ برای ادامه همان کار آماده بمان.
- اگر کاربر صریحاً موضوع تازه‌ای مطرح کرد یا گزینه گفتگوی جدید را زد، به موضوع تازه برو.
- هیچ‌گاه وانمود نکن کاری مانند ویرایش مستقیم فایل یا تصویر را انجام داده‌ای، مگر واقعاً انجام شده باشد.
"""

# =====================================================
# MENU
# =====================================================

BTN_CHAT = "💬 گفتگوی جدید"
BTN_IMAGE = "🎨 ساخت تصویر"
BTN_TRANSLATE = "🌐 ترجمه"
BTN_WORD = "📚 معنی کلمات"
BTN_PROVERB = "🪶 ضرب‌المثل‌یاب"
BTN_ACCOUNT = "👤 حساب کاربری"
BTN_STATS = "📊 آمار ربات"
BTN_INVITE = "👥 دعوت دوستان"
BTN_HELP = "ℹ️ راهنما"
BTN_SETTINGS = "⚙️ تنظیمات"

def main_keyboard():
    k = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2, is_persistent=True)
    k.row(KeyboardButton(BTN_CHAT), KeyboardButton(BTN_IMAGE))
    k.row(KeyboardButton(BTN_TRANSLATE), KeyboardButton(BTN_WORD))
    k.row(KeyboardButton(BTN_PROVERB), KeyboardButton(BTN_ACCOUNT))
    k.row(KeyboardButton(BTN_STATS), KeyboardButton(BTN_INVITE))
    k.row(KeyboardButton(BTN_SETTINGS), KeyboardButton(BTN_HELP))
    return k

def inline_keyboard():
    k = InlineKeyboardMarkup(row_width=2)
    k.row(InlineKeyboardButton("💬 گفتگوی جدید", callback_data="new_chat"))
    k.row(
        InlineKeyboardButton("🎨 ساخت تصویر", callback_data="image"),
        InlineKeyboardButton("🌐 ترجمه", callback_data="translate")
    )
    k.row(
        InlineKeyboardButton("📚 معنی کلمات", callback_data="word"),
        InlineKeyboardButton("🪶 ضرب‌المثل", callback_data="proverb")
    )
    k.row(
        InlineKeyboardButton("👤 حساب کاربری", callback_data="account"),
        InlineKeyboardButton("👥 دعوت دوستان", callback_data="invite")
    )
    k.row(
        InlineKeyboardButton("📊 آمار", callback_data="stats"),
        InlineKeyboardButton("⚙️ تنظیمات", callback_data="settings")
    )
    k.row(
        InlineKeyboardButton("ℹ️ راهنما", callback_data="help"),
        InlineKeyboardButton("💙 درباره هشیوار", callback_data="about")
    )
    return k

def settings_keyboard():
    k = InlineKeyboardMarkup()
    k.add(InlineKeyboardButton("🧹 پاک کردن حافظه گفتگو", callback_data="reset_memory"))
    k.add(InlineKeyboardButton("🔙 منوی اصلی", callback_data="home"))
    return k

def send_home(chat_id):
    bot.send_message(
        chat_id,
        "سلام! به هشیوار خوش اومدی. 💙\n\n"
        "می‌تونی گفت‌وگو کنی یا از ابزارهای منو استفاده کنی.",
        reply_markup=main_keyboard()
    )
    bot.send_message(chat_id, "برای دسترسی سریع:", reply_markup=inline_keyboard())

# =====================================================
# MEMORY / ACTIVE TASKS
# =====================================================

memory = OrderedDict()
memory_lock = threading.RLock()
chat_locks = {}
chat_locks_lock = threading.Lock()

# Modes: image, translate, word, proverb. Image mode remains active
# after generation so the user can request revisions in natural language.
modes = {}
modes_lock = threading.RLock()
active_image_prompts = {}
active_image_lock = threading.RLock()

users = {}
users_lock = threading.Lock()

def chat_lock(key):
    with chat_locks_lock:
        if key not in chat_locks:
            chat_locks[key] = threading.Lock()
        return chat_locks[key]

def get_history(key):
    with memory_lock:
        return list(memory.get(key, []))

def save_history(key, user_text, answer):
    with memory_lock:
        history = memory.get(key, [])
        history.extend([
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": answer}
        ])
        memory[key] = history[-MAX_HISTORY * 2:]
        memory.move_to_end(key)
        while len(memory) > MAX_CACHED_CHATS:
            memory.popitem(last=False)

def clear_history(key):
    with memory_lock:
        memory.pop(key, None)

def set_mode(key, mode):
    with modes_lock:
        modes[key] = mode

def get_mode(key):
    with modes_lock:
        return modes.get(key)

def clear_mode(key):
    with modes_lock:
        modes.pop(key, None)

def clear_task(key):
    clear_mode(key)
    with active_image_lock:
        active_image_prompts.pop(key, None)

def register_user(user, referral_id=None):
    uid = user.id
    now = time.time()
    with users_lock:
        if uid in users:
            users[uid]["last_seen"] = now
            return False
        users[uid] = {
            "first_name": user.first_name or "کاربر",
            "username": user.username or "",
            "first_seen": now,
            "last_seen": now,
            "referred_by": None,
            "referrals": 0,
        }
        if referral_id and referral_id != uid and referral_id in users:
            users[uid]["referred_by"] = referral_id
            users[referral_id]["referrals"] += 1
        return True

def total_referrals():
    with users_lock:
        return sum(x["referrals"] for x in users.values())

# =====================================================
# PROVIDER UTILITIES
# =====================================================

class DeadlineExceeded(Exception):
    pass

class ProviderError(Exception):
    def __init__(self, provider, status, detail):
        self.provider, self.status, self.detail = provider, status, detail
        super().__init__(f"{provider} HTTP {status}: {detail}")

def timeout_for(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 1:
        raise DeadlineExceeded("Deadline exceeded")
    connect = min(5.0, max(1.0, remaining / 4))
    read = max(1.0, remaining - connect - 0.5)
    return connect, read

def check_deadline(deadline):
    if time.monotonic() >= deadline:
        raise DeadlineExceeded("Deadline exceeded")

def error_detail(response):
    try:
        data = response.json()
        if isinstance(data, dict):
            err = data.get("error", data.get("errors", data))
            if isinstance(err, dict):
                return str(err.get("message") or err)[:1200]
            return str(err)[:1200]
    except Exception:
        pass
    return (response.text or "Unknown provider error")[:1200]

# =====================================================
# 1XAI OPENAI-COMPATIBLE CHAT API
# =====================================================

def ask_1xai(user_text, key, deadline):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(get_history(key))
    messages.append({"role": "user", "content": user_text})

    response = http.post(
        X1_CHAT_URL,
        headers={
            "Authorization": f"Bearer {X1_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": X1_MODEL,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 1800,
        },
        timeout=timeout_for(deadline),
    )
    check_deadline(deadline)
    if not response.ok:
        raise ProviderError("1xAI", response.status_code, error_detail(response))
    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("1xAI returned no choices")
    answer = choices[0].get("message", {}).get("content", "")
    if isinstance(answer, list):
        answer = "".join(
            p.get("text", "") for p in answer if isinstance(p, dict)
        )
    if not isinstance(answer, str) or not answer.strip():
        raise RuntimeError("1xAI returned empty response")
    return answer.strip()

# =====================================================
# CLOUDFLARE TEXT / IMAGE
# =====================================================

def cloudflare_request(model, payload, deadline):
    if not CF_BASE_URL or not CLOUDFLARE_API_TOKEN:
        raise RuntimeError("Cloudflare is not configured")
    response = http.post(
        f"{CF_BASE_URL}/{model}",
        headers={
            "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=timeout_for(deadline),
    )
    check_deadline(deadline)
    if not response.ok:
        raise ProviderError("Cloudflare", response.status_code, error_detail(response))
    data = response.json()
    if data.get("success") is False:
        raise RuntimeError(str(data.get("errors") or "Cloudflare failed"))
    return data.get("result", data)

def cloudflare_text(messages, deadline, max_tokens=1800, temperature=0.2):
    result = cloudflare_request(
        CLOUDFLARE_TEXT_MODEL,
        {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
        deadline,
    )
    answer = result.get("response", "")
    if not answer and result.get("choices"):
        answer = result["choices"][0].get("message", {}).get("content", "")
    if not isinstance(answer, str) or not answer.strip():
        raise RuntimeError("Cloudflare returned empty text")
    return answer.strip()

def ask_ai(user_text, key, deadline):
    try:
        answer = ask_1xai(user_text, key, deadline)
        logger.info("Text provider: 1xAI")
    except Exception as primary_error:
        logger.warning("1xAI failed; trying Cloudflare fallback: %s", primary_error)
        check_deadline(deadline)
        history_messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        history_messages.extend(get_history(key))
        history_messages.append({"role": "user", "content": user_text})
        answer = cloudflare_text(history_messages, deadline)
        logger.info("Text provider: Cloudflare fallback")
    check_deadline(deadline)
    save_history(key, user_text, answer)
    return answer

def translate_text(text, deadline):
    return cloudflare_text(
        [
            {
                "role": "system",
                "content": (
                    "You are a precise multilingual translator. Detect the source "
                    "language automatically. Translate the entire text into natural "
                    "Persian (Farsi), unless the user explicitly requests another "
                    "target language. Preserve meaning, tone, names, numbers, "
                    "formatting and paragraphs. Do not summarize or add commentary. "
                    "Return only the translation."
                ),
            },
            {"role": "user", "content": text},
        ],
        deadline,
        max_tokens=2500,
        temperature=0.1,
    )

def translate_image_prompt(prompt, deadline):
    return cloudflare_text(
        [
            {
                "role": "system",
                "content": (
                    "Convert the user's complete image request into a detailed "
                    "English image-generation prompt. Preserve all requested "
                    "subjects, counts, identity constraints, appearance, clothing, "
                    "pose, expression, setting, objects, colors, lighting, framing, "
                    "composition, style and realism. Do not invent details. "
                    "Return only the English prompt."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        deadline,
        max_tokens=1300,
        temperature=0.1,
    )

def generate_image(prompt, deadline):
    if len(prompt) > 1800:
        prompt = prompt[:1800]
    result = cloudflare_request(
        CLOUDFLARE_IMAGE_MODEL,
        {"prompt": prompt, "steps": 4},
        deadline,
    )
    image_b64 = result.get("image")
    if not image_b64:
        raise RuntimeError("Image provider returned no image")
    raw = base64.b64decode(image_b64, validate=True)
    if not raw:
        raise RuntimeError("Empty image")
    return raw

# =====================================================
# MESSAGE HELPERS / PAGES
# =====================================================

def send_long(chat_id, text):
    chunks = [text[i:i + MAX_MESSAGE_LENGTH] for i in range(0, len(text), MAX_MESSAGE_LENGTH)]
    for i, chunk in enumerate(chunks):
        bot.send_message(
            chat_id, chunk,
            reply_markup=main_keyboard() if i == len(chunks) - 1 else None
        )

def generic_error(chat_id):
    bot.send_message(
        chat_id,
        "⚠️ هشیوار فعلاً نتونست پاسخ رو آماده کنه. کمی بعد دوباره تلاش کن.",
        reply_markup=main_keyboard()
    )

def show_help(chat_id):
    bot.send_message(
        chat_id,
        "ℹ️ راهنمای هشیوار\n\n"
        "💬 گفتگوی جدید: شروع مکالمه تازه و پاک شدن زمینه قبلی\n"
        "🎨 ساخت تصویر: ساخت تصویر و ادامه اصلاح درخواست با پیام‌های بعدی\n"
        "🌐 ترجمه: تشخیص خودکار زبان و ترجمه به فارسی\n"
        "📚 معنی کلمات: معنی، مترادف، متضاد و مثال\n"
        "🪶 ضرب‌المثل‌یاب: ضرب‌المثل‌های مرتبط با موضوع\n"
        "👤 حساب کاربری، 👥 دعوت دوستان و 📊 آمار\n"
        "⚙️ تنظیمات: پاک کردن حافظه گفتگو",
        reply_markup=main_keyboard()
    )

def show_about(chat_id):
    bot.send_message(
        chat_id,
        "💙 هشیوار یک دستیار هوش مصنوعی برای گفت‌وگو، یادگیری، "
        "نوشتن، ترجمه، تحلیل و خلاقیت است.\n\n"
        "هدفش پاسخ‌گویی دقیق، طبیعی و کاربردی است؛ "
        "با حفظ زمینه کارهایی که با هم انجام می‌دهیم.",
        reply_markup=main_keyboard()
    )

def show_settings(chat_id):
    bot.send_message(
        chat_id,
        "⚙️ تنظیمات\n\nمی‌توانی حافظه مکالمه فعلی را پاک کنی.",
        reply_markup=settings_keyboard()
    )

def show_account(message):
    bot.send_message(
        message.chat.id,
        "👤 حساب کاربری\n\n"
        f"شناسه: {message.from_user.id}\n"
        f"نام: {message.from_user.first_name or 'کاربر'}",
        reply_markup=main_keyboard()
    )

def show_stats(chat_id, uid):
    with users_lock:
        total = len(users)
        own = users.get(uid, {}).get("referrals", 0)
        new24 = sum(1 for u in users.values() if u["first_seen"] >= time.time() - 86400)
    if uid in ADMIN_IDS:
        text = (
            "📊 آمار مدیریتی\n\n"
            f"کل کاربران: {total}\n"
            f"کاربران جدید ۲۴ ساعت اخیر: {new24}\n"
            f"کل دعوت‌های موفق: {total_referrals()}\n\n"
            "این آمار تا وقتی پایگاه داده دائمی اضافه نشود، با راه‌اندازی مجدد پاک می‌شود."
        )
    else:
        text = f"📊 دعوت‌های موفق شما: {own}"
    bot.send_message(chat_id, text, reply_markup=main_keyboard())

def show_invite(chat_id, uid):
    try:
        username = bot.get_me().username
        link = f"https://t.me/{username}?start=ref_{uid}"
        with users_lock:
            count = users.get(uid, {}).get("referrals", 0)
        bot.send_message(
            chat_id,
            f"👥 لینک دعوت اختصاصی:\n{link}\n\nدعوت‌های موفق: {count}",
            reply_markup=main_keyboard()
        )
    except Exception:
        logger.exception("Invite error")
        generic_error(chat_id)

def show_image_notice(chat_id):
    if not CF_BASE_URL:
        bot.send_message(chat_id, "⚠️ سرویس تصویرسازی تنظیم نشده است.", reply_markup=main_keyboard())
        return
    set_mode(str(chat_id), "image")
    bot.send_message(
        chat_id,
        "🎨 توضیح تصویرت رو بفرست.\n\n"
        "بعد از ساخت هم می‌تونی تغییرات رو به زبان طبیعی بگی؛ "
        "مثلاً «نورش گرم‌تر باشه» یا «پس‌زمینه آبی بشه».\n\n"
        "برای خروج، «گفتگوی جدید» یا یکی از ابزارهای منو رو انتخاب کن.",
        reply_markup=main_keyboard()
    )

def show_translate_notice(chat_id):
    if not CF_BASE_URL:
        bot.send_message(chat_id, "⚠️ سرویس ترجمه تنظیم نشده است.", reply_markup=main_keyboard())
        return
    set_mode(str(chat_id), "translate")
    bot.send_message(
        chat_id,
        "🌐 متن یا واژه‌ات رو بفرست. زبان مبدأ رو تشخیص می‌دم "
        "و به فارسی روان ترجمه می‌کنم.\n"
        "اگر زبان مقصد دیگری می‌خواهی، همان را در پیامت مشخص کن.",
        reply_markup=main_keyboard()
    )

def show_word_notice(chat_id):
    set_mode(str(chat_id), "word")
    bot.send_message(
        chat_id,
        "📚 کلمه یا عبارت رو بفرست؛ معنی، کاربرد، مترادف، متضاد "
        "و مثال رو برات می‌نویسم.",
        reply_markup=main_keyboard()
    )

def show_proverb_notice(chat_id):
    set_mode(str(chat_id), "proverb")
    bot.send_message(
        chat_id,
        "🪶 موضوع یا مفهوم موردنظرت رو بنویس؛ "
        "ضرب‌المثل‌های واقعی و مرتبط رو همراه معنی می‌گم.",
        reply_markup=main_keyboard()
    )

def word_request(word):
    return (
        "در نقش فرهنگ‌نویس دقیق پاسخ بده. درباره این واژه توضیح بده: "
        f"{word}\n\nمعنی ساده و دقیق، کاربردها، مترادف‌ها، متضادها، "
        "ریشه فقط در صورت اطمینان و دو مثال طبیعی ارائه کن. "
        "اگر واژه چند معنی دارد، آن‌ها را جدا کن و چیزی جعل نکن."
    )

def proverb_request(topic):
    return (
        f"برای موضوع «{topic}» ضرب‌المثل‌های واقعی و معتبر فارسی پیشنهاد کن. "
        "برای هرکدام معنی کوتاه بنویس. جمله ابداعی را ضرب‌المثل معرفی نکن؛ "
        "اگر نمونه معتبر کم است، تعداد کمتری ارائه بده."
    )

# =====================================================
# IMAGE TASK CONTINUITY
# =====================================================

def handle_image(chat_id, key, user_text):
    if not CF_BASE_URL:
        bot.send_message(chat_id, "⚠️ سرویس تصویرسازی در دسترس نیست.", reply_markup=main_keyboard())
        return

    with active_image_lock:
        previous = active_image_prompts.get(key, "")
        if previous:
            combined = (
                "Previous image-generation prompt:\n" + previous +
                "\n\nUser's requested revision:\n" + user_text +
                "\n\nCreate a new image that preserves all previous requirements "
                "except where the user explicitly changes them."
            )
        else:
            combined = user_text

    waiting = bot.send_message(chat_id, "🎨 دارم تصویرت رو آماده می‌کنم... ⚡")
    deadline = time.monotonic() + IMAGE_WAIT
    try:
        english = translate_image_prompt(combined, deadline)
        image_bytes = generate_image(english, deadline)
        check_deadline(deadline)
        with active_image_lock:
            active_image_prompts[key] = combined
        photo = io.BytesIO(image_bytes)
        photo.name = "hoshivar-image.png"
        try:
            bot.delete_message(chat_id, waiting.message_id)
        except Exception:
            pass
        bot.send_photo(
            chat_id, photo,
            caption="✨ تصویر آماده شد. اگر تغییری می‌خواهی، توضیح بده تا نسخه تازه‌ای بسازم. 💙",
            reply_markup=main_keyboard(),
            timeout=35
        )
        # Keep image mode active for follow-up refinements.
        set_mode(key, "image")
    except Exception:
        logger.exception("Image generation failed")
        try:
            bot.delete_message(chat_id, waiting.message_id)
        except Exception:
            pass
        bot.send_message(
            chat_id,
            "⚠️ ساخت تصویر انجام نشد. اگر خواستی دوباره تلاش کن؛ "
            "زمینه درخواستت حفظ شده است.",
            reply_markup=main_keyboard()
        )
        set_mode(key, "image")

# =====================================================
# COMMANDS
# =====================================================

@bot.message_handler(commands=["start"])
def start(message):
    key = str(message.chat.id)
    clear_task(key)
    clear_history(key)
    referral = None
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) > 1 and parts[1].startswith("ref_"):
        try:
            referral = int(parts[1][4:])
        except ValueError:
            pass
    register_user(message.from_user, referral)
    send_home(message.chat.id)

@bot.message_handler(commands=["help"])
def help_command(message):
    register_user(message.from_user)
    show_help(message.chat.id)

@bot.message_handler(commands=["reset"])
def reset_command(message):
    key = str(message.chat.id)
    clear_history(key)
    clear_task(key)
    bot.send_message(message.chat.id, "🧹 حافظه و کار فعال پاک شد.", reply_markup=main_keyboard())

@bot.message_handler(commands=["stats"])
def stats_command(message):
    register_user(message.from_user)
    show_stats(message.chat.id, message.from_user.id)

# =====================================================
# CALLBACKS
# =====================================================

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass
    try:
        chat_id = call.message.chat.id
        key = str(chat_id)
        action = call.data
        register_user(call.from_user)

        if action in ("home", "new_chat"):
            clear_task(key)
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
                f"👤 حساب کاربری\n\nشناسه: {call.from_user.id}\nنام: {call.from_user.first_name or 'کاربر'}",
                reply_markup=main_keyboard()
            )
        elif action == "image":
            show_image_notice(chat_id)
        elif action == "translate":
            show_translate_notice(chat_id)
        elif action == "word":
            show_word_notice(chat_id)
        elif action == "proverb":
            show_proverb_notice(chat_id)
        elif action == "invite":
            show_invite(chat_id, call.from_user.id)
        elif action == "stats":
            show_stats(chat_id, call.from_user.id)
        elif action == "reset_memory":
            clear_history(key)
            clear_task(key)
            bot.send_message(chat_id, "🧹 حافظه پاک شد.", reply_markup=main_keyboard())
    except Exception:
        logger.exception("Callback handler failed")

# =====================================================
# TEXT HANDLER
# =====================================================

@bot.message_handler(content_types=["text"])
def handle_text(message):
    chat_id = message.chat.id
    key = str(chat_id)
    text = (message.text or "").strip()
    if not text:
        return

    register_user(message.from_user)

    # Main keyboard actions explicitly switch/cancel active tasks.
    if text == BTN_CHAT:
        clear_task(key)
        clear_history(key)
        bot.send_message(chat_id, "💬 گفتگوی تازه شروع شد.", reply_markup=main_keyboard())
        return
    if text == BTN_IMAGE:
        show_image_notice(chat_id)
        return
    if text == BTN_TRANSLATE:
        clear_task(key)
        show_translate_notice(chat_id)
        return
    if text == BTN_WORD:
        clear_task(key)
        show_word_notice(chat_id)
        return
    if text == BTN_PROVERB:
        clear_task(key)
        show_proverb_notice(chat_id)
        return
    if text == BTN_ACCOUNT:
        clear_task(key)
        show_account(message)
        return
    if text == BTN_STATS:
        clear_task(key)
        show_stats(chat_id, message.from_user.id)
        return
    if text == BTN_INVITE:
        clear_task(key)
        show_invite(chat_id, message.from_user.id)
        return
    if text == BTN_HELP:
        clear_task(key)
        show_help(chat_id)
        return
    if text == BTN_SETTINGS:
        clear_task(key)
        show_settings(chat_id)
        return

    mode = get_mode(key)

    if mode == "image":
        handle_image(chat_id, key, text)
        return

    if mode == "translate":
        clear_mode(key)
        waiting = bot.send_message(chat_id, "🌐 در حال ترجمه...")
        try:
            with ai_semaphore:
                result = translate_text(text, time.monotonic() + MAX_WAIT)
            try:
                bot.delete_message(chat_id, waiting.message_id)
            except Exception:
                pass
            send_long(chat_id, "🌐 ترجمه:\n\n" + result)
        except Exception:
            logger.exception("Translation failed")
            try:
                bot.delete_message(chat_id, waiting.message_id)
            except Exception:
                pass
            generic_error(chat_id)
        return

    if mode == "word":
        clear_mode(key)
        text = word_request(text)
    elif mode == "proverb":
        clear_mode(key)
        text = proverb_request(text)

    try:
        waiting = bot.send_message(chat_id, "🧠 هشیوار در حال فکر کردنه... ⚡")
    except Exception:
        logger.exception("Unable to send waiting message")
        return

    deadline = time.monotonic() + MAX_WAIT
    try:
        with chat_lock(key):
            with ai_semaphore:
                answer = ask_ai(text, key, deadline)
        try:
            bot.delete_message(chat_id, waiting.message_id)
        except Exception:
            pass
        send_long(chat_id, answer)
    except Exception:
        logger.exception("AI request failed")
        try:
            bot.delete_message(chat_id, waiting.message_id)
        except Exception:
            pass
        generic_error(chat_id)

# =====================================================
# WEBHOOK SERVER
# =====================================================

class WebhookHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        if self.path in ("/", "/health"):
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_error(404)

    def do_GET(self):
        if self.path in ("/", "/health"):
            body = b"Hoshivar AI Bot is running"
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
        if self.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
            self.send_error(403)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1_000_000:
                self.send_error(400)
                return
            raw = self.rfile.read(length)
            update = telebot.types.Update.de_json(raw.decode("utf-8"))
            bot.process_new_updates([update])
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        except Exception:
            logger.exception("Webhook update failed")
            self.send_error(500)

    def log_message(self, fmt, *args):
        logger.info("WEBHOOK: %s", fmt % args)

# =====================================================
# START
# =====================================================

if __name__ == "__main__":
    logger.info("Starting Hoshivar AI")
    logger.info("Primary text model: 1xAI / %s", X1_MODEL)
    logger.info("Cloudflare configured: %s", bool(CF_BASE_URL))
    logger.info("Concurrent AI limit: %s", MAX_CONCURRENT_AI)

    server = ThreadingHTTPServer(("0.0.0.0", PORT), WebhookHandler)
    server.daemon_threads = True

    try:
        result = bot.set_webhook(
            url=WEBHOOK_URL,
            secret_token=WEBHOOK_SECRET,
            allowed_updates=["message", "callback_query"],
        )
        if not result:
            raise RuntimeError("Telegram webhook setup failed")
        logger.info("Webhook configured; Hoshivar is live")
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down Hoshivar")
    except Exception:
        logger.exception("Startup failed")
        raise
    finally:
        server.server_close()
        http.close()
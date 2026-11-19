import os
import re
import io
import time
import base64
import sqlite3
import logging
import threading
import requests
import telebot
from PIL import Image, ImageEnhance, ImageFilter

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from telebot.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

# =====================================================
# HOSHIVAR | CONFIGURATION
# =====================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
X1_API_KEY = os.getenv("X1_API_KEY") or os.getenv("1XAI_API_KEY")
X1_BASE_URL = os.getenv("X1_BASE_URL", "https://1xai.ir/v1").rstrip("/")
X1_MODEL = os.getenv("X1_MODEL", "gpt-4o-mini")

CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID")
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN")
CLOUDFLARE_TEXT_MODEL = os.getenv("CLOUDFLARE_TEXT_MODEL", "@cf/meta/llama-3.1-8b-instruct-fast")
CLOUDFLARE_IMAGE_MODEL = os.getenv("CLOUDFLARE_IMAGE_MODEL", "@cf/black-forest-labs/flux-1-schnell")
CLOUDFLARE_EDIT_IMAGE_MODEL = os.getenv("CLOUDFLARE_EDIT_IMAGE_MODEL", "@cf/black-forest-labs/flux-2-klein-4b")

PORT = int(os.getenv("PORT", "10000"))
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
WEBHOOK_PATH = "/telegram-webhook"
DB_PATH = os.getenv("DB_PATH", "hoshivar.db")
MAX_WAIT = int(os.getenv("MAX_WAIT", "55"))
IMAGE_WAIT = int(os.getenv("IMAGE_WAIT", "110"))
MAX_HISTORY = int(os.getenv("MAX_HISTORY", "12"))
MAX_MESSAGE_LENGTH = 3900
MAX_CONCURRENT_AI = int(os.getenv("MAX_CONCURRENT_AI", "8"))
MAX_USERS_BROADCAST_BATCH = 20

ADMIN_IDS = {
    int(v.strip()) for v in os.getenv("ADMIN_IDS", "").split(",")
    if v.strip().isdigit()
}

for env_name, env_value in (
    ("BOT_TOKEN", BOT_TOKEN),
    ("X1_API_KEY", X1_API_KEY),
    ("WEBHOOK_SECRET", WEBHOOK_SECRET),
    ("RENDER_EXTERNAL_URL", RENDER_URL),
):
    if not env_value:
        raise RuntimeError(f"Required environment variable is missing: {env_name}")

if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", WEBHOOK_SECRET):
    raise RuntimeError("WEBHOOK_SECRET must contain only letters, numbers, _ or -")
if bool(CLOUDFLARE_ACCOUNT_ID) != bool(CLOUDFLARE_API_TOKEN):
    raise RuntimeError("Set both Cloudflare credentials or leave both empty")

X1_CHAT_URL = f"{X1_BASE_URL}/chat/completions"
CF_BASE_URL = (
    f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/run"
    if CLOUDFLARE_ACCOUNT_ID else None
)
WEBHOOK_URL = RENDER_URL.rstrip("/") + WEBHOOK_PATH

# =====================================================
# LOGGING / CLIENTS
# =====================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("HOSHIVAR")
http = requests.Session()
http.headers.update({"User-Agent": "Hoshivar-Telegram-Bot/3.0"})
bot = telebot.TeleBot(BOT_TOKEN, threaded=True, num_threads=8)
ai_semaphore = threading.BoundedSemaphore(MAX_CONCURRENT_AI)
db_lock = threading.RLock()
chat_locks = {}
chat_locks_guard = threading.Lock()
pending_image_refs = {}  # per-user Telegram photo awaiting an edit instruction
pending_image_refs_lock = threading.Lock()

# =====================================================
# PERSONALITY: WARM, WITTY, WISE, NEVER ROBOTIC
# =====================================================
SYSTEM_PROMPT = r"""
تو «هشیوار» هستی؛ یک دستیار هوشمند با شخصیت مستقل، خردمند، خوش‌ذوق، شیرین‌زبان، صمیمی و دقیق.

هویت و لحن:
- مثل یک همراه باهوش و خوش‌صحبت حرف بزن؛ نه مثل فرم اداری، نه مثل ربات خشک و نه مثل شخصیت نمایشی و اغراق‌آمیز.
- گرما و مهربانی را در انتخاب واژه‌ها نشان بده. گاهی شوخی ظریف، بازی زبانی یا جمله‌ای بانمک به‌جا استفاده کن؛ اما لوس، کودکانه یا تصنعی نشو.
- خردمند باش، نه خطیب دائمی. فقط وقتی به فهم موضوع کمک می‌کند از نکته حکیمانه، مثال یا تشبیه استفاده کن.
- در موقعیت جدی، ناراحتی، خطر یا تصمیم مهم، شوخی را کنار بگذار و آرام، محترمانه و دقیق باش.
- به جای تعارف‌های تکراری، واکنش متناسب و تازه نشان بده. لازم نیست هر پاسخ را با پیشنهاد یا جمله احساسی تمام کنی.
- اگر کاربر تشکر کرد، طبیعی و کوتاه جواب بده. اگر فقط سلام کرد، گرم و سرزنده پاسخ بده و راه شروع گفتگو را باز بگذار.
- وقتی کاربر از مهربانی یا کمکت تشکر می‌کند، در موقعیت مناسب می‌توانی با لحنی گرم و خودمانی بگویی: «ممنون از مهربونیت! 💙 حالا بگو ببینم، چطور می‌تونم کمکت کنم؟»
- این جمله را عیناً و پشت‌سرهم تکرار نکن؛ پاسخ را با توجه به متن گفتگو متنوع و طبیعی نگه دار و اگر گفتگو ادامه دارد، بی‌دلیل آن را به شروع دوباره تبدیل نکن.
- پاسخ‌های ساده را ساده و کوتاه نگه دار؛ موضوعات پیچیده را کامل، روشن و مرحله‌بندی‌شده توضیح بده.
- در پاسخ‌های تولیدشده از ایموجی متناسب با موضوع و حال‌وهوای گفتگو استفاده کن؛ نه اجباری و نه بیش‌ازحد.
- در متن‌های ثابت منو و راهنما ممکن است ایموجی‌های متنوع و شاد بیشتری ببینی؛ اما پاسخ عادی باید خوانا و متناسب بماند.

دقت و صداقت:
- حقیقت و دقت را به موافقت بی‌دلیل ترجیح بده. اشتباه کاربر را محترمانه و روشن اصلاح کن.
- چیزی را جعل نکن؛ منبع، آمار، تجربه شخصی، خاطره یا قابلیت انجام‌نشده نساز.
- اگر مطمئن نیستی، سطح اطمینان و محدودیت را صریح بگو.
- چاپلوسی، نصیحت بی‌مورد، تکرار، عبارت‌های کلیشه‌ای و پرگویی را کنار بگذار.
- به شخصیت فرد حمله نکن؛ ایده و استدلال را نقد کن.

پیوستگی:
- زمینه کار فعال و جزئیات قبلی را حفظ کن. اگر کاربر گفت «این یکی رو هم ترجمه کن»، ترجمه را ادامه بده.
- اگر در حال اصلاح تصویر، متن یا پاسخ است، خروجی قبلی و خواسته‌های قبلی را مبنا قرار بده و فقط تغییر خواسته‌شده را اعمال کن.
- بعد از انجام یک کار، خودسرانه به گفتگوی عادی یا ابزار دیگری نپر.
- اگر کاربر صریحاً ابزار دیگری انتخاب کرد، موضوع را عوض کرد یا گفت گفتگوی جدید، مطابق همان عمل کن.
- ترجیحات دائمی را بدون اجازه روشن کاربر تغییر نده.

زبان:
- زبان پاسخ را با زبان کاربر هماهنگ کن. فارسی را طبیعی، روان و درست بنویس.
- در ترجمه، معنی، لحن، نام‌ها، عددها و قالب متن را حفظ کن؛ خلاصه‌سازی نکن مگر خواسته شود.
"""

# =====================================================
# DATABASE: persistent users, history, modes, counters
# =====================================================
@contextmanager
def db_conn():
    with db_lock:
        conn = sqlite3.connect(DB_PATH, timeout=20)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def init_db():
    with db_conn() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            first_name TEXT NOT NULL DEFAULT '',
            username TEXT NOT NULL DEFAULT '',
            first_seen REAL NOT NULL,
            last_seen REAL NOT NULL,
            referred_by INTEGER,
            is_blocked INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS chats (
            chat_key TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            mode TEXT NOT NULL DEFAULT 'chat',
            image_prompt TEXT NOT NULL DEFAULT '',
            updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_key TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_history_chat ON history(chat_key, id);
        CREATE TABLE IF NOT EXISTS usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            tool TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_usage_created ON usage(created_at);
        CREATE TABLE IF NOT EXISTS settings (
            name TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS profile_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            memory_text TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_profile_memory_user ON profile_memory(user_id, id);
        """)


def user_key(message_or_call):
    chat = message_or_call.chat if hasattr(message_or_call, "chat") else message_or_call.message.chat
    user = message_or_call.from_user
    # Per-user state prevents people in group chats from sharing task/history.
    return f"{chat.id}:{user.id}"


def lock_for(key):
    with chat_locks_guard:
        return chat_locks.setdefault(key, threading.Lock())


def register_user(user, referral_id=None):
    now = time.time()
    with db_conn() as conn:
        existing = conn.execute("SELECT user_id FROM users WHERE user_id=?", (user.id,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE users SET first_name=?, username=?, last_seen=? WHERE user_id=?",
                (user.first_name or "کاربر", user.username or "", now, user.id),
            )
            return False
        valid_ref = None
        if referral_id and referral_id != user.id:
            ref = conn.execute("SELECT user_id FROM users WHERE user_id=?", (referral_id,)).fetchone()
            if ref:
                valid_ref = referral_id
        conn.execute(
            "INSERT INTO users(user_id,first_name,username,first_seen,last_seen,referred_by) VALUES(?,?,?,?,?,?)",
            (user.id, user.first_name or "کاربر", user.username or "", now, now, valid_ref),
        )
        return True


def touch_user(user):
    now = time.time()
    with db_conn() as conn:
        conn.execute(
            "UPDATE users SET first_name=?, username=?, last_seen=? WHERE user_id=?",
            (user.first_name or "کاربر", user.username or "", now, user.id),
        )
        row = conn.execute("SELECT is_blocked FROM users WHERE user_id=?", (user.id,)).fetchone()
        return bool(row["is_blocked"]) if row else False


def get_mode(key):
    with db_conn() as conn:
        row = conn.execute("SELECT mode FROM chats WHERE chat_key=?", (key,)).fetchone()
        return row["mode"] if row else "chat"


def set_mode(key, user_id, chat_id, mode):
    with db_conn() as conn:
        conn.execute("""
            INSERT INTO chats(chat_key,user_id,chat_id,mode,updated_at)
            VALUES(?,?,?,?,?)
            ON CONFLICT(chat_key) DO UPDATE SET mode=excluded.mode, updated_at=excluded.updated_at
        """, (key, user_id, chat_id, mode, time.time()))


def get_image_prompt(key):
    with db_conn() as conn:
        row = conn.execute("SELECT image_prompt FROM chats WHERE chat_key=?", (key,)).fetchone()
        return row["image_prompt"] if row else ""


def set_image_prompt(key, user_id, chat_id, prompt):
    with db_conn() as conn:
        conn.execute("""
            INSERT INTO chats(chat_key,user_id,chat_id,mode,image_prompt,updated_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(chat_key) DO UPDATE SET image_prompt=excluded.image_prompt, updated_at=excluded.updated_at
        """, (key, user_id, chat_id, "image", prompt, time.time()))


def clear_task(key, user_id=None, chat_id=None):
    with db_conn() as conn:
        if user_id is None or chat_id is None:
            conn.execute("DELETE FROM chats WHERE chat_key=?", (key,))
        else:
            conn.execute("""
                INSERT INTO chats(chat_key,user_id,chat_id,mode,image_prompt,updated_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(chat_key) DO UPDATE SET mode='chat', image_prompt='', updated_at=excluded.updated_at
            """, (key, user_id, chat_id, "chat", "", time.time()))


def get_history(key):
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT role,content FROM history WHERE chat_key=? ORDER BY id DESC LIMIT ?",
            (key, MAX_HISTORY * 2),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def save_history(key, user_text, answer):
    now = time.time()
    with db_conn() as conn:
        conn.executemany(
            "INSERT INTO history(chat_key,role,content,created_at) VALUES(?,?,?,?)",
            [(key, "user", user_text, now), (key, "assistant", answer, now)],
        )
        conn.execute("""
            DELETE FROM history WHERE chat_key=? AND id NOT IN
            (SELECT id FROM history WHERE chat_key=? ORDER BY id DESC LIMIT ?)
        """, (key, key, MAX_HISTORY * 2))


def clear_history(key):
    with db_conn() as conn:
        conn.execute("DELETE FROM history WHERE chat_key=?", (key,))


def log_usage(user_id, tool):
    with db_conn() as conn:
        conn.execute("INSERT INTO usage(user_id,tool,created_at) VALUES(?,?,?)", (user_id, tool, time.time()))


def get_profile_memories(user_id):
    with db_conn() as conn:
        rows = conn.execute("SELECT memory_text FROM profile_memory WHERE user_id=? ORDER BY id DESC LIMIT 30", (user_id,)).fetchall()
    return [row["memory_text"] for row in reversed(rows)]


def add_profile_memory(user_id, memory_text):
    memory_text = memory_text.strip()[:700]
    if not memory_text:
        return
    with db_conn() as conn:
        exists = conn.execute("SELECT id FROM profile_memory WHERE user_id=? AND memory_text=?", (user_id, memory_text)).fetchone()
        if not exists:
            conn.execute("INSERT INTO profile_memory(user_id,memory_text,created_at) VALUES(?,?,?)", (user_id, memory_text, time.time()))


def delete_profile_memory(user_id, phrase=None):
    with db_conn() as conn:
        if phrase:
            conn.execute("DELETE FROM profile_memory WHERE user_id=? AND memory_text LIKE ?", (user_id, f"%{phrase}%"))
        else:
            conn.execute("DELETE FROM profile_memory WHERE user_id=?", (user_id,))


def show_profile_memory(chat_id, user_id):
    items = get_profile_memories(user_id)
    if not items:
        text = "حافظه بلندمدتت هنوز خالیه. اگر خواستی چیزی رو نگه دارم، با «یادت باشه ...» بهم بگو. 💙"
    else:
        text = "🧠 چیزهایی که با اجازه‌ات برای شناخت بهتر ترجیحاتت نگه داشته‌ام:\n\n" + "\n".join(f"• {item}" for item in items)
        text += "\n\nبرای حذف یک مورد بنویس: «فراموش کن [عبارت]»؛ برای پاک‌کردن همه بنویس: «همه حافظه‌ام رو پاک کن»."
    bot.send_message(chat_id, text, reply_markup=main_keyboard(user_id))


def is_maintenance():
    with db_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE name='maintenance'").fetchone()
        return bool(row and row["value"] == "1")


def set_maintenance(enabled):
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO settings(name,value) VALUES('maintenance',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",
            ("1" if enabled else "0",),
        )

# =====================================================
# TELEGRAM MENU: SIMPLE HOME + DIRECT TOOL LIST
# =====================================================
BTN_CHAT = "💬 گفتگوی جدید"
BTN_TOOLS = "🧰 ابزارها"
BTN_ACCOUNT = "👤 حساب کاربری"
BTN_INVITE = "🎁 دعوت دوستان"
BTN_SETTINGS = "⚙️ تنظیمات"
BTN_HELP = "ℹ️ راهنما"
BTN_MENU = "🏠 منوی اصلی"

# Hoshivar's named tools
BTN_IMAGE = "🎨 خیال‌نگار"
BTN_TRANSLATE = "🌍 زبان‌یار"
BTN_WORD = "📖 واژه‌کاو"
BTN_PROVERB = "📜 مثل‌دان"
BTN_STORY = "🪄 قصه‌پرداز"
BTN_TEXT = "✍️ متن‌آرا"
BTN_STATS = "📊 آمار ربات"


def main_keyboard(uid=None):
    """Persistent home navigation. Invite is directly accessible here."""
    k = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2, is_persistent=True)
    k.row(KeyboardButton(BTN_CHAT), KeyboardButton(BTN_TOOLS))
    k.row(KeyboardButton(BTN_ACCOUNT), KeyboardButton(BTN_INVITE))
    k.row(KeyboardButton(BTN_SETTINGS), KeyboardButton(BTN_HELP))
    return k


def inline_keyboard(uid=None):
    k = InlineKeyboardMarkup(row_width=2)
    k.row(InlineKeyboardButton("🧠 هم‌فکر | گفتگوی هوشمند", callback_data="new_chat"))
    k.row(InlineKeyboardButton("🧰 جعبه‌ابزار هشیوار", callback_data="tools"))
    k.row(InlineKeyboardButton("👤 حساب کاربری", callback_data="account"),
          InlineKeyboardButton("🎁 دعوت دوستان", callback_data="invite"))
    k.row(InlineKeyboardButton("⚙️ تنظیمات", callback_data="settings"),
          InlineKeyboardButton("ℹ️ راهنما", callback_data="help"))
    k.row(InlineKeyboardButton("💙 درباره هشیوار", callback_data="about"))
    if uid in ADMIN_IDS:
        k.row(InlineKeyboardButton("👑 پنل مدیریت", callback_data="admin"))
    return k


def tools_keyboard():
    """Show only implemented tools; no placeholder or unavailable features."""
    k = InlineKeyboardMarkup(row_width=2)
    k.row(InlineKeyboardButton("🌍 زبان‌یار", callback_data="translate"),
          InlineKeyboardButton("📖 واژه‌کاو", callback_data="word"))
    k.row(InlineKeyboardButton("📜 مثل‌دان", callback_data="proverb"),
          InlineKeyboardButton("🎨 خیال‌نگار", callback_data="image"))
    k.row(InlineKeyboardButton("🪄 قصه‌پرداز", callback_data="story"),
          InlineKeyboardButton("✍️ متن‌آرا", callback_data="textcraft"))
    k.row(InlineKeyboardButton("🏠 صفحه اصلی", callback_data="home"))
    return k


def image_studio_keyboard():
    k = InlineKeyboardMarkup(row_width=2)
    k.row(InlineKeyboardButton("🎨 خیال‌نگار | ساخت تصویر", callback_data="img_generate"))
    k.row(InlineKeyboardButton("🪄 دگرگون‌ساز | ویرایش عکس", callback_data="img_edit"),
          InlineKeyboardButton("🏞️ پس‌زمینه‌چین", callback_data="img_background"))
    k.row(InlineKeyboardButton("✨ شفاف‌ساز | بهبود وضوح", callback_data="img_enhance"))
    k.row(InlineKeyboardButton("🏠 صفحه اصلی", callback_data="home"))
    return k


def show_image_studio(chat_id, uid):
    bot.send_message(
        chat_id,
        "🖼️ استودیوی تصویر هشیوار\n\n"
        "🎨 خیال‌نگار: ساخت تصویر از توضیح متنی، بدون نیاز به عکس.\n"
        "🪄 دگرگون‌ساز: ویرایش عکس با حفظ سوژه‌های اصلی.\n"
        "🏞️ پس‌زمینه‌چین: تغییر محیط و پس‌زمینه عکس.\n"
        "✨ شفاف‌ساز: افزایش وضوح و شارپ‌سازی پایه عکس.\n\n"
        "یکی از بخش‌ها رو انتخاب کن تا وارد همون حالت بشی. 💙🤍💚",
        reply_markup=image_studio_keyboard(),
    )


def select_image_mode(chat_id, uid, key, mode):
    set_mode(key, uid, chat_id, mode)
    set_image_prompt(key, uid, chat_id, "")
    with pending_image_refs_lock:
        pending_image_refs.pop(key, None)
    messages = {
        "image_generate": ("🎨 خیال‌نگار فعال شد", "ایده‌ات رو به فارسی توصیف کن تا تصویر تازه بسازم. مثلاً: یک مسجد باشکوه در دشتی سرسبز با کوه‌های مه‌آلود، نور صبح و سبک کاملاً واقع‌گرایانه."),
        "image_edit": ("🪄 دگرگون‌ساز فعال شد", "اول عکس رو بفرست؛ می‌تونی توضیح رو همراه عکس یا در پیام بعدی بنویسی. فقط تغییرهایی رو انجام می‌دم که درخواست می‌کنی."),
        "image_background": ("🏞️ پس‌زمینه‌چین فعال شد", "عکس رو بفرست و بگو پس‌زمینه به چه محیطی تبدیل بشه؛ مثلاً ساحل، جنگل، مسجد یا فضای آتلیه. سوژه اصلی باید تا حد امکان حفظ بشه."),
        "image_enhance": ("✨ شفاف‌ساز فعال شد", "عکس رو بفرست تا وضوح، اندازه و شارپ‌بودن آن را با پردازش پایه بهتر کنم. این روش بازسازی هوش مصنوعی جزئیات ازدست‌رفته نیست."),
    }
    title, desc = messages[mode]
    bot.send_message(chat_id, f"{title}\n\n{desc}\n\nبرای رفتن به ابزار دیگر، خودت از منوی ابزارها انتخاب کن.", reply_markup=main_keyboard(uid))


def account_keyboard():
    k = InlineKeyboardMarkup(row_width=2)
    k.row(InlineKeyboardButton("📊 آمار و فعالیت", callback_data="stats"),
          InlineKeyboardButton("🎁 دعوت دوستان", callback_data="invite"))
    k.row(InlineKeyboardButton("🏠 صفحه اصلی", callback_data="home"))
    return k


def settings_keyboard():
    k = InlineKeyboardMarkup()
    k.add(InlineKeyboardButton("🧹 پاک‌کردن حافظه همین گفتگو", callback_data="reset_memory"))
    k.add(InlineKeyboardButton("🏠 صفحه اصلی", callback_data="home"))
    return k


def admin_keyboard():
    k = InlineKeyboardMarkup(row_width=2)
    k.row(InlineKeyboardButton("📊 آمار کامل", callback_data="admin_stats"), InlineKeyboardButton("🩺 سلامت سرویس‌ها", callback_data="admin_health"))
    k.row(InlineKeyboardButton("🔧 روشن/خاموش تعمیرات", callback_data="admin_maintenance"))
    k.row(InlineKeyboardButton("🔙 صفحه اصلی", callback_data="home"))
    return k


def send_home(chat_id, uid):
    bot.send_message(
        chat_id,
        "💙💚🤍 به هشیوار خوش اومدی! ✨\n\n"
        "🧠 خرد رهنمای و خرد دلگشای\n"
        "خرد دست گیرد به هر دو سرای\n"
        "📜 حکیم ابوالقاسم فردوسی\n\n"
        "من هشیوارم؛ همراهت برای فکر کردن، یاد گرفتن و ساختن چیزهای تازه. 🌱\n"
        "⚡ از گفت‌وگوی تازه شروع کن یا سری به جعبه‌ابزارم بزن! 🪄",
        reply_markup=main_keyboard(uid),
    )
    bot.send_message(chat_id, "🌈 از کجا شروع کنیم؟", reply_markup=inline_keyboard(uid))

# =====================================================
# PROVIDERS
# =====================================================
class ProviderError(Exception):
    pass


def timeout_for(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 1:
        raise TimeoutError("Request deadline exceeded")
    connect = min(5.0, max(1.0, remaining / 4))
    return connect, max(1.0, remaining - connect - 0.5)


def check_deadline(deadline):
    if time.monotonic() >= deadline:
        raise TimeoutError("Request deadline exceeded")


def error_detail(response):
    try:
        data = response.json()
        return str(data.get("error", data.get("errors", data)))[:1000]
    except Exception:
        return (response.text or "Unknown error")[:1000]


def ask_1xai(user_text, key, deadline, user_id=None):
    system_text = SYSTEM_PROMPT
    if user_id is not None:
        saved = get_profile_memories(user_id)
        if saved:
            system_text += "\n\nاطلاعات بلندمدتی که کاربر صریحاً اجازه داده به خاطر بسپاری (فقط در موارد مرتبط استفاده کن):\n- " + "\n- ".join(saved)
    messages = [{"role": "system", "content": system_text}]
    messages.extend(get_history(key))
    messages.append({"role": "user", "content": user_text})
    response = http.post(
        X1_CHAT_URL,
        headers={"Authorization": f"Bearer {X1_API_KEY}", "Content-Type": "application/json"},
        json={"model": X1_MODEL, "messages": messages, "temperature": 0.78, "max_tokens": 2000},
        timeout=timeout_for(deadline),
    )
    check_deadline(deadline)
    if not response.ok:
        raise ProviderError(f"1xAI HTTP {response.status_code}: {error_detail(response)}")
    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        raise ProviderError("1xAI response had no choices")
    content = choices[0].get("message", {}).get("content", "")
    if isinstance(content, list):
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    if not isinstance(content, str) or not content.strip():
        raise ProviderError("1xAI returned empty content")
    return content.strip()


def cloudflare_request(model, payload, deadline):
    if not CF_BASE_URL:
        raise RuntimeError("Cloudflare is not configured")
    response = http.post(
        f"{CF_BASE_URL}/{model}",
        headers={"Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}", "Content-Type": "application/json"},
        json=payload,
        timeout=timeout_for(deadline),
    )
    check_deadline(deadline)
    if not response.ok:
        raise ProviderError(f"Cloudflare HTTP {response.status_code}: {error_detail(response)}")
    data = response.json()
    if data.get("success") is False:
        raise ProviderError(str(data.get("errors") or "Cloudflare request failed"))
    return data.get("result", data)


def cloudflare_text(messages, deadline, max_tokens=1800, temperature=0.2):
    result = cloudflare_request(CLOUDFLARE_TEXT_MODEL, {"messages": messages, "max_tokens": max_tokens, "temperature": temperature}, deadline)
    answer = result.get("response", "")
    if not answer and result.get("choices"):
        answer = result["choices"][0].get("message", {}).get("content", "")
    if not isinstance(answer, str) or not answer.strip():
        raise ProviderError("Cloudflare returned empty text")
    return answer.strip()


def ask_ai(user_text, key, deadline, user_id=None):
    try:
        answer = ask_1xai(user_text, key, deadline, user_id)
        logger.info("Text provider: 1xAI")
    except Exception as primary_error:
        logger.warning("1xAI failed; using Cloudflare fallback: %s", primary_error)
        check_deadline(deadline)
        system_text = SYSTEM_PROMPT
        if user_id is not None:
            saved = get_profile_memories(user_id)
            if saved:
                system_text += "\n\nاطلاعات بلندمدت مورد تأیید کاربر (فقط در موارد مرتبط):\n- " + "\n- ".join(saved)
        messages = [{"role": "system", "content": system_text}] + get_history(key) + [{"role": "user", "content": user_text}]
        answer = cloudflare_text(messages, deadline)
        logger.info("Text provider: Cloudflare fallback")
    check_deadline(deadline)
    save_history(key, user_text, answer)
    return answer


def translate_text(text, deadline):
    return cloudflare_text([
        {"role": "system", "content": (
            "You are an expert multilingual translator. Detect source language. "
            "Translate the complete input into natural Persian unless the user explicitly "
            "specifies another target language. Preserve meaning, register, emotion, "
            "names, numbers, formatting and paragraphs. Do not summarize, explain or "
            "add invented context. Return only the translation."
        )},
        {"role": "user", "content": text},
    ], deadline, max_tokens=2500, temperature=0.1)


def translate_image_prompt(prompt, deadline):
    return cloudflare_text([
        {"role": "system", "content": (
            "You are Hoshivar's professional image-prompt engineer. Convert the user's Persian request "
            "into a polished, production-ready English image prompt. Improve clarity, composition, camera, "
            "lighting and material detail where useful, but never invent extra subjects, props, people, "
            "architecture, weather or styling. Preserve the exact requested number of people. "
            "For photo editing, explicitly treat the input photo as the immutable source of truth: keep "
            "the same identities, faces, expressions, age, skin tone, body shape, pose and clothing unless "
            "the user explicitly asks to change one of them. Change only the requested region or property. "
            "For background replacement, preserve foreground people and objects; replace only the background "
            "with the exact environment requested, including its weather, vegetation and lighting. "
            "For text-to-image, follow the user's subject and scene literally and add only professional "
            "photographic details that do not alter the concept. Specify photorealism only when appropriate. "
            "Avoid contradictory instructions and avoid generic filler. Return only the final English prompt."
        )},
        {"role": "user", "content": prompt},
    ], deadline, max_tokens=1700, temperature=0.0)


def prepare_reference_image(image_bytes):
    """Resize a Telegram photo to the documented <=512px input limit."""
    try:
        with Image.open(io.BytesIO(image_bytes)) as source:
            image = source.convert("RGB")
            image.thumbnail((510, 510), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=90, optimize=True)
            return output.getvalue()
    except Exception as exc:
        raise ProviderError("Could not decode the reference image") from exc


def generate_image(prompt, deadline, reference_image=None):
    if reference_image:
        # FLUX.2 Klein 4B accepts image references via multipart form data.
        # Input images must be no larger than 512x512 pixels.
        prepared = prepare_reference_image(reference_image)
        response = http.post(
            f"{CF_BASE_URL}/{CLOUDFLARE_EDIT_IMAGE_MODEL}",
            headers={"Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}"},
            data={"prompt": prompt[:1800], "width": "1024", "height": "1024"},
            files={"input_image_0": ("reference.jpg", prepared, "image/jpeg")},
            timeout=timeout_for(deadline),
        )
        check_deadline(deadline)
        if not response.ok:
            raise ProviderError(
                f"Cloudflare image-edit HTTP {response.status_code}: {error_detail(response)}"
            )
        data = response.json()
        if data.get("success") is False:
            raise ProviderError(str(data.get("errors") or "Cloudflare image edit failed"))
        result = data.get("result", data)
    else:
        result = cloudflare_request(
            CLOUDFLARE_IMAGE_MODEL,
            {"prompt": prompt[:1800], "steps": 4},
            deadline,
        )

    image_b64 = result.get("image")
    if not image_b64:
        raise ProviderError("Image provider returned no image")
    if isinstance(image_b64, str) and image_b64.startswith("data:"):
        image_b64 = image_b64.split(",", 1)[-1]
    try:
        raw = base64.b64decode(image_b64, validate=True)
    except Exception as exc:
        raise ProviderError("Image provider returned invalid image data") from exc
    if not raw:
        raise ProviderError("Image provider returned empty image")
    return raw

# =====================================================
# UI / TOOL HELPERS
# =====================================================
def send_long(chat_id, text, uid=None):
    chunks = [text[i:i + MAX_MESSAGE_LENGTH] for i in range(0, len(text), MAX_MESSAGE_LENGTH)] or [""]
    for index, chunk in enumerate(chunks):
        bot.send_message(chat_id, chunk, reply_markup=main_keyboard(uid) if index == len(chunks) - 1 else None)


def show_help(chat_id, uid):
    bot.send_message(chat_id,
        "🌈 راهنمای کوچولوی هشیوار 💙💚🤍\n\n"
        "💬 گفتگوی جدید: برای شروع یک مکالمه تازه؛ هر موضوعی داری همین‌جا بپرس.\n"
        "🧰 ابزارها: جعبه‌ابزار هشیوار؛ هر ابزار را انتخاب کن و راهنمای نمونه‌دارش را ببین.\n"
        "👤 حساب کاربری: دیدن اطلاعات و آمار فعالیتت.\n"
        "🎁 دعوت دوستان: لینک اختصاصی‌ات را بگیر و هشیوار را به دوستانت معرفی کن.\n"
        "⚙️ تنظیمات: مدیریت حافظه همین گفتگو.\n"
        "ℹ️ راهنما: همین راهنمای استفاده.\n\n"
        "✨ ابزارها چطور کار می‌کنند؟\n"
        "🌍 زبان‌یار: متن را بفرست و زبان مقصد را مشخص کن؛ مثلاً «این متن رو به انگلیسی ترجمه کن».\n"
        "📖 واژه‌کاو: یک واژه بفرست؛ مثلاً «معنی، مترادف و متضاد واژه دانایی رو بگو».\n"
        "📜 مثل‌دان: موضوع را بنویس؛ مثلاً «برای صبر و شکیبایی چند ضرب‌المثل بگو».\n"
        "🖼️ استودیوی تصویر: خیال‌نگار برای ساخت تصویر، دگرگون‌ساز برای ویرایش، پس‌زمینه‌چین برای تغییر محیط و شفاف‌ساز برای بهبود وضوح.\n"
        "🪄 قصه‌پرداز: موضوع یا شخصیت‌ها را بگو؛ مثلاً «یک قصه کوتاه درباره دوستی یک روباه و پرنده بنویس».\n"
        "✍️ متن‌آرا: متن را بفرست و تغییر دلخواهت را بگو؛ مثلاً «این پیام رو صمیمی‌تر و روان‌تر کن».\n\n"
        "💡 نکته: ابزار انتخاب‌شده فعال می‌ماند تا خودت ابزار دیگری انتخاب کنی یا گفتگوی جدید بزنی. بازکردن منو، حساب یا راهنما حالت ابزار را تغییر نمی‌دهد. 💚",
        reply_markup=main_keyboard(uid))


def show_about(chat_id, uid):
    bot.send_message(chat_id,
        "💙💚🤍 هشیوار؛ همراهی برای فکرهای بزرگ و سؤال‌های کوچک! ✨\n\n"
        "قرار است دقیق باشد، اما خشک نه؛ خردمند باشد، اما نصیحت‌گر نه؛ "
        "خوش‌صحبت باشد، اما پرحرف نه.", reply_markup=main_keyboard(uid))


def show_settings(chat_id):
    bot.send_message(chat_id, "⚙️ تنظیمات گفتگو\n\n• تاریخچه گفتگو در پایگاه داده نگه‌داری می‌شود.\n• حافظه بلندمدت فقط با درخواست روشن تو ثبت می‌شود.\n• برای دیدن حافظه بنویس «حافظه من».\n• برای ثبت مورد تازه بنویس «یادت باشه ...».\n• برای حذف موردی بنویس «فراموش کن ...».\n• ابزار انتخاب‌شده با نمایش منو عوض نمی‌شود.", reply_markup=settings_keyboard())


def show_account(chat_id, uid):
    with db_conn() as conn:
        row = conn.execute("SELECT first_name,username,first_seen FROM users WHERE user_id=?", (uid,)).fetchone()
        refs = conn.execute("SELECT COUNT(*) AS n FROM users WHERE referred_by=?", (uid,)).fetchone()["n"]
    name = row["first_name"] if row else "کاربر"
    bot.send_message(chat_id, f"👤 حساب کاربری\n\nنام: {name}\nشناسه: {uid}\nدعوت‌های موفق: {refs}\n\n🎁 لینک دعوتت همیشه از صفحه اصلی هم در دسترسه؛ از بخش زیر آمار فعالیتت رو ببین.", reply_markup=account_keyboard())


def show_stats(chat_id, uid):
    with db_conn() as conn:
        refs = conn.execute("SELECT COUNT(*) AS n FROM users WHERE referred_by=?", (uid,)).fetchone()["n"]
        if uid in ADMIN_IDS:
            total = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
            active = conn.execute("SELECT COUNT(*) AS n FROM users WHERE last_seen>=?", (time.time()-86400,)).fetchone()["n"]
            uses = conn.execute("SELECT COUNT(*) AS n FROM usage").fetchone()["n"]
            new = conn.execute("SELECT COUNT(*) AS n FROM users WHERE first_seen>=?", (time.time()-86400,)).fetchone()["n"]
            text = f"📊 آمار هشیوار\n\nکاربران کل: {total}\nکاربران فعال ۲۴ ساعت: {active}\nکاربران جدید ۲۴ ساعت: {new}\nاستفاده ثبت‌شده از ابزارها: {uses}\nدعوت‌های موفق: {conn.execute('SELECT COUNT(*) AS n FROM users WHERE referred_by IS NOT NULL').fetchone()['n']}"
        else:
            text = f"📊 دعوت‌های موفق شما: {refs}"
    bot.send_message(chat_id, text, reply_markup=main_keyboard(uid))


def show_invite(chat_id, uid):
    username = bot.get_me().username
    link = f"https://t.me/{username}?start=ref_{uid}"
    with db_conn() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM users WHERE referred_by=?", (uid,)).fetchone()["n"]
    bot.send_message(chat_id, f"🎁 لینک دعوت اختصاصی تو:\n{link}\n\nدعوت‌های موفق: {count}\n\nهر دعوت واقعی، یک دوست تازه رو با هشیوار آشنا می‌کنه! 💚🤍✨", reply_markup=main_keyboard(uid))


def set_tool(message, mode, title, description):
    key = user_key(message)
    set_mode(key, message.from_user.id, message.chat.id, mode)
    bot.send_message(message.chat.id, f"{title}\n\n{description}", reply_markup=main_keyboard(message.from_user.id))


def select_tool(chat_id, user_id, key, mode):
    set_mode(key, user_id, chat_id, mode)
    notices = {
        "image": ("🖼️ استودیوی تصویر هشیوار", "از منوی بازشده یکی از چهار بخش خیال‌نگار، دگرگون‌ساز، پس‌زمینه‌چین یا شفاف‌ساز رو انتخاب کن."),
        "translate": ("🌍 زبان‌یار؛ پلی بین زبان‌ها! 💚", "📝 متن رو بفرست و اگر زبان مقصد رو می‌دونی مشخص کن.\nمثلاً: «این جمله رو به انگلیسی ترجمه کن: از آشنایی با تو خوشحالم.»\n✨ اگر مقصد رو نگفتی، معمولاً به فارسی ترجمه می‌کنم."),
        "word": ("📖 واژه‌کاو؛ بریم سراغ راز واژه‌ها! 🤍", "🔎 کلمه یا عبارت رو بفرست تا معنی، کاربرد، مترادف، متضاد و مثال‌هاش رو بگم.\nمثلاً: «معنی واژه خردمند چیه؟ دو مترادف و یک مثال هم بزن.»"),
        "proverb": ("📜 مثل‌دان؛ برای هر حال‌وهوا، یک مثل! 🌱", "💭 موضوع یا موقعیت رو بنویس تا ضرب‌المثل‌های مرتبط رو همراه با معنی و کاربردشون پیدا کنم.\nمثلاً: «برای عجله کردن و پشیمانی چه ضرب‌المثلی داریم؟»"),
        "story": ("🪄 قصه‌پرداز؛ قصه از خیال تو شروع می‌شه! ✨", "📚 موضوع، شخصیت‌ها یا حال‌وهوای داستان رو بگو.\nمثلاً: «یک داستان کوتاه و شیرین درباره دوستی یک روباه و یک پرنده بنویس؛ پایانش امیدبخش باشه.»\n💡 می‌تونی ژانر، طول و گروه سنی مخاطب رو هم مشخص کنی."),
        "textcraft": ("✍️ متن‌آرا؛ متن تو، با حال‌وهوای دلخواهت! 💙", "📝 متن رو بفرست و بگو چه تغییری می‌خوای.\nمثلاً: «این پیام رو رسمی‌تر کن»، «صمیمی و دلنشینش کن»، «کوتاهش کن» یا «غلط‌های املاییش رو بگیر».\n🌈 معنی اصلی متن رو بدون اجازه‌ات عوض نمی‌کنم."),
    }
    title, desc = notices[mode]
    bot.send_message(chat_id, f"{title}\n\n{desc}\n\n💚🤍 هر وقت آماده‌ای، متن یا درخواستت رو بفرست. برای تغییر ابزار، خودت ابزار دیگه‌ای رو انتخاب کن. ⚡", reply_markup=main_keyboard(user_id))


def word_request(word):
    return ("در نقش فرهنگ‌نویس دقیق پاسخ بده. درباره این واژه توضیح بده: " + word +
            "\nمعنی‌های رایج و دقیق، کاربرد، مترادف، متضاد و دو مثال طبیعی ارائه کن. "
            "اگر چند معنی دارد جدا کن. ریشه‌شناسی را فقط در صورت اطمینان بگو و چیزی جعل نکن.")


def proverb_request(topic):
    return (f"برای موضوع «{topic}» ضرب‌المثل‌های واقعی و معتبر فارسی پیشنهاد کن. "
            "برای هر مورد معنی و کاربرد کوتاه بنویس. عبارت ابداعی را ضرب‌المثل جا نزن؛ "
            "اگر نمونه معتبر کم است، فقط همان تعداد را ارائه کن.")


def story_request(prompt):
    return ("در نقش داستان‌پردازی خلاق و خوش‌ذوق، بر اساس درخواست کاربر داستان بنویس: " + prompt +
            "\nداستان منسجم، خوش‌خوان و متناسب با مخاطب باشد. اگر جزئیاتی مشخص نشده، خلاقانه اما معقول انتخاب کن. "
            "از کلیشه و پند مستقیم غیرضروری دوری کن و پایان مناسبی بساز.")


def textcraft_request(prompt):
    return ("در نقش ویراستار و نویسنده فارسی دقیق عمل کن. درخواست کاربر درباره متن را انجام بده: " + prompt +
            "\nمعنای اصلی، اطلاعات، نام‌ها و منظور نویسنده را حفظ کن مگر کاربر صریحاً تغییر بخواهد. "
            "اگر فقط اصلاح نگارشی خواسته، محتوا را بازنویسی اساسی نکن. خروجی را مستقیم و آماده استفاده ارائه کن.")

def enhance_image_basic(image_bytes):
    """Non-AI enhancement: 2x Lanczos resize and restrained sharpening."""
    try:
        with Image.open(io.BytesIO(image_bytes)) as source:
            image = source.convert("RGB")
            width, height = image.size
            scale = min(2.0, 3000 / max(width, height))
            if scale > 1.0:
                image = image.resize((int(width * scale), int(height * scale)), Image.Resampling.LANCZOS)
            image = ImageEnhance.Contrast(image).enhance(1.03)
            image = ImageEnhance.Sharpness(image).enhance(1.35)
            image = image.filter(ImageFilter.UnsharpMask(radius=1.4, percent=115, threshold=3))
            out = io.BytesIO()
            image.save(out, format="JPEG", quality=94, optimize=True)
            return out.getvalue()
    except Exception as exc:
        raise ProviderError("Could not enhance the image") from exc


# =====================================================
# IMAGE TASK: preserve previous prompt and mode
# =====================================================
def handle_image(chat_id, uid, key, user_text, reference_image=None):
    mode = get_mode(key)
    if mode == "image_enhance":
        if not reference_image:
            bot.send_message(chat_id, "برای شفاف‌سازی، اول عکس رو بفرست.")
            return
        waiting = bot.send_message(chat_id, "✨ دارم وضوح عکس رو به روش پایه بهتر می‌کنم...")
        try:
            result = enhance_image_basic(reference_image)
            photo = io.BytesIO(result)
            photo.name = "hoshivar-enhanced.jpg"
            try: bot.delete_message(chat_id, waiting.message_id)
            except Exception: pass
            bot.send_photo(chat_id, photo, caption="✨ وضوح و شارپ‌بودن عکس بهبود داده شد. توجه: این نسخه بازسازی AI نیست.", reply_markup=main_keyboard(uid), timeout=40)
            log_usage(uid, "image_enhance")
        except Exception:
            logger.exception("Basic image enhancement failed")
            try: bot.delete_message(chat_id, waiting.message_id)
            except Exception: pass
            bot.send_message(chat_id, "بهبود عکس انجام نشد؛ لطفاً یک عکس معمولی با حجم کمتر بفرست.", reply_markup=main_keyboard(uid))
        finally:
            set_mode(key, uid, chat_id, "image_enhance")
        return

    is_generation = mode == "image_generate"
    is_background = mode == "image_background"
    if not CF_BASE_URL:
        bot.send_message(chat_id, "سرویس تصویرسازی تنظیم نشده؛ فعلاً امکان اجرای این ابزار نیست.", reply_markup=main_keyboard(uid))
        return
    if is_generation and reference_image:
        bot.send_message(chat_id, "برای خیال‌نگار، توضیح متنی بفرست. برای ویرایش عکس، بخش دگرگون‌ساز رو انتخاب کن.")
        return
    if not is_generation and not reference_image:
        bot.send_message(chat_id, "اول عکس رو بفرست تا بتونم ویرایشش کنم.")
        return

    previous = get_image_prompt(key)
    if reference_image:
        if is_background:
            combined = (
                "BACKGROUND REPLACEMENT ONLY. Use the supplied photograph as the source image. "
                "Keep every foreground person and foreground object: same exact number of people, "
                "same facial identity, face shape, expression, age, skin tone, hair, clothing, body "
                "proportions, pose and position. Do not remove, duplicate, replace or redesign them. "
                "Replace only the background with the precise scene requested by the user. "
                "Match perspective, shadows and natural lighting. User's background request: " + user_text
            )
        else:
            combined = (
                "EDIT THE PROVIDED PHOTO, DO NOT CREATE A DIFFERENT SCENE FROM SCRATCH. "
                "Treat the input as the source of truth. Preserve all people and objects not explicitly "
                "requested to change. Preserve the exact number of people and their facial identities, "
                "features, expressions, age, skin tone, hair, clothing, body proportions and poses. "
                "Apply only the requested changes. User's exact edit request: " + user_text
            )
    else:
        combined = user_text
        if previous:
            combined = ("Original image brief (keep unchanged requirements):\n" + previous +
                        "\nLatest user change:\n" + user_text +
                        "\nApply only the latest change and preserve all other requirements.")

    waiting_text = "🏞️ پس‌زمینه رو طبق خواسته‌ات تغییر می‌دم..." if is_background else ("🪄 عکس رو با حفظ سوژه‌های اصلی ویرایش می‌کنم..." if reference_image else "🎨 دارم تصویرت رو می‌سازم...")
    waiting = bot.send_message(chat_id, waiting_text)
    deadline = time.monotonic() + IMAGE_WAIT
    try:
        with ai_semaphore:
            english = translate_image_prompt(combined, deadline)
            image_bytes = generate_image(english, deadline, reference_image=reference_image)
        check_deadline(deadline)
        set_image_prompt(key, uid, chat_id, combined)
        photo = io.BytesIO(image_bytes)
        photo.name = "hoshivar-image.jpg"
        try: bot.delete_message(chat_id, waiting.message_id)
        except Exception: pass
        caption = "🏞️ پس‌زمینه تغییر کرد؛ اگر اصلاح دیگری می‌خواهی بگو." if is_background else ("🪄 عکس ویرایش شد؛ اگر تغییر دیگری می‌خواهی بگو." if reference_image else "🎨 تصویرت آماده شد!")
        bot.send_photo(chat_id, photo, caption=caption + " 💙", reply_markup=main_keyboard(uid), timeout=40)
        log_usage(uid, "image_background" if is_background else ("image_edit" if reference_image else "image_generate"))
    except Exception:
        logger.exception("Image generation/editing failed")
        try: bot.delete_message(chat_id, waiting.message_id)
        except Exception: pass
        bot.send_message(chat_id, "این بار تصویر آماده نشد. حالت فعلی رو نگه داشتم؛ کمی بعد دوباره تلاش کن.", reply_markup=main_keyboard(uid))
    finally:
        set_mode(key, uid, chat_id, mode)


def download_telegram_photo(message):
    if not message.photo:
        raise ValueError("Message has no photo")
    photo = message.photo[-1]
    file_info = bot.get_file(photo.file_id)
    image_bytes = bot.download_file(file_info.file_path)
    if not image_bytes:
        raise ProviderError("Telegram returned an empty photo")
    return image_bytes


@bot.message_handler(content_types=["photo"])
def handle_photo(message):
    chat_id, uid = message.chat.id, message.from_user.id
    key = user_key(message)
    register_user(message.from_user)
    if touch_user(message.from_user):
        bot.send_message(chat_id, "دسترسی این حساب به ربات محدود شده است.")
        return
    if is_maintenance() and uid not in ADMIN_IDS:
        bot.send_message(chat_id, "هشیوار فعلاً در حال رسیدگی و مرتب‌کردن اوضاعه. کمی بعد برگرد.")
        return
    if get_mode(key) not in ("image_edit", "image_background", "image_enhance"):
        bot.send_message(chat_id, "برای ویرایش عکس، اول دگرگون‌ساز، پس‌زمینه‌چین یا شفاف‌ساز رو از استودیوی تصویر انتخاب کن. 💙", reply_markup=main_keyboard(uid))
        return
    try:
        reference = download_telegram_photo(message)
        caption = (message.caption or "").strip()
        if caption:
            handle_image(chat_id, uid, key, caption, reference_image=reference)
        else:
            with pending_image_refs_lock:
                pending_image_refs[key] = reference
            bot.send_message(chat_id, "عکست رسید. حالا بنویس چه تغییری می‌خوای؛ مثلاً «همین دو نفر رو با حفظ چهره داخل مسجد نشان بده». 💙")
    except Exception:
        logger.exception("Photo receive failed")
        bot.send_message(chat_id, "نتونستم عکس رو دریافت کنم. لطفاً یک بار دیگه به‌صورت عکس معمولی بفرست.", reply_markup=main_keyboard(uid))

# =====================================================
# ADMIN PANEL (restricted to ADMIN_IDS)
# =====================================================
def admin_only(uid):
    return uid in ADMIN_IDS


def show_admin(chat_id, uid):
    if not admin_only(uid):
        return
    bot.send_message(chat_id, "🛠 پنل مدیریت هشیوار\n\nابزارهای نظارت و کنترل پایه در دسترس‌اند.", reply_markup=admin_keyboard())


def admin_stats(chat_id, uid):
    if not admin_only(uid):
        return
    with db_conn() as conn:
        total = conn.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        blocked = conn.execute("SELECT COUNT(*) n FROM users WHERE is_blocked=1").fetchone()["n"]
        daily = conn.execute("SELECT COUNT(*) n FROM users WHERE last_seen>=?", (time.time()-86400,)).fetchone()["n"]
        by_tool = conn.execute("SELECT tool,COUNT(*) n FROM usage WHERE created_at>=? GROUP BY tool ORDER BY n DESC", (time.time()-86400,)).fetchall()
    details = "\n".join(f"• {r['tool']}: {r['n']}" for r in by_tool) or "هنوز آماری ثبت نشده."
    bot.send_message(chat_id, f"📊 گزارش مدیریتی\n\nکل کاربران: {total}\nفعال ۲۴ ساعت: {daily}\nمسدود: {blocked}\n\nاستفاده ابزارها در ۲۴ ساعت:\n{details}", reply_markup=admin_keyboard())


def admin_health(chat_id, uid):
    if not admin_only(uid):
        return
    cf = "فعال" if CF_BASE_URL else "تنظیم نشده"
    bot.send_message(chat_id, f"🩺 وضعیت پیکربندی\n\nربات: فعال\n1xAI: تنظیم شده\nCloudflare: {cf}\nپایگاه داده: {DB_PATH}\nمدل متنی: {X1_MODEL}", reply_markup=admin_keyboard())


def toggle_maintenance(chat_id, uid):
    if not admin_only(uid):
        return
    new_state = not is_maintenance()
    set_maintenance(new_state)
    status = "روشن شد؛ فقط مدیران می‌توانند از ربات استفاده کنند." if new_state else "خاموش شد؛ ربات دوباره برای همه در دسترس است."
    bot.send_message(chat_id, "🔧 حالت تعمیرات " + status, reply_markup=admin_keyboard())

# =====================================================
# COMMANDS
# =====================================================
@bot.message_handler(commands=["start"])
def start(message):
    referral = None
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) > 1 and parts[1].startswith("ref_"):
        try:
            referral = int(parts[1][4:])
        except ValueError:
            pass
    register_user(message.from_user, referral)
    send_home(message.chat.id, message.from_user.id)


@bot.message_handler(commands=["help"])
def help_command(message):
    register_user(message.from_user)
    show_help(message.chat.id, message.from_user.id)


@bot.message_handler(commands=["new"])
def new_command(message):
    key = user_key(message)
    clear_history(key)
    clear_task(key, message.from_user.id, message.chat.id)
    bot.send_message(message.chat.id, "یک صفحه تازه؛ ذهن رو از نو مرتب می‌کنیم. 🌱", reply_markup=main_keyboard(message.from_user.id))


@bot.message_handler(commands=["reset"])
def reset_command(message):
    key = user_key(message)
    clear_history(key)
    clear_task(key, message.from_user.id, message.chat.id)
    bot.send_message(message.chat.id, "حافظه همین گفت‌وگو و کار فعال پاک شد.", reply_markup=main_keyboard(message.from_user.id))


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
        if not (call.data or "").startswith("tools_coming_"):
            bot.answer_callback_query(call.id)
    except Exception:
        pass
    try:
        chat_id, uid, action = call.message.chat.id, call.from_user.id, call.data
        key = user_key(call)
        register_user(call.from_user)
        if action == "new_chat":
            clear_history(key)
            clear_task(key, uid, chat_id)
            bot.send_message(chat_id, "خب، یک شروع تازه! هر فکری داری از همین‌جا بازش می‌کنیم. 🌱", reply_markup=main_keyboard(uid))
        elif action == "home":
            # Showing the menu does not change the active tool.
            bot.send_message(chat_id, "منو اینجاست؛ ابزار فعلی‌ات هم سر جاشه. 💙", reply_markup=main_keyboard(uid))
            bot.send_message(chat_id, "ابزار دیگری خواستی انتخاب کن:", reply_markup=inline_keyboard(uid))
        elif action == "help": show_help(chat_id, uid)
        elif action == "about": show_about(chat_id, uid)
        elif action == "settings": show_settings(chat_id)
        elif action == "account": show_account(chat_id, uid)
        elif action == "tools":
            bot.send_message(chat_id, "🧰 جعبه‌ابزار هشیوار 💙💚🤍\n\nهر کدوم از این ابزارها برای کاری ساخته شده؛ انتخابش کن تا راهنمای کوتاه و مثال‌هاش رو ببینی. ✨", reply_markup=tools_keyboard())
        elif action == "image":
            show_image_studio(chat_id, uid)
        elif action in ("img_generate", "img_edit", "img_background", "img_enhance"):
            image_modes = {"img_generate": "image_generate", "img_edit": "image_edit", "img_background": "image_background", "img_enhance": "image_enhance"}
            select_image_mode(chat_id, uid, key, image_modes[action])
        elif action in ("translate", "word", "proverb", "story", "textcraft"):
            select_tool(chat_id, uid, key, action)
        elif action == "invite": show_invite(chat_id, uid)
        elif action == "stats": show_stats(chat_id, uid)
        elif action == "reset_memory":
            clear_history(key)
            bot.send_message(chat_id, "تاریخچه همین گفت‌وگو پاک شد؛ ابزار انتخاب‌شده را نگه داشتم. 💙", reply_markup=main_keyboard(uid))
        elif action == "admin": show_admin(chat_id, uid)
        elif action == "admin_stats": admin_stats(chat_id, uid)
        elif action == "admin_health": admin_health(chat_id, uid)
        elif action == "admin_maintenance": toggle_maintenance(chat_id, uid)
    except Exception:
        logger.exception("Callback failed")

# =====================================================
# TEXT HANDLER
# =====================================================
@bot.message_handler(content_types=["text"])
def handle_text(message):
    chat_id, uid = message.chat.id, message.from_user.id
    key = user_key(message)
    text = (message.text or "").strip()
    if not text:
        return
    register_user(message.from_user)
    if touch_user(message.from_user):
        bot.send_message(chat_id, "دسترسی این حساب به ربات محدود شده است.")
        return
    if is_maintenance() and uid not in ADMIN_IDS:
        bot.send_message(chat_id, "هشیوار فعلاً در حال رسیدگی و مرتب‌کردن اوضاعه. کمی بعد برگرد؛ زود می‌بینیمت. 🌱")
        return

    # Explicit menu actions only. The menu-display action preserves current mode.
    if text == BTN_MENU:
        bot.send_message(chat_id, "منو حاضر شد؛ کار قبلی‌ات همچنان فعاله. 💙", reply_markup=main_keyboard(uid))
        bot.send_message(chat_id, "برای انتخاب بخش موردنظرت:", reply_markup=inline_keyboard(uid))
        return
    if text == BTN_TOOLS:
        bot.send_message(chat_id, "🧰 جعبه‌ابزار هشیوار 💙💚🤍\n\nابزاری رو انتخاب کن تا راهنمای استفاده و نمونه درخواستش رو ببینی. ✨", reply_markup=tools_keyboard())
        return
    if text == BTN_CHAT:
        clear_history(key)
        clear_task(key, uid, chat_id)
        bot.send_message(chat_id, "خب، بریم سراغ یک شروع تازه! این بار از کجا شروع کنیم؟ 🌱", reply_markup=main_keyboard(uid))
        return
    tool_shortcuts = {
        BTN_TRANSLATE: "translate", BTN_WORD: "word",
        BTN_PROVERB: "proverb", BTN_STORY: "story", BTN_TEXT: "textcraft",
    }
    if text == BTN_IMAGE:
        show_image_studio(chat_id, uid); return
    if text in tool_shortcuts:
        select_tool(chat_id, uid, key, tool_shortcuts[text]); return
    if text == BTN_ACCOUNT:
        show_account(chat_id, uid); return
    if text == BTN_STATS:
        show_stats(chat_id, uid); return
    if text == BTN_INVITE:
        show_invite(chat_id, uid); return
    if text == BTN_HELP:
        show_help(chat_id, uid); return
    if text == BTN_SETTINGS:
        show_settings(chat_id); return

    # Long-term profile memory is opt-in: save only when the user explicitly asks.
    remember_prefixes = ("یادت باشه", "به خاطر بسپار", "این رو به خاطر بسپار", "این را به خاطر بسپار")
    matched_prefix = next((prefix for prefix in remember_prefixes if text.startswith(prefix)), None)
    if matched_prefix:
        item = text[len(matched_prefix):].lstrip(" :،؛")
        if item:
            add_profile_memory(uid, item)
            bot.send_message(chat_id, "حتماً؛ این نکته رو برای گفتگوهای بعدی نگه می‌دارم. هر وقت خواستی می‌تونی ببینی یا پاکش کنی. 💙", reply_markup=main_keyboard(uid))
        else:
            bot.send_message(chat_id, "حتماً؛ فقط بگو دقیقاً چه چیزی رو به خاطر بسپارم؟", reply_markup=main_keyboard(uid))
        return
    if text.startswith("فراموش کن"):
        phrase = text[len("فراموش کن"):].strip(" :،؛ ")
        if phrase:
            delete_profile_memory(uid, phrase)
            bot.send_message(chat_id, "اگر موردی با این عبارت ثبت شده بود، از حافظه بلندمدتت حذف شد. 🌱", reply_markup=main_keyboard(uid))
        else:
            bot.send_message(chat_id, "برای حذف یک مورد، بعد از «فراموش کن» بخشی از همان مورد رو بنویس.", reply_markup=main_keyboard(uid))
        return
    if text in ("همه حافظه‌ام رو پاک کن", "تمام حافظه‌ام را پاک کن", "پاک کن همه حافظه‌ام"):
        delete_profile_memory(uid)
        clear_history(key)
        bot.send_message(chat_id, "حافظه بلندمدت و تاریخچه همین گفتگو پاک شد. 💙", reply_markup=main_keyboard(uid))
        return
    if text in ("حافظه من", "حافظه‌ام", "حافظه من رو نشون بده", "/memory"):
        show_profile_memory(chat_id, uid)
        return

    mode = get_mode(key)
    if mode in ("image_generate", "image_edit", "image_background", "image_enhance"):
        with pending_image_refs_lock:
            reference = pending_image_refs.pop(key, None)
        handle_image(chat_id, uid, key, text, reference_image=reference)
        return

    waiting = None
    try:
        if mode == "translate":
            waiting = bot.send_message(chat_id, "🌐 دارم ترجمه‌اش می‌کنم؛ حواسم به لحن و معنی هم هست...")
            log_usage(uid, "translate")
            with ai_semaphore:
                answer = translate_text(text, time.monotonic() + MAX_WAIT)
            output = "🌐 ترجمه:\n\n" + answer
        elif mode == "word":
            waiting = bot.send_message(chat_id, "📚 بذار معنی دقیقش رو از چند زاویه بررسی کنم...")
            log_usage(uid, "dictionary")
            with lock_for(key), ai_semaphore:
                answer = ask_ai(word_request(text), key, time.monotonic() + MAX_WAIT, uid)
            output = answer
        elif mode == "proverb":
            waiting = bot.send_message(chat_id, "📜 دنبال نمونه‌های درست و مرتبط می‌گردم... 💚")
            log_usage(uid, "proverb")
            with lock_for(key), ai_semaphore:
                answer = ask_ai(proverb_request(text), key, time.monotonic() + MAX_WAIT, uid)
            output = answer
        elif mode == "story":
            waiting = bot.send_message(chat_id, "🪄 دارم نخ قصه‌ات رو پیدا می‌کنم... ✨")
            log_usage(uid, "story")
            with lock_for(key), ai_semaphore:
                answer = ask_ai(story_request(text), key, time.monotonic() + MAX_WAIT, uid)
            output = answer
        elif mode == "textcraft":
            waiting = bot.send_message(chat_id, "✍️ دارم متن رو با سلیقه‌ای که گفتی مرتب می‌کنم... 💙")
            log_usage(uid, "textcraft")
            with lock_for(key), ai_semaphore:
                answer = ask_ai(textcraft_request(text), key, time.monotonic() + MAX_WAIT, uid)
            output = answer
        else:
            waiting = bot.send_message(chat_id, "🧠 بذار یک لحظه روش فکر کنم...")
            log_usage(uid, "chat")
            with lock_for(key), ai_semaphore:
                answer = ask_ai(text, key, time.monotonic() + MAX_WAIT, uid)
            output = answer
        if waiting:
            try: bot.delete_message(chat_id, waiting.message_id)
            except Exception: pass
        send_long(chat_id, output, uid)
        # Intentionally do not clear mode after success. It remains active until
        # the user explicitly selects another tool or starts a new chat.
    except Exception:
        logger.exception("Message processing failed")
        if waiting:
            try: bot.delete_message(chat_id, waiting.message_id)
            except Exception: pass
        bot.send_message(chat_id, "این بار پاسخ کامل نشد؛ گاهی مسیر ارتباطی کمی ناز می‌کنه. دوباره امتحان کن، ابزار انتخاب‌شده‌ات همچنان فعاله. 💙", reply_markup=main_keyboard(uid))

# =====================================================
# WEBHOOK SERVER
# =====================================================
class WebhookHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        if self.path in ("/", "/health"):
            self.send_response(200); self.send_header("Content-Length", "0"); self.end_headers()
        else:
            self.send_error(404)

    def do_GET(self):
        if self.path in ("/", "/health"):
            body = b"Hoshivar AI Bot is running"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path != WEBHOOK_PATH:
            self.send_error(404); return
        if self.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
            self.send_error(403); return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1_000_000:
                self.send_error(400); return
            raw = self.rfile.read(length)
            update = telebot.types.Update.de_json(raw.decode("utf-8"))
            bot.process_new_updates([update])
            self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
        except Exception:
            logger.exception("Webhook update failed")
            self.send_error(500)

    def log_message(self, fmt, *args):
        logger.info("WEBHOOK: " + fmt, *args)

# =====================================================
# START
# =====================================================
if __name__ == "__main__":
    init_db()
    logger.info("Starting Hoshivar | primary=1xAI/%s | cloudflare=%s", X1_MODEL, bool(CF_BASE_URL))
    logger.info("SQLite database: %s", DB_PATH)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), WebhookHandler)
    server.daemon_threads = True
    try:
        ok = bot.set_webhook(url=WEBHOOK_URL, secret_token=WEBHOOK_SECRET, allowed_updates=["message", "callback_query"])
        if not ok:
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
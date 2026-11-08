
import os
import requests
import telebot

# تنظیمات ربات
BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing")

if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY is missing")

bot = telebot.TeleBot(BOT_TOKEN)

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/"
    "models/gemini-2.5-flash:generateContent"
)

SYSTEM_PROMPT = """
تو هشیوار هستی؛ یک دستیار هوش مصنوعی فارسی‌زبان،
باهوش، مودب، صمیمی و دقیق.
به زبان کاربر پاسخ بده.
پاسخ‌هایت روشن، مفید و قابل فهم باشند.
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
        "contents": [
            {
                "role": "user",
                "parts": [{"text": message}]
            }
        ],
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
        "من هشیوار هستم؛ دستیار هوش مصنوعی شما. 🤖\n\n"
        "هر سؤالی داری بپرس، من اینجام تا کمکت کنم."
    )


@bot.message_handler(commands=["help"])
def help_command(message):
    bot.reply_to(
        message,
        "🤖 راهنمای هشیوار\n\n"
        "سؤالت رو به صورت پیام برام بفرست.\n"
        "من با هوش مصنوعی بهت پاسخ می‌دم."
    )


@bot.message_handler(func=lambda message: bool(message.text))
def chat(message):
    waiting = bot.reply_to(message, "⏳ هشیوار در حال فکر کردنه...")

    try:
        answer = ask_ai(message.text)

        if len(answer) > 4000:
            for i in range(0, len(answer), 4000):
                bot.send_message(message.chat.id, answer[i:i+4000])
        else:
            bot.send_message(message.chat.id, answer)

    except requests.exceptions.Timeout:
        bot.send_message(message.chat.id, "⏳ پاسخ کمی طول کشید. دوباره امتحان کن.")

    except requests.exceptions.RequestException:
        bot.send_message(message.chat.id, "⚠️ ارتباط با هوش مصنوعی برقرار نشد.")

    except Exception as error:
        print("ERROR:", error)
        bot.send_message(message.chat.id, "❌ مشکلی پیش اومد. لطفاً دوباره تلاش کن.")

    finally:
        try:
            bot.delete_message(message.chat.id, waiting.message_id)
        except Exception:
            pass


print("Hoshiwar AI Bot is running...")

bot.infinity_polling(skip_pending=True, timeout=30)

import os
import requests
from bs4 import BeautifulSoup
import json
import urllib.parse
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask, request

# =========================================================
# CONFIG
# =========================================================

TOKEN = os.environ.get("BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN غير موجود في Environment Variables")

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

RENDER_URL = os.environ.get(
    "RENDER_URL",
    "https://osint-bot-t0vn.onrender.com"
)

WEBHOOK_URL = f"{RENDER_URL.rstrip('/')}/{TOKEN}"

USER_CACHE = {}


# =========================================================
# FLASK / WEBHOOK
# =========================================================

@app.route("/")
def home():
    return "🤖 OSINT Bot is active!"


@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():

    if request.headers.get("content-type", "").startswith("application/json"):

        json_string = request.get_data().decode("utf-8")

        update = telebot.types.Update.de_json(json_string)

        bot.process_new_updates([update])

        return "", 200

    return "", 403


# =========================================================
# USERNAME NORMALIZATION
# =========================================================

def normalize_username(text):
    """
    يحافظ على الرموز الموجودة في اليوزر.
    
    أمثلة:
    @Lann_100  -> Lann_100
    @user-name -> user-name
    user.name  -> user.name
    test$user  -> test$user
    """

    username = text.strip()

    # إزالة @ من البداية فقط
    if username.startswith("@"):
        username = username[1:]

    # إزالة المسافات من البداية والنهاية فقط
    username = username.strip()

    return username


def analyze_username_strength(username):

    # نحسب طول الاسم كما هو
    length = len(username)

    if length == 3:
        return "ثلاثي (مميز جداً ونادر 🔥)"

    elif length == 4:
        return "رباعي (مميز وقيم 💎)"

    elif length <= 6:
        return "قصير ومميز ✨"

    else:
        return "عادي طويل 📌"


def encoded_username(username):
    """
    تشفير الجزء الخاص باليوزر داخل الرابط
    حتى لا تكسر الرموز الخاصة URL.
    """

    return urllib.parse.quote(username, safe="")


# =========================================================
# GITHUB
# =========================================================

def check_github(username):

    # GitHub لديه قواعد خاصة بأسماء المستخدمين.
    # لا نحاول تحويل الاسم أو حذف الرموز منه.
    # إذا كان الاسم غير صالح في GitHub سيعيد API غالباً 404.

    url = f"https://api.github.com/users/{encoded_username(username)}"

    headers = {
        "User-Agent": "OSINT-Telegram-Bot",
        "Accept": "application/vnd.github+json"
    }

    try:

        res = requests.get(
            url,
            headers=headers,
            timeout=8
        )

        if res.status_code == 200:

            data = res.json()

            if (
                "id" in data
                and data.get("login", "").lower() == username.lower()
            ):

                details = [
                    "✅ **غيت هاب (GitHub)**",
                    f"🔗 الرابط: https://github.com/{username}",
                    f"📊 تقييم اليوزر: {analyze_username_strength(username)}",
                    f"🆔 المعرف الرقمي: {data.get('id', 'غير متوفر')}",
                    f"👤 الاسم الكامل: {data.get('name') or 'غير محدد'}",
                    f"📦 المستودعات: {data.get('public_repos', 0)}",
                    f"👥 المتابعين: {data.get('followers', 0)}"
                ]

                return "\n".join(details)

    except requests.RequestException:
        pass

    except Exception:
        pass

    return None


# =========================================================
# TIKTOK
# =========================================================

def check_tiktok(username):

    url = f"https://www.tiktok.com/@{encoded_username(username)}"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "Chrome/122.0.0.0 Safari/537.36"
        )
    }

    try:

        res = requests.get(
            url,
            headers=headers,
            timeout=10
        )

        if res.status_code != 200:
            return None

        soup = BeautifulSoup(res.text, "html.parser")

        sig_data = soup.find(
            "script",
            id="SIGI_STATE"
        )

        if not sig_data:
            return None

        try:

            data = json.loads(
                sig_data.string or "{}"
            )

            users = data.get(
                "UserModule",
                {}
            ).get(
                "users",
                {}
            )

            for uid, info in users.items():

                if info.get(
                    "uniqueId",
                    ""
                ).lower() == username.lower():

                    details = [
                        "✅ **تيك توك (TikTok)**",
                        f"🔗 الرابط: {url}",
                        f"📊 تقييم اليوزر: {analyze_username_strength(username)}",
                        f"👤 الاسم: {info.get('nickname') or 'غير محدد'}",
                        f"📝 البايو: {info.get('signature') or 'لا يوجد'}"
                    ]

                    stats = data.get(
                        "UserModule",
                        {}
                    ).get(
                        "stats",
                        {}
                    ).get(
                        uid,
                        {}
                    )

                    if stats:
                        details.append(
                            f"👥 المتابعين: "
                            f"{stats.get('followerCount', 'مخفي')}"
                        )

                    return "\n".join(details)

        except Exception:
            pass

    except requests.RequestException:
        pass

    except Exception:
        pass

    return None


# =========================================================
# SNAPCHAT
# =========================================================

def check_snapchat(username):

    url = (
        f"https://www.snapchat.com/add/"
        f"{encoded_username(username)}"
    )

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    try:

        res = requests.get(
            url,
            headers=headers,
            timeout=8
        )

        if res.status_code != 200:
            return None

        soup = BeautifulSoup(
            res.text,
            "html.parser"
        )

        text_content = soup.get_text().lower()

        not_found_keywords = [
            "sorry",
            "not found",
            "تعذر العثور",
            "هذا الحساب غير موجود",
            "doesn't exist"
        ]

        if any(
            keyword in text_content
            for keyword in not_found_keywords
        ):
            return None

        details = [
            "✅ **سناب شات (Snapchat)**",
            f"🔗 الرابط: {url}",
            f"📊 تقييم اليوزر: "
            f"{analyze_username_strength(username)}",
            "👻 يمكنك فتح الرابط أعلاه مباشرة للتحقق من الحساب."
        ]

        return "\n".join(details)

    except requests.RequestException:
        pass

    except Exception:
        pass

    return None


# =========================================================
# INSTAGRAM
# =========================================================

def check_instagram(username):

    url = (
        f"https://www.instagram.com/"
        f"{encoded_username(username)}/"
    )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "Chrome/122.0.0.0 Safari/537.36"
        )
    }

    try:

        res = requests.get(
            url,
            headers=headers,
            timeout=8
        )

        if res.status_code != 200:
            return None

        soup = BeautifulSoup(
            res.text,
            "html.parser"
        )

        title = soup.find("title")

        title_text = (
            title.text.lower()
            if title
            else ""
        )

        if (
            "login" in title_text
            or title_text == "instagram"
            or not title_text
        ):
            return None

        details = [
            "✅ **إنستغرام (Instagram)**",
            f"🔗 الرابط: {url}",
            f"📊 تقييم اليوزر: "
            f"{analyze_username_strength(username)}"
        ]

        return "\n".join(details)

    except requests.RequestException:
        pass

    except Exception:
        pass

    return None


# =========================================================
# REDDIT
# =========================================================

def check_reddit(username):

    url = (
        f"https://www.reddit.com/user/"
        f"{encoded_username(username)}/about.json"
    )

    headers = {
        "User-Agent": "OSINT-Telegram-Bot/1.0"
    }

    try:

        res = requests.get(
            url,
            headers=headers,
            timeout=8
        )

        if res.status_code != 200:
            return None

        data = res.json().get(
            "data",
            {}
        )

        if (
            data
            and "id" in data
            and data.get(
                "name",
                ""
            ).lower() == username.lower()
        ):

            details = [
                "✅ **ريديت (Reddit)**",
                f"🔗 الرابط: https://www.reddit.com/user/{username}",
                f"📊 تقييم اليوزر: "
                f"{analyze_username_strength(username)}",
                f"🔥 الكارما الإجمالية: "
                f"{data.get('total_karma', 0)}"
            ]

            return "\n".join(details)

    except requests.RequestException:
        pass

    except Exception:
        pass

    return None


# =========================================================
# GENERAL PLATFORM
# =========================================================

def check_general_platform(
    name,
    url_template,
    username
):

    url = url_template.format(
        encoded_username(username)
    )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "Chrome/122.0.0.0 Safari/537.36"
        )
    }

    try:

        res = requests.get(
            url,
            headers=headers,
            timeout=8
        )

        if res.status_code != 200:
            return None

        soup = BeautifulSoup(
            res.text,
            "html.parser"
        )

        text_content = soup.get_text().lower()

        not_found_keywords = [
            "page not found",
            "user not found",
            "عذراً",
            "هذا الحساب غير موجود",
            "does not exist",
            "404"
        ]

        if any(
            keyword in text_content
            for keyword in not_found_keywords
        ):
            return None

        details = [
            f"✅ **{name}**",
            f"🔗 الرابط: {url}",
            f"📊 تقييم اليوزر: "
            f"{analyze_username_strength(username)}"
        ]

        return "\n".join(details)

    except requests.RequestException:
        pass

    except Exception:
        pass

    return None


# =========================================================
# START
# =========================================================

@bot.message_handler(commands=["start"])
def send_welcome(message):

    welcome_text = (
        "👋 **مرحباً بك في أداة البصمة الرقمية!**\n\n"
        "🔍 أرسل اليوزر مباشرة.\n\n"
        "يدعم مثلاً:\n"
        "`Lann_100`\n"
        "`user-name`\n"
        "`user.name`\n"
        "`@username`"
    )

    bot.send_message(
        message.chat.id,
        welcome_text,
        parse_mode="Markdown"
    )


# =========================================================
# SEARCH
# =========================================================

@bot.message_handler(
    func=lambda message: True
)
def search_username(message):

    username = normalize_username(
        message.text or ""
    )

    if not username:
        return

    msg = bot.reply_to(
        message,
        f"🔍 جاري الفحص الشامل لـ (@{username})...\n"
        "يرجى الانتظار."
    )

    platforms = {

        "snapchat": lambda:
            check_snapchat(username),

        "tiktok": lambda:
            check_tiktok(username),

        "instagram": lambda:
            check_instagram(username),

        "reddit": lambda:
            check_reddit(username),

        "twitter": lambda:
            check_general_platform(
                "تويتر / إكس (Twitter)",
                "https://twitter.com/{}",
                username
            ),

        "pinterest": lambda:
            check_general_platform(
                "بنترست (Pinterest)",
                "https://www.pinterest.com/{}/",
                username
            ),

        "twitch": lambda:
            check_general_platform(
                "تويتش (Twitch)",
                "https://www.twitch.tv/{}",
                username
            ),

        # لا نحذف GitHub من القائمة بسبب "_".
        # GitHub نفسه سيحدد إذا كان الاسم صالحاً أم لا.
        "github": lambda:
            check_github(username)
    }

    found_results = {}

    for key, func in platforms.items():

        try:

            result = func()

            if result:
                found_results[key] = result

        except Exception:
            continue

    try:
        bot.delete_message(
            message.chat.id,
            msg.message_id
        )
    except Exception:
        pass

    if not found_results:

        bot.send_message(
            message.chat.id,
            f"❌ **لم يتم العثور على حسابات مطابقة لـ "
            f"(@{username}).**",
            parse_mode="Markdown"
        )

        return

    USER_CACHE[message.chat.id] = found_results

    markup = InlineKeyboardMarkup()

    markup.row_width = 2

    platform_names = {

        "snapchat": "👻 سناب شات",

        "tiktok": "🎵 تيك توك",

        "instagram": "📸 إنستغرام",

        "github": "🐙 غيت هاب",

        "reddit": "🤖 ريديت",

        "twitter": "🐦 تويتر / إكس",

        "pinterest": "📌 بنترست",

        "twitch": "💜 تويتش"
    }

    buttons = []

    for key in found_results.keys():

        buttons.append(
            InlineKeyboardButton(
                platform_names.get(key, key),
                callback_data=f"show_{key}"
            )
        )

    markup.add(*buttons)

    bot.send_message(
        message.chat.id,
        f"🎯 **تم العثور على الحساب (@{username}) "
        f"في المنصات التالية:**",
        reply_markup=markup,
        parse_mode="Markdown"
    )


# =========================================================
# CALLBACK
# =========================================================

@bot.callback_query_handler(
    func=lambda call:
        call.data.startswith("show_")
)
def handle_platform_callback(call):

    chat_id = call.message.chat.id

    platform_key = call.data.replace(
        "show_",
        "",
        1
    )

    user_data = USER_CACHE.get(
        chat_id
    )

    if (
        user_data
        and platform_key in user_data
    ):

        bot.answer_callback_query(
            call.id,
            "✅ يتم تحميل التفاصيل..."
        )

        bot.send_message(
            chat_id,
            user_data[platform_key],
            parse_mode="Markdown"
        )

    else:

        bot.answer_callback_query(
            call.id,
            "⚠️ انتهت صلاحية الجلسة، "
            "يرجى إعادة إرسال اليوزر.",
            show_alert=True
        )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    bot.remove_webhook()

    bot.set_webhook(
        url=WEBHOOK_URL
    )

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )

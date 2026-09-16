import os
import requests
from bs4 import BeautifulSoup
import json
import urllib.parse
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask, request


# =========================================================
# إعدادات البوت
# =========================================================

# حط توكن البوت الجديد هنا
TOKEN = "8974546244:AAGSIwbh9FmENOiKYP2tS33_Z-ixjPl0cl4"

bot = telebot.TeleBot(TOKEN)

app = Flask(__name__)

# رابط Render الخاص بك
RENDER_URL = "https://osint-bot-t0vn.onrender.com"

WEBHOOK_URL = f"{RENDER_URL.rstrip('/')}/{TOKEN}"

# تخزين نتائج البحث مؤقتًا
USER_CACHE = {}


# =========================================================
# الصفحة الرئيسية
# =========================================================

@app.route("/")
def home():
    return "🤖 OSINT Bot is active!"


# =========================================================
# Telegram Webhook
# =========================================================

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():

    content_type = request.headers.get("content-type", "")

    if content_type.startswith("application/json"):

        json_string = request.get_data().decode("utf-8")

        update = telebot.types.Update.de_json(json_string)

        bot.process_new_updates([update])

        return "", 200

    return "", 403


# =========================================================
# تنظيف اليوزر
# =========================================================

def normalize_username(text):
    """
    يحافظ على اليوزر كما كتبه المستخدم.

    أمثلة:
    @Lann_100  -> Lann_100
    Lann_100   -> Lann_100
    user-name  -> user-name
    user.name  -> user.name
    """

    username = (text or "").strip()

    # إزالة @ من البداية فقط
    if username.startswith("@"):
        username = username[1:]

    return username.strip()


# =========================================================
# تشفير اليوزر داخل الرابط
# =========================================================

def encode_username(username):
    """
    يمنع الرموز الخاصة من كسر الرابط.
    """

    return urllib.parse.quote(
        username,
        safe=""
    )


# =========================================================
# تقييم طول اليوزر
# =========================================================

def analyze_username_strength(username):

    length = len(username)

    if length == 3:
        return "ثلاثي (مميز جداً ونادر 🔥)"

    elif length == 4:
        return "رباعي (مميز وقيم 💎)"

    elif length <= 6:
        return "قصير ومميز ✨"

    else:
        return "عادي طويل 📌"


# =========================================================
# GitHub
# =========================================================

def check_github(username):

    encoded = encode_username(username)

    url = f"https://api.github.com/users/{encoded}"

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

        if res.status_code != 200:
            return None

        data = res.json()

        login = data.get("login", "")

        if (
            data.get("id")
            and login.lower() == username.lower()
        ):

            details = [
                "✅ GitHub",
                f"🔗 الرابط: https://github.com/{username}",
                f"📊 تقييم اليوزر: {analyze_username_strength(username)}",
                f"🆔 المعرف الرقمي: {data.get('id', 'غير متوفر')}",
                f"👤 الاسم: {data.get('name') or 'غير محدد'}",
                f"📦 المستودعات: {data.get('public_repos', 0)}",
                f"👥 المتابعين: {data.get('followers', 0)}"
            ]

            return "\n".join(details)

    except Exception:
        pass

    return None


# =========================================================
# TikTok
# =========================================================

def check_tiktok(username):

    encoded = encode_username(username)

    url = f"https://www.tiktok.com/@{encoded}"

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

        soup = BeautifulSoup(
            res.text,
            "html.parser"
        )

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

            user_module = data.get(
                "UserModule",
                {}
            )

            users = user_module.get(
                "users",
                {}
            )

            stats_all = user_module.get(
                "stats",
                {}
            )

            for uid, info in users.items():

                unique_id = info.get(
                    "uniqueId",
                    ""
                )

                if unique_id.lower() == username.lower():

                    details = [
                        "✅ TikTok",
                        f"🔗 الرابط: {url}",
                        f"📊 تقييم اليوزر: {analyze_username_strength(username)}",
                        f"👤 الاسم: {info.get('nickname') or 'غير محدد'}",
                        f"📝 البايو: {info.get('signature') or 'لا يوجد'}"
                    ]

                    stats = stats_all.get(
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

    except Exception:
        pass

    return None


# =========================================================
# Snapchat
# =========================================================

def check_snapchat(username):

    encoded = encode_username(username)

    url = (
        f"https://www.snapchat.com/add/"
        f"{encoded}"
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
            "✅ Snapchat",
            f"🔗 الرابط: {url}",
            f"📊 تقييم اليوزر: {analyze_username_strength(username)}",
            "👻 يمكنك فتح الرابط للتحقق من الحساب."
        ]

        return "\n".join(details)

    except Exception:
        pass

    return None


# =========================================================
# Instagram
# =========================================================

def check_instagram(username):

    encoded = encode_username(username)

    url = (
        f"https://www.instagram.com/"
        f"{encoded}/"
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
            "✅ Instagram",
            f"🔗 الرابط: {url}",
            f"📊 تقييم اليوزر: {analyze_username_strength(username)}"
        ]

        return "\n".join(details)

    except Exception:
        pass

    return None


# =========================================================
# Reddit
# =========================================================

def check_reddit(username):

    encoded = encode_username(username)

    url = (
        f"https://www.reddit.com/user/"
        f"{encoded}/about.json"
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

        account_name = data.get(
            "name",
            ""
        )

        if (
            data.get("id")
            and account_name.lower() == username.lower()
        ):

            details = [
                "✅ Reddit",
                f"🔗 الرابط: https://www.reddit.com/user/{username}",
                f"📊 تقييم اليوزر: {analyze_username_strength(username)}",
                f"🔥 الكارما الإجمالية: {data.get('total_karma', 0)}"
            ]

            return "\n".join(details)

    except Exception:
        pass

    return None


# =========================================================
# فحص المنصات العامة
# =========================================================

def check_general_platform(
    name,
    url_template,
    username
):

    encoded = encode_username(username)

    url = url_template.format(
        encoded
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
            f"✅ {name}",
            f"🔗 الرابط: {url}",
            f"📊 تقييم اليوزر: {analyze_username_strength(username)}"
        ]

        return "\n".join(details)

    except Exception:
        pass

    return None


# =========================================================
# /start
# =========================================================

@bot.message_handler(
    commands=["start"]
)
def send_welcome(message):

    welcome_text = (
        "👋 مرحباً بك في أداة البصمة الرقمية!\n\n"
        "🔍 أرسل اليوزر مباشرة.\n\n"
        "أمثلة:\n"
        "Lann_100\n"
        "user-name\n"
        "user.name\n"
        "@username"
    )

    bot.send_message(
        message.chat.id,
        welcome_text
    )


# =========================================================
# البحث
# =========================================================

@bot.message_handler(
    func=lambda message: True
)
def search_username(message):

    username = normalize_username(
        message.text
    )

    if not username:
        return

    # حماية بسيطة من إدخال طويل جدًا
    if len(username) > 100:

        bot.reply_to(
            message,
            "⚠️ اليوزر طويل جدًا."
        )

        return

    msg = bot.reply_to(
        message,
        f"🔍 جاري الفحص الشامل لـ (@{username})...\n"
        "يرجى الانتظار."
    )

    # =====================================================
    # جميع المنصات
    # =====================================================

    platforms = {

        "snapchat": lambda:
            check_snapchat(username),

        "tiktok": lambda:
            check_tiktok(username),

        "instagram": lambda:
            check_instagram(username),

        "reddit": lambda:
            check_reddit(username),

        "github": lambda:
            check_github(username),

        "twitter": lambda:
            check_general_platform(
                "Twitter / X",
                "https://twitter.com/{}",
                username
            ),

        "pinterest": lambda:
            check_general_platform(
                "Pinterest",
                "https://www.pinterest.com/{}/",
                username
            ),

        "twitch": lambda:
            check_general_platform(
                "Twitch",
                "https://www.twitch.tv/{}",
                username
            )
    }

    # =====================================================
    # الفحص
    # =====================================================

    found_results = {}

    for key, function in platforms.items():

        try:

            result = function()

            if result:

                found_results[key] = result

        except Exception:
            continue

    # =====================================================
    # حذف رسالة الانتظار
    # =====================================================

    try:

        bot.delete_message(
            message.chat.id,
            msg.message_id
        )

    except Exception:
        pass

    # =====================================================
    # لا توجد نتائج
    # =====================================================

    if not found_results:

        bot.send_message(
            message.chat.id,
            f"❌ لم يتم العثور على حسابات مطابقة لـ (@{username})."
        )

        return

    # =====================================================
    # حفظ النتائج
    # =====================================================

    USER_CACHE[message.chat.id] = found_results

    # =====================================================
    # أزرار المنصات
    # =====================================================

    markup = InlineKeyboardMarkup()

    platform_names = {

        "snapchat": "👻 سناب شات",

        "tiktok": "🎵 تيك توك",

        "instagram": "📸 إنستغرام",

        "github": "🐙 GitHub",

        "reddit": "🤖 Reddit",

        "twitter": "🐦 Twitter / X",

        "pinterest": "📌 Pinterest",

        "twitch": "💜 Twitch"
    }

    buttons = []

    for key in found_results.keys():

        button = InlineKeyboardButton(
            platform_names.get(
                key,
                key
            ),
            callback_data=f"show_{key}"
        )

        buttons.append(button)

    # ترتيب الأزرار صفين
    for i in range(
        0,
        len(buttons),
        2
    ):

        markup.row(
            *buttons[i:i + 2]
        )

    # =====================================================
    # إرسال النتائج
    # =====================================================

    bot.send_message(
        message.chat.id,
        (
            f"🎯 تم العثور على الحساب (@{username}) "
            "في المنصات التالية:"
        ),
        reply_markup=markup
    )


# =========================================================
# الضغط على زر المنصة
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
            user_data[platform_key]
        )

    else:

        bot.answer_callback_query(
            call.id,
            "⚠️ انتهت صلاحية النتائج.",
            show_alert=True
        )


# =========================================================
# تشغيل البوت
# =========================================================

if __name__ == "__main__":

    print("🚀 Starting OSINT Telegram Bot...")

    # إزالة Webhook القديم
    try:
        bot.remove_webhook()
    except Exception:
        pass

    # إنشاء Webhook الجديد
    bot.set_webhook(
        url=WEBHOOK_URL
    )

    print(
        f"✅ Webhook set: {RENDER_URL}"
    )

    # Render يعطي PORT تلقائيًا
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

import re
import json
import requests
import urllib.parse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask, request


# =========================================================
# CONFIG
# =========================================================

TOKEN = "8974546244:AAGSIwbh9FmENOiKYP2tS33_Z-ixjPl0cl4"

RENDER_URL = "https://osint-bot-t0vn.onrender.com"

# لا نضع التوكن داخل رابط Webhook
WEBHOOK_PATH = "/telegram-webhook"
WEBHOOK_URL = f"{RENDER_URL.rstrip('/')}{WEBHOOK_PATH}"

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# آخر نتيجة لكل مستخدم
USER_CACHE = {}


# =========================================================
# HTTP
# =========================================================

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; PublicProfileBot/1.0)"
}


def http_get(url, headers=None, params=None, timeout=10):
    try:
        final_headers = DEFAULT_HEADERS.copy()

        if headers:
            final_headers.update(headers)

        return requests.get(
            url,
            headers=final_headers,
            params=params,
            timeout=timeout,
            allow_redirects=True
        )

    except requests.RequestException:
        return None


# =========================================================
# HELPERS
# =========================================================

def normalize_username(text):
    username = text.strip()

    if username.startswith("@"):
        username = username[1:]

    return username.strip()


def encode_username(username):
    return urllib.parse.quote(username, safe="")


def value(data, default="غير متوفر"):
    if data is None:
        return default

    text = str(data).strip()

    return text if text else default


def format_number(number):
    if number is None:
        return "غير متوفر"

    try:
        return f"{int(number):,}"
    except Exception:
        return str(number)


def format_date(date_string):
    if not date_string:
        return "غير متوفر"

    try:
        dt = datetime.fromisoformat(
            str(date_string).replace("Z", "+00:00")
        )

        return dt.strftime("%Y-%m-%d %H:%M UTC")

    except Exception:
        return str(date_string)


def format_timestamp(timestamp):
    if not timestamp:
        return "غير متوفر"

    try:
        dt = datetime.fromtimestamp(float(timestamp))

        return dt.strftime("%Y-%m-%d %H:%M")

    except Exception:
        return "غير متوفر"


def profile_strength(username):
    length = len(username)

    if length >= 12:
        return "قوي"
    elif length >= 7:
        return "متوسط"
    else:
        return "قصير"


def safe_url(url):
    if not url:
        return "غير متوفر"

    return str(url)


def send_long_message(chat_id, text):
    """
    Telegram لديه حد لطول الرسالة.
    نقسم التقرير الطويل إلى عدة رسائل.
    """

    limit = 3900

    while text:

        if len(text) <= limit:
            bot.send_message(chat_id, text)
            break

        cut = text.rfind("\n", 0, limit)

        if cut < 500:
            cut = limit

        part = text[:cut]

        bot.send_message(chat_id, part)

        text = text[cut:].lstrip()


def get_meta(soup, property_name=None, name=None):
    if property_name:
        tag = soup.find("meta", attrs={"property": property_name})

        if tag and tag.get("content"):
            return tag.get("content").strip()

    if name:
        tag = soup.find("meta", attrs={"name": name})

        if tag and tag.get("content"):
            return tag.get("content").strip()

    return None


def get_page_metadata(soup):
    title = soup.title.string.strip() if soup.title and soup.title.string else None

    return {
        "title": title,
        "description": get_meta(soup, property_name="og:description")
                       or get_meta(soup, name="description"),
        "image": get_meta(soup, property_name="og:image"),
        "url": get_meta(soup, property_name="og:url"),
        "type": get_meta(soup, property_name="og:type"),
    }


def looks_not_found(text):
    if not text:
        return False

    text = text.lower()

    bad_words = [
        "page not found",
        "user not found",
        "profile not found",
        "doesn't exist",
        "does not exist",
        "this page isn't available",
        "this page is not available",
        "404 not found"
    ]

    return any(word in text for word in bad_words)


# =========================================================
# GITHUB
# =========================================================

def check_github(username):

    encoded = encode_username(username)

    url = f"https://api.github.com/users/{encoded}"

    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "PublicProfileBot/1.0"
    }

    response = http_get(
        url,
        headers=headers,
        timeout=10
    )

    if not response:
        return None

    if response.status_code == 404:
        return None

    if response.status_code != 200:
        return (
            "🐙 GitHub\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ تعذر قراءة الحساب\n"
            f"HTTP: {response.status_code}\n"
            f"🔗 {url}"
        )

    try:
        data = response.json()
    except Exception:
        return None

    login = data.get("login")

    if not login:
        return None

    lines = [
        "🐙 GITHUB",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"👤 Username: {value(login)}",
        f"📛 Name: {value(data.get('name'))}",
        f"📝 Bio: {value(data.get('bio'))}",
        f"🏢 Company: {value(data.get('company'))}",
        f"🌐 Website: {value(data.get('blog'))}",
        f"👥 Followers: {format_number(data.get('followers'))}",
        f"👤 Following: {format_number(data.get('following'))}",
        f"📦 Public repositories: {format_number(data.get('public_repos'))}",
        f"📝 Public gists: {format_number(data.get('public_gists'))}",
        f"🆔 GitHub ID: {value(data.get('id'))}",
        f"🏷️ Account type: {value(data.get('type'))}",
        f"📅 Created: {format_date(data.get('created_at'))}",
        f"🔄 Updated: {format_date(data.get('updated_at'))}",
        f"🖼️ Avatar: {safe_url(data.get('avatar_url'))}",
        "",
        f"🔗 Profile: {safe_url(data.get('html_url'))}",
        ""
    ]

    # -----------------------------------------------------
    # أحدث المستودعات العامة
    # -----------------------------------------------------

    repos_url = f"https://api.github.com/users/{encoded}/repos"

    repos_response = http_get(
        repos_url,
        headers=headers,
        params={
            "per_page": 5,
            "sort": "updated",
            "direction": "desc"
        },
        timeout=10
    )

    if repos_response and repos_response.status_code == 200:

        try:
            repos = repos_response.json()

            if repos:
                lines.append("📚 LATEST PUBLIC REPOSITORIES")
                lines.append("━━━━━━━━━━━━━━━━━━━━")

                for index, repo in enumerate(repos, start=1):

                    repo_name = value(repo.get("name"))

                    description = value(
                        repo.get("description"),
                        "بدون وصف"
                    )

                    language = value(
                        repo.get("language"),
                        "غير محددة"
                    )

                    stars = format_number(repo.get("stargazers_count"))
                    forks = format_number(repo.get("forks_count"))

                    repo_url = safe_url(repo.get("html_url"))

                    lines.extend([
                        "",
                        f"{index}. 📦 {repo_name}",
                        f"   📝 {description}",
                        f"   💻 Language: {language}",
                        f"   ⭐ Stars: {stars}",
                        f"   🍴 Forks: {forks}",
                        f"   🔗 {repo_url}"
                    ])

        except Exception:
            pass

    return "\n".join(lines)


# =========================================================
# REDDIT
# =========================================================

def check_reddit(username):

    encoded = encode_username(username)

    url = f"https://www.reddit.com/user/{encoded}/about.json"

    headers = {
        "User-Agent": "PublicProfileBot/1.0"
    }

    response = http_get(
        url,
        headers=headers,
        timeout=10
    )

    if not response:
        return None

    if response.status_code != 200:
        return None

    try:
        payload = response.json()
        data = payload.get("data", {})
    except Exception:
        return None

    if not data:
        return None

    lines = [
        "🔴 REDDIT",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"👤 Username: {value(data.get('name'))}",
        f"🆔 ID: {value(data.get('id'))}",
        f"🏆 Link Karma: {format_number(data.get('link_karma'))}",
        f"💬 Comment Karma: {format_number(data.get('comment_karma'))}",
        f"⭐ Total Karma: {format_number(data.get('total_karma'))}",
        f"📅 Created: {format_timestamp(data.get('created_utc'))}",
        f"🛡️ Moderator: {value(data.get('is_mod'))}",
        f"🔗 Profile: https://www.reddit.com/user/{encoded}/"
    ]

    icon = data.get("icon_img")

    if icon:
        lines.append(f"🖼️ Avatar: {icon}")

    subreddit = data.get("subreddit")

    if isinstance(subreddit, dict):
        display_name = subreddit.get("display_name")

        if display_name:
            lines.append(
                f"👤 Profile subreddit: r/{display_name}"
            )

    return "\n".join(lines)


# =========================================================
# TIKTOK
# =========================================================

def check_tiktok(username):

    encoded = encode_username(username)

    url = f"https://www.tiktok.com/@{encoded}"

    response = http_get(url, timeout=10)

    if not response:
        return None

    if response.status_code == 404:
        return None

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    metadata = get_page_metadata(soup)

    # TikTok public structured data
    script = soup.find(
        "script",
        id="SIGI_STATE"
    )

    if script and script.string:

        try:
            data = json.loads(script.string)

            users = data.get("UserModule", {}).get("users", {})

            user_data = None

            for _, item in users.items():

                if isinstance(item, dict):

                    if (
                        item.get("uniqueId", "").lower()
                        == username.lower()
                    ):
                        user_data = item
                        break

            if user_data:

                stats_all = data.get(
                    "UserModule",
                    {}
                ).get("stats", {})

                stats = {}

                for key, item in stats_all.items():

                    if isinstance(item, dict):
                        stats = item
                        break

                lines = [
                    "🎵 TIKTOK",
                    "━━━━━━━━━━━━━━━━━━━━",
                    "",
                    f"👤 Username: {value(user_data.get('uniqueId'))}",
                    f"📛 Nickname: {value(user_data.get('nickname'))}",
                    f"📝 Bio: {value(user_data.get('signature'))}",
                    f"✅ Verified: {value(user_data.get('verified'))}",
                    f"🔒 Private: {value(user_data.get('privateAccount'))}",
                    f"👥 Followers: {format_number(stats.get('followerCount'))}",
                    f"👤 Following: {format_number(stats.get('followingCount'))}",
                    f"❤️ Likes: {format_number(stats.get('heartCount'))}",
                    f"🎬 Videos: {format_number(stats.get('videoCount'))}",
                    f"🖼️ Avatar: {value(user_data.get('avatarLarger'))}",
                    f"🔗 Profile: {url}"
                ]

                return "\n".join(lines)

        except Exception:
            pass

    # Fallback إلى OpenGraph
    if metadata["title"] or metadata["description"]:

        combined = " ".join([
            value(metadata["title"], ""),
            value(metadata["description"], "")
        ])

        if not looks_not_found(combined):

            return "\n".join([
                "🎵 TIKTOK",
                "━━━━━━━━━━━━━━━━━━━━",
                "",
                f"👤 Username: @{username}",
                f"📛 Page title: {value(metadata['title'])}",
                f"📝 Description: {value(metadata['description'])}",
                f"🖼️ Image: {value(metadata['image'])}",
                f"🔗 Profile: {url}",
                "",
                "ℹ️ بعض الإحصائيات قد لا تكون متاحة بدون وصول رسمي للمنصة."
            ])

    return None


# =========================================================
# INSTAGRAM
# =========================================================

def check_instagram(username):

    encoded = encode_username(username)

    url = f"https://www.instagram.com/{encoded}/"

    response = http_get(url, timeout=10)

    if not response:
        return None

    if response.status_code == 404:
        return None

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    metadata = get_page_metadata(soup)

    combined = " ".join([
        value(metadata["title"], ""),
        value(metadata["description"], "")
    ])

    if looks_not_found(combined):
        return None

    if not metadata["title"] and not metadata["description"]:
        return None

    return "\n".join([
        "📸 INSTAGRAM",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"👤 Username: @{username}",
        f"📛 Page title: {value(metadata['title'])}",
        f"📝 Public description: {value(metadata['description'])}",
        f"🖼️ Profile image: {value(metadata['image'])}",
        f"🔗 Profile: {url}",
        "",
        "ℹ️ بيانات Instagram التفصيلية قد تتطلب وصولًا رسميًا من Meta."
    ])


# =========================================================
# SNAPCHAT
# =========================================================

def check_snapchat(username):

    encoded = encode_username(username)

    url = f"https://www.snapchat.com/add/{encoded}"

    response = http_get(url, timeout=10)

    if not response:
        return None

    if response.status_code == 404:
        return None

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    metadata = get_page_metadata(soup)

    combined = " ".join([
        value(metadata["title"], ""),
        value(metadata["description"], "")
    ])

    if looks_not_found(combined):
        return None

    if not metadata["title"] and not metadata["description"]:
        return None

    return "\n".join([
        "👻 SNAPCHAT",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"👤 Username: @{username}",
        f"📛 Page title: {value(metadata['title'])}",
        f"📝 Public description: {value(metadata['description'])}",
        f"🖼️ Image: {value(metadata['image'])}",
        f"🔗 Profile: {url}"
    ])


# =========================================================
# GENERAL PLATFORM
# =========================================================

def check_general_platform(
    platform_name,
    url_template,
    username
):

    encoded = encode_username(username)

    url = url_template.format(encoded)

    response = http_get(url, timeout=10)

    if not response:
        return None

    if response.status_code == 404:
        return None

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    metadata = get_page_metadata(soup)

    combined = " ".join([
        value(metadata["title"], ""),
        value(metadata["description"], "")
    ])

    if looks_not_found(combined):
        return None

    if not metadata["title"] and not metadata["description"]:
        return None

    return "\n".join([
        f"🌐 {platform_name.upper()}",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"👤 Username: @{username}",
        f"📛 Page title: {value(metadata['title'])}",
        f"📝 Public description: {value(metadata['description'])}",
        f"🖼️ Image: {value(metadata['image'])}",
        f"🔗 Profile: {url}",
        "",
        "ℹ️ التفاصيل المعروضة هي البيانات العامة التي أمكن قراءتها من الصفحة."
    ])


# =========================================================
# PLATFORM LIST
# =========================================================

def get_platforms(username):

    return {
        "snapchat": (
            "👻 Snapchat",
            lambda: check_snapchat(username)
        ),

        "tiktok": (
            "🎵 TikTok",
            lambda: check_tiktok(username)
        ),

        "instagram": (
            "📸 Instagram",
            lambda: check_instagram(username)
        ),

        "reddit": (
            "🔴 Reddit",
            lambda: check_reddit(username)
        ),

        "github": (
            "🐙 GitHub",
            lambda: check_github(username)
        ),

        "twitter": (
            "𝕏 Twitter / X",
            lambda: check_general_platform(
                "Twitter / X",
                "https://twitter.com/{}",
                username
            )
        ),

        "pinterest": (
            "📌 Pinterest",
            lambda: check_general_platform(
                "Pinterest",
                "https://www.pinterest.com/{}/",
                username
            )
        ),

        "twitch": (
            "🎮 Twitch",
            lambda: check_general_platform(
                "Twitch",
                "https://www.twitch.tv/{}",
                username
            )
        )
    }


# =========================================================
# START
# =========================================================

@bot.message_handler(commands=["start"])
def start(message):

    text = (
        "🤖 OSINT PUBLIC PROFILE BOT\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "أرسل Username وسأبحث عنه في المنصات العامة.\n\n"
        "مثال:\n"
        "Lann_100\n\n"
        "أو:\n"
        "@Lann_100\n\n"
        "📊 عند العثور على حساب، يمكنك فتح تقريره الكامل."
    )

    bot.send_message(
        message.chat.id,
        text
    )


# =========================================================
# SEARCH
# =========================================================

@bot.message_handler(func=lambda message: True)
def search_username(message):

    username = normalize_username(message.text)

    if not username:
        bot.send_message(
            message.chat.id,
            "❌ أرسل Username صحيح."
        )
        return

    if len(username) > 100:
        bot.send_message(
            message.chat.id,
            "❌ Username طويل جدًا."
        )
        return

    waiting = bot.send_message(
        message.chat.id,
        "🔎 جاري فحص المنصات العامة...\n"
        "⏳ انتظر قليلًا."
    )

    platforms = get_platforms(username)

    results = {}

    # تشغيل الفحوصات بالتوازي لتقليل وقت الانتظار
    with ThreadPoolExecutor(max_workers=6) as executor:

        future_map = {
            executor.submit(function): key
            for key, (_, function) in platforms.items()
        }

        for future in as_completed(future_map):

            key = future_map[future]

            try:
                result = future.result()

                if result:
                    results[key] = result

            except Exception:
                pass

    # حفظ النتائج
    USER_CACHE[message.chat.id] = {
        "username": username,
        "results": results
    }

    try:
        bot.delete_message(
            message.chat.id,
            waiting.message_id
        )
    except Exception:
        pass

    # -----------------------------------------------------
    # SUMMARY
    # -----------------------------------------------------

    lines = [
        "🔎 PUBLIC ACCOUNT SEARCH",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"👤 Username: {username}",
        f"📏 Length: {len(username)}",
        f"💪 Username strength: {profile_strength(username)}",
        "",
        f"📊 Found accounts: {len(results)}",
        f"🌐 Checked platforms: {len(platforms)}",
        ""
    ]

    if results:
        lines.append("✅ ACCOUNTS FOUND")
        lines.append("━━━━━━━━━━━━━━━━━━━━")

        for key, (display_name, _) in platforms.items():

            if key in results:
                lines.append(
                    f"✅ {display_name}"
                )

        lines.extend([
            "",
            "👇 اضغط على المنصة لعرض التقرير الكامل."
        ])

    else:

        lines.extend([
            "❌ لم يتم العثور على حسابات مؤكدة.",
            "",
            "ℹ️ بعض المنصات تمنع القراءة العامة أو تتطلب وصولًا رسميًا."
        ])

    keyboard = InlineKeyboardMarkup(row_width=2)

    for key, (display_name, _) in platforms.items():

        if key in results:

            keyboard.add(
                InlineKeyboardButton(
                    display_name,
                    callback_data=f"show_{key}"
                )
            )

    bot.send_message(
        message.chat.id,
        "\n".join(lines),
        reply_markup=keyboard if results else None
    )


# =========================================================
# CALLBACK
# =========================================================

@bot.callback_query_handler(
    func=lambda call: call.data.startswith("show_")
)
def handle_platform_callback(call):

    chat_id = call.message.chat.id

    platform_key = call.data.replace(
        "show_",
        "",
        1
    )

    user_data = USER_CACHE.get(chat_id)

    if not user_data:
        bot.answer_callback_query(
            call.id,
            "❌ انتهت نتيجة البحث."
        )
        return

    results = user_data.get(
        "results",
        {}
    )

    result = results.get(platform_key)

    if not result:
        bot.answer_callback_query(
            call.id,
            "❌ لا توجد نتيجة."
        )
        return

    bot.answer_callback_query(
        call.id,
        "📊 جاري عرض التقرير..."
    )

    send_long_message(
        chat_id,
        result
    )


# =========================================================
# FLASK
# =========================================================

@app.route("/")
def home():

    return "🤖 OSINT Public Profile Bot is active!"


@app.route(
    WEBHOOK_PATH,
    methods=["POST"]
)
def webhook():

    content_type = request.headers.get(
        "content-type",
        ""
    )

    if content_type.startswith("application/json"):

        json_string = request.get_data().decode(
            "utf-8"
        )

        update = telebot.types.Update.de_json(
            json_string
        )

        bot.process_new_updates(
            [update]
        )

        return "", 200

    return "", 403


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    print("🚀 Starting OSINT Public Profile Bot...")

    try:
        bot.remove_webhook()
    except Exception:
        pass

    bot.set_webhook(
        url=WEBHOOK_URL
    )

    print(
        f"✅ Webhook set: {WEBHOOK_URL}"
    )

    port = 10000

    app.run(
        host="0.0.0.0",
        port=port
    )

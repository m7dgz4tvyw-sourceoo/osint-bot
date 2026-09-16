import json
import requests
import urllib.parse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask, request
from bs4 import BeautifulSoup


# =========================================================
# CONFIG
# =========================================================

# ضع توكن جديد هنا
TOKEN = "حط_توكن_جديد_هنا"

RENDER_URL = "https://osint-bot-t0vn.onrender.com"

# مسار webhook مستقل عن التوكن
WEBHOOK_PATH = "/telegram-webhook"
WEBHOOK_URL = f"{RENDER_URL.rstrip('/')}{WEBHOOK_PATH}"

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

USER_CACHE = {}


# =========================================================
# HTTP
# =========================================================

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    )
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

    if not text:
        return default

    return text


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

        dt = datetime.fromtimestamp(
            float(timestamp)
        )

        return dt.strftime("%Y-%m-%d %H:%M")

    except Exception:
        return "غير متوفر"


def profile_strength(username):

    length = len(username)

    if length >= 12:
        return "قوي"

    if length >= 7:
        return "متوسط"

    return "قصير"


def send_long_message(chat_id, text):

    limit = 3900

    while text:

        if len(text) <= limit:

            bot.send_message(
                chat_id,
                text
            )

            break

        cut = text.rfind(
            "\n",
            0,
            limit
        )

        if cut < 500:
            cut = limit

        part = text[:cut]

        bot.send_message(
            chat_id,
            part
        )

        text = text[cut:].lstrip()


# =========================================================
# HTML METADATA
# =========================================================

def get_meta(
    soup,
    property_name=None,
    name=None
):

    if property_name:

        tag = soup.find(
            "meta",
            attrs={
                "property": property_name
            }
        )

        if tag and tag.get("content"):
            return tag.get("content").strip()

    if name:

        tag = soup.find(
            "meta",
            attrs={
                "name": name
            }
        )

        if tag and tag.get("content"):
            return tag.get("content").strip()

    return None


def get_page_metadata(soup):

    title = None

    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    return {
        "title": title,

        "description":
            get_meta(
                soup,
                property_name="og:description"
            )
            or
            get_meta(
                soup,
                name="description"
            ),

        "image":
            get_meta(
                soup,
                property_name="og:image"
            ),

        "url":
            get_meta(
                soup,
                property_name="og:url"
            ),

        "type":
            get_meta(
                soup,
                property_name="og:type"
            )
    }


def looks_not_found(text):

    if not text:
        return False

    text = text.lower()

    bad_words = [
        "page not found",
        "user not found",
        "profile not found",
        "account not found",
        "doesn't exist",
        "does not exist",
        "this page isn't available",
        "this page is not available",
        "404 not found"
    ]

    return any(
        word in text
        for word in bad_words
    )


# =========================================================
# GITHUB
# =========================================================

def check_github(username):

    encoded = encode_username(username)

    api_url = (
        f"https://api.github.com/users/{encoded}"
    )

    headers = {
        "Accept":
            "application/vnd.github+json",

        "X-GitHub-Api-Version":
            "2026-03-10",

        "User-Agent":
            "PublicProfileBot/1.0"
    }

    response = http_get(
        api_url,
        headers=headers,
        timeout=10
    )

    if not response:
        return None

    if response.status_code == 404:
        return None

    if response.status_code != 200:

        return {
            "status": "possible",
            "text": "\n".join([
                "🐙 GITHUB",
                "━━━━━━━━━━━━━━━━━━━━",
                "",
                f"👤 Username: {username}",
                f"⚠️ HTTP: {response.status_code}",
                f"🔗 https://github.com/{username}",
                "",
                "⚠️ تعذر تأكيد البيانات من API."
            ])
        }

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

        "🟢 الحالة: مؤكد",
        "",

        f"👤 Username: {value(login)}",
        f"📛 Name: {value(data.get('name'))}",
        f"📝 Bio: {value(data.get('bio'))}",
        f"🏢 Company: {value(data.get('company'))}",
        f"🌐 Website: {value(data.get('blog'))}",

        f"👥 Followers: "
        f"{format_number(data.get('followers'))}",

        f"👤 Following: "
        f"{format_number(data.get('following'))}",

        f"📦 Public repositories: "
        f"{format_number(data.get('public_repos'))}",

        f"📝 Public gists: "
        f"{format_number(data.get('public_gists'))}",

        f"🆔 ID: {value(data.get('id'))}",

        f"🏷️ Type: "
        f"{value(data.get('type'))}",

        f"📅 Created: "
        f"{format_date(data.get('created_at'))}",

        f"🔄 Updated: "
        f"{format_date(data.get('updated_at'))}",

        f"🖼️ Avatar: "
        f"{value(data.get('avatar_url'))}",

        "",
        f"🔗 Profile: "
        f"{value(data.get('html_url'))}"
    ]

    # -----------------------------------------------------
    # PUBLIC REPOSITORIES
    # -----------------------------------------------------

    repos_url = (
        f"https://api.github.com/users/"
        f"{encoded}/repos"
    )

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

    if (
        repos_response
        and repos_response.status_code == 200
    ):

        try:

            repos = repos_response.json()

            if repos:

                lines.extend([
                    "",
                    "📚 LATEST PUBLIC REPOSITORIES",
                    "━━━━━━━━━━━━━━━━━━━━"
                ])

                for index, repo in enumerate(
                    repos,
                    start=1
                ):

                    repo_name = value(
                        repo.get("name")
                    )

                    description = value(
                        repo.get("description"),
                        "بدون وصف"
                    )

                    language = value(
                        repo.get("language"),
                        "غير محددة"
                    )

                    stars = format_number(
                        repo.get(
                            "stargazers_count"
                        )
                    )

                    forks = format_number(
                        repo.get(
                            "forks_count"
                        )
                    )

                    repo_url = value(
                        repo.get("html_url")
                    )

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

    return {
        "status": "confirmed",
        "text": "\n".join(lines)
    }


# =========================================================
# REDDIT
# =========================================================

def check_reddit(username):

    encoded = encode_username(username)

    url = (
        f"https://www.reddit.com/"
        f"user/{encoded}/about.json"
    )

    response = http_get(
        url,
        headers={
            "User-Agent":
                "PublicProfileBot/1.0"
        },
        timeout=10
    )

    if not response:
        return None

    if response.status_code == 404:
        return None

    if response.status_code != 200:

        return {
            "status": "possible",
            "text": "\n".join([
                "🔴 REDDIT",
                "━━━━━━━━━━━━━━━━━━━━",
                "",
                f"👤 Username: {username}",
                f"🔗 https://www.reddit.com/user/{encoded}/",
                "",
                "⚠️ تعذر قراءة بيانات الحساب."
            ])
        }

    try:

        payload = response.json()

        data = payload.get(
            "data",
            {}
        )

    except Exception:
        return None

    if not data:
        return None

    lines = [

        "🔴 REDDIT",
        "━━━━━━━━━━━━━━━━━━━━",
        "",

        "🟢 الحالة: مؤكد",
        "",

        f"👤 Username: "
        f"{value(data.get('name'))}",

        f"🆔 ID: "
        f"{value(data.get('id'))}",

        f"🏆 Link Karma: "
        f"{format_number(data.get('link_karma'))}",

        f"💬 Comment Karma: "
        f"{format_number(data.get('comment_karma'))}",

        f"⭐ Total Karma: "
        f"{format_number(data.get('total_karma'))}",

        f"📅 Created: "
        f"{format_timestamp(data.get('created_utc'))}",

        f"🛡️ Moderator: "
        f"{value(data.get('is_mod'))}",

        f"🔗 Profile: "
        f"https://www.reddit.com/user/{encoded}/"
    ]

    icon = data.get("icon_img")

    if icon:
        lines.append(
            f"🖼️ Avatar: {icon}"
        )

    return {
        "status": "confirmed",
        "text": "\n".join(lines)
    }


# =========================================================
# TIKTOK
# =========================================================

def check_tiktok(username):

    encoded = encode_username(username)

    url = (
        f"https://www.tiktok.com/"
        f"@{encoded}"
    )

    response = http_get(
        url,
        timeout=10
    )

    if not response:
        return None

    if response.status_code == 404:
        return None

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    metadata = get_page_metadata(soup)

    # -----------------------------------------------------
    # SIGI_STATE
    # -----------------------------------------------------

    script = soup.find(
        "script",
        id="SIGI_STATE"
    )

    if script and script.string:

        try:

            data = json.loads(
                script.string
            )

            users = (
                data
                .get("UserModule", {})
                .get("users", {})
            )

            user_data = None

            for _, item in users.items():

                if not isinstance(
                    item,
                    dict
                ):
                    continue

                if (
                    item.get(
                        "uniqueId",
                        ""
                    ).lower()
                    == username.lower()
                ):

                    user_data = item
                    break

            if user_data:

                stats_all = (
                    data
                    .get("UserModule", {})
                    .get("stats", {})
                )

                stats = {}

                for _, item in stats_all.items():

                    if isinstance(
                        item,
                        dict
                    ):
                        stats = item
                        break

                lines = [

                    "🎵 TIKTOK",
                    "━━━━━━━━━━━━━━━━━━━━",
                    "",

                    "🟢 الحالة: مؤكد",
                    "",

                    f"👤 Username: "
                    f"{value(user_data.get('uniqueId'))}",

                    f"📛 Nickname: "
                    f"{value(user_data.get('nickname'))}",

                    f"📝 Bio: "
                    f"{value(user_data.get('signature'))}",

                    f"✅ Verified: "
                    f"{value(user_data.get('verified'))}",

                    f"🔒 Private: "
                    f"{value(user_data.get('privateAccount'))}",

                    f"👥 Followers: "
                    f"{format_number(stats.get('followerCount'))}",

                    f"👤 Following: "
                    f"{format_number(stats.get('followingCount'))}",

                    f"❤️ Likes: "
                    f"{format_number(stats.get('heartCount'))}",

                    f"🎬 Videos: "
                    f"{format_number(stats.get('videoCount'))}",

                    f"🖼️ Avatar: "
                    f"{value(user_data.get('avatarLarger'))}",

                    f"🔗 Profile: {url}"
                ]

                return {
                    "status": "confirmed",
                    "text": "\n".join(lines)
                }

        except Exception:
            pass

    # -----------------------------------------------------
    # Metadata fallback
    # -----------------------------------------------------

    title = value(
        metadata["title"],
        ""
    )

    description = value(
        metadata["description"],
        ""
    )

    combined = (
        f"{title} {description}"
    )

    if looks_not_found(combined):
        return None

    if title or description:

        return {
            "status": "possible",
            "text": "\n".join([

                "🎵 TIKTOK",
                "━━━━━━━━━━━━━━━━━━━━",
                "",

                "🟡 الحالة: محتمل",
                "",

                f"👤 Username: @{username}",
                f"📛 Title: {value(title)}",
                f"📝 Description: "
                f"{value(description)}",
                f"🖼️ Image: "
                f"{value(metadata['image'])}",

                f"🔗 Profile: {url}",

                "",
                "ℹ️ الصفحة استجابت، "
                "لكن البيانات التفصيلية غير متاحة."
            ])
        }

    return None


# =========================================================
# INSTAGRAM
# =========================================================

def check_instagram(username):

    encoded = encode_username(username)

    url = (
        f"https://www.instagram.com/"
        f"{encoded}/"
    )

    response = http_get(
        url,
        timeout=10
    )

    if not response:
        return None

    if response.status_code == 404:
        return None

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    metadata = get_page_metadata(soup)

    title = value(
        metadata["title"],
        ""
    )

    description = value(
        metadata["description"],
        ""
    )

    combined = (
        f"{title} {description}"
    )

    if looks_not_found(combined):
        return None

    if title or description:

        return {
            "status": "possible",
            "text": "\n".join([

                "📸 INSTAGRAM",
                "━━━━━━━━━━━━━━━━━━━━",
                "",

                "🟡 الحالة: محتمل",
                "",

                f"👤 Username: @{username}",
                f"📛 Title: {value(title)}",
                f"📝 Public description: "
                f"{value(description)}",
                f"🖼️ Image: "
                f"{value(metadata['image'])}",

                f"🔗 Profile: {url}",

                "",
                "ℹ️ Instagram قد يحجب "
                "التفاصيل بدون وصول رسمي."
            ])
        }

    # 200 بدون metadata
    if response.status_code == 200:

        return {
            "status": "possible",
            "text": "\n".join([

                "📸 INSTAGRAM",
                "━━━━━━━━━━━━━━━━━━━━",
                "",

                "🟡 الحالة: محتمل",
                "",

                f"👤 Username: @{username}",
                f"🔗 Profile: {url}",

                "",
                "⚠️ الصفحة استجابت، "
                "لكن لم نستطع استخراج "
                "بيانات عامة كافية."
            ])
        }

    return None


# =========================================================
# SNAPCHAT
# =========================================================

def check_snapchat(username):

    encoded = encode_username(username)

    url = (
        f"https://www.snapchat.com/"
        f"add/{encoded}"
    )

    response = http_get(
        url,
        timeout=10
    )

    if not response:
        return None

    if response.status_code == 404:
        return None

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    metadata = get_page_metadata(soup)

    title = value(
        metadata["title"],
        ""
    )

    description = value(
        metadata["description"],
        ""
    )

    combined = (
        f"{title} {description}"
    )

    if looks_not_found(combined):
        return None

    if title or description:

        return {
            "status": "possible",
            "text": "\n".join([

                "👻 SNAPCHAT",
                "━━━━━━━━━━━━━━━━━━━━",
                "",

                "🟡 الحالة: محتمل",
                "",

                f"👤 Username: @{username}",
                f"📛 Title: {value(title)}",
                f"📝 Description: "
                f"{value(description)}",
                f"🖼️ Image: "
                f"{value(metadata['image'])}",

                f"🔗 Profile: {url}"
            ])
        }

    if response.status_code == 200:

        return {
            "status": "possible",
            "text": "\n".join([

                "👻 SNAPCHAT",
                "━━━━━━━━━━━━━━━━━━━━",
                "",

                "🟡 الحالة: محتمل",
                "",

                f"👤 Username: @{username}",
                f"🔗 Profile: {url}",

                "",
                "⚠️ الصفحة استجابت، "
                "لكن البيانات غير كافية."
            ])
        }

    return None


# =========================================================
# GENERAL PLATFORM
# =========================================================

def check_general_platform(
    platform_name,
    url_template,
    username
):

    encoded = encode_username(username)

    url = url_template.format(
        encoded
    )

    response = http_get(
        url,
        timeout=10
    )

    if not response:
        return None

    # 404 = غير موجود بشكل واضح
    if response.status_code == 404:
        return None

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    metadata = get_page_metadata(
        soup
    )

    title = value(
        metadata["title"],
        ""
    )

    description = value(
        metadata["description"],
        ""
    )

    combined = (
        f"{title} {description}"
    )

    if looks_not_found(combined):
        return None

    # عندنا بيانات
    if (
        title
        or description
        or metadata["image"]
    ):

        return {
            "status": "possible",
            "text": "\n".join([

                f"🌐 {platform_name.upper()}",
                "━━━━━━━━━━━━━━━━━━━━",
                "",

                "🟡 الحالة: محتمل",
                "",

                f"👤 Username: @{username}",
                f"📛 Page title: "
                f"{value(title)}",
                f"📝 Description: "
                f"{value(description)}",
                f"🖼️ Image: "
                f"{value(metadata['image'])}",

                f"🔗 Profile: {url}",

                "",
                "ℹ️ الصفحة استجابت "
                "وأعطت بيانات عامة."
            ])
        }

    # 200 بدون metadata
    if response.status_code == 200:

        return {
            "status": "possible",
            "text": "\n".join([

                f"🌐 {platform_name.upper()}",
                "━━━━━━━━━━━━━━━━━━━━",
                "",

                "🟡 الحالة: محتمل",
                "",

                f"👤 Username: @{username}",
                f"🔗 Profile: {url}",

                "",
                "⚠️ الصفحة استجابت، "
                "لكن لا توجد metadata كافية."
            ])
        }

    return None


# =========================================================
# PLATFORM LIST
# =========================================================

def get_platforms(username):

    return {

        "snapchat": (
            "👻 Snapchat",
            lambda:
                check_snapchat(username)
        ),

        "tiktok": (
            "🎵 TikTok",
            lambda:
                check_tiktok(username)
        ),

        "instagram": (
            "📸 Instagram",
            lambda:
                check_instagram(username)
        ),

        "reddit": (
            "🔴 Reddit",
            lambda:
                check_reddit(username)
        ),

        "github": (
            "🐙 GitHub",
            lambda:
                check_github(username)
        ),

        "twitter": (
            "𝕏 Twitter / X",
            lambda:
                check_general_platform(
                    "Twitter / X",
                    "https://twitter.com/{}",
                    username
                )
        ),

        "pinterest": (
            "📌 Pinterest",
            lambda:
                check_general_platform(
                    "Pinterest",
                    "https://www.pinterest.com/{}/",
                    username
                )
        ),

        "twitch": (
            "🎮 Twitch",
            lambda:
                check_general_platform(
                    "Twitch",
                    "https://www.twitch.tv/{}",
                    username
                )
        )
    }


# =========================================================
# START
# =========================================================

@bot.message_handler(
    commands=["start"]
)
def start(message):

    text = (
        "🤖 PUBLIC PROFILE SEARCH\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"

        "أرسل Username للبحث.\n\n"

        "مثال:\n"
        "osvo4\n\n"

        "أو:\n"
        "@osvo4\n\n"

        "🟢 مؤكد = تم الحصول على بيانات "
        "مباشرة.\n"

        "🟡 محتمل = الصفحة استجابت لكن "
        "المنصة لم تعطِ بيانات كافية.\n"

        "🔴 غير موجود = استجابة واضحة "
        "بعدم وجود الصفحة."
    )

    bot.send_message(
        message.chat.id,
        text
    )


# =========================================================
# SEARCH
# =========================================================

@bot.message_handler(
    func=lambda message: True
)
def search_username(message):

    username = normalize_username(
        message.text
    )

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

    platforms = get_platforms(
        username
    )

    results = {}

    # -----------------------------------------------------
    # PARALLEL CHECK
    # -----------------------------------------------------

    with ThreadPoolExecutor(
        max_workers=6
    ) as executor:

        future_map = {
            executor.submit(
                function
            ): key

            for key, (_, function)
            in platforms.items()
        }

        for future in as_completed(
            future_map
        ):

            key = future_map[
                future
            ]

            try:

                result = future.result()

                if result:
                    results[key] = result

            except Exception:
                pass

    # -----------------------------------------------------
    # CACHE
    # -----------------------------------------------------

    USER_CACHE[
        message.chat.id
    ] = {

        "username": username,

        "results": results
    }

    # -----------------------------------------------------
    # DELETE WAITING MESSAGE
    # -----------------------------------------------------

    try:

        bot.delete_message(
            message.chat.id,
            waiting.message_id
        )

    except Exception:
        pass

    # -----------------------------------------------------
    # COUNTS
    # -----------------------------------------------------

    confirmed = 0
    possible = 0

    for result in results.values():

        if result["status"] == "confirmed":
            confirmed += 1

        elif result["status"] == "possible":
            possible += 1

    not_found = (
        len(platforms)
        - len(results)
    )

    # -----------------------------------------------------
    # SUMMARY
    # -----------------------------------------------------

    lines = [

        "🔎 PUBLIC ACCOUNT SEARCH",
        "━━━━━━━━━━━━━━━━━━━━",
        "",

        f"👤 Username: {username}",

        f"📏 Length: "
        f"{len(username)}",

        f"💪 Username strength: "
        f"{profile_strength(username)}",

        "",

        "📊 RESULTS",
        "━━━━━━━━━━━━━━━━━━━━",

        f"🟢 Confirmed: {confirmed}",
        f"🟡 Possible: {possible}",
        f"🔴 Not found: {not_found}",

        ""
    ]

    # -----------------------------------------------------
    # PLATFORM STATUS
    # -----------------------------------------------------

    for key, (
        display_name,
        _
    ) in platforms.items():

        if key not in results:

            lines.append(
                f"🔴 {display_name}"
            )

            continue

        status = results[
            key
        ]["status"]

        if status == "confirmed":

            lines.append(
                f"🟢 {display_name}"
            )

        else:

            lines.append(
                f"🟡 {display_name}"
            )

    # -----------------------------------------------------
    # BUTTONS
    # -----------------------------------------------------

    keyboard = InlineKeyboardMarkup(
        row_width=2
    )

    for key, (
        display_name,
        _
    ) in platforms.items():

        if key in results:

            keyboard.add(
                InlineKeyboardButton(
                    display_name,
                    callback_data=(
                        f"show_{key}"
                    )
                )
            )

    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "👇 اضغط على المنصة "
        "لعرض التقرير الكامل."
    ])

    bot.send_message(
        message.chat.id,
        "\n".join(lines),
        reply_markup=(
            keyboard
            if results
            else None
        )
    )


# =========================================================
# CALLBACK
# =========================================================

@bot.callback_query_handler(
    func=lambda call:
        call.data.startswith("show_")
)
def handle_platform_callback(call):

    chat_id = (
        call.message.chat.id
    )

    platform_key = (
        call.data
        .replace(
            "show_",
            "",
            1
        )
    )

    user_data = USER_CACHE.get(
        chat_id
    )

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

    result = results.get(
        platform_key
    )

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
        result["text"]
    )


# =========================================================
# FLASK
# =========================================================

@app.route("/")
def home():

    return (
        "🤖 Public Profile Bot is active!"
    )


@app.route(
    WEBHOOK_PATH,
    methods=["POST"]
)
def webhook():

    content_type = request.headers.get(
        "content-type",
        ""
    )

    if content_type.startswith(
        "application/json"
    ):

        json_string = (
            request
            .get_data()
            .decode("utf-8")
        )

        update = (
            telebot.types.Update
            .de_json(json_string)
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

    print(
        "🚀 Starting Public Profile Bot..."
    )

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

    port = int(
        __import__("os")
        .environ
        .get(
            "PORT",
            10000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )

import os
import requests
from bs4 import BeautifulSoup
import json
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask, request

TOKEN = "8974546244:AAGSIwbh9FmENOiKYP2tS33_Z-ixjPl0cl4"
bot = telebot.TeleBot(TOKEN)

app = Flask(__name__)

WEBHOOK_URL = f"https://osint-bot-t0vn.onrender.com/{TOKEN}"

USER_CACHE = {}

@app.route('/')
def home():
    return "🤖 Interactive OSINT Bot with Special Characters Support is active!"

@app.route(f'/{TOKEN}', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return '', 200
    else:
        return '', 403

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

def check_github(username):
    url = f"https://api.github.com/users/{username}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=8)
        if res.status_code == 200:
            data = res.json()
            if "id" in data and data.get("login", "").lower() == username.lower():
                details = [
                    f"✅ **غيت هاب (GitHub)**",
                    f"🔗 الرابط: https://github.com/{username}",
                    f"📊 تقييم اليوزر: {analyze_username_strength(username)}",
                    f"🆔 المعرف الرقمي (ID): {data.get('id', 'غير متوفر')}",
                    f"👤 الاسم الكامل: {data.get('name', 'غير محدد')}",
                    f"🏢 الشركة: {data.get('company', 'لا يوجد')}",
                    f"📍 الموقع: {data.get('location', 'غير محدد')}",
                    f"📝 البايو: {data.get('bio', 'لا يوجد')}",
                    f"📦 المستودعات العامة: {data.get('public_repos', 0)}",
                    f"👥 المتابعين: {data.get('followers', 0)}",
                    f"📅 تاريخ الإنشاء: {data.get('created_at', 'غير متوفر')}"
                ]
                return "\n".join(details)
    except Exception:
        pass
    return None

def check_tiktok(username):
    url = f"https://www.tiktok.com/@{username}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0"}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            sig_data = soup.find('script', id='SIGI_STATE')
            if sig_data:
                try:
                    data = json.loads(sig_data.string)
                    users = data.get('UserModule', {}).get('users', {})
                    for uid, info in users.items():
                        if info.get('uniqueId', '').lower() == username.lower():
                            details = [
                                f"✅ **تيك توك (TikTok)**",
                                f"🔗 الرابط: {url}",
                                f"📊 تقييم اليوزر: {analyze_username_strength(username)}",
                                f"👤 الاسم الكامل: {info.get('nickname', 'غير محدد')}",
                                f"🆔 المعرّف (ID): {info.get('id', 'غير متوفر')}",
                                f"📝 البايو / النبذة: {info.get('signature', 'لا يوجد')}",
                                f"🔒 حساب خاص: {'نعم' if info.get('privateAccount') else 'لا'}",
                                f"✔️ موثق: {'نعم' if info.get('verified') else 'لا'}"
                            ]
                            stats = data.get('UserModule', {}).get('stats', {}).get(uid, {})
                            if stats:
                                details.append(f"👥 المتابعين: {stats.get('followerCount', 'مخفي')}")
                                details.append(f"❤️ إجمالي الإعجابات: {stats.get('heart', 'مخفي')}")
                            return "\n".join(details)
                except Exception:
                    pass
    except Exception:
        pass
    return None

def check_snapchat(username):
    url = f"https://www.snapchat.com/add/{username}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0"}
    try:
        res = requests.get(url, headers=headers, timeout=8)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            text_content = soup.get_text().lower()
            not_found_keywords = ["sorry", "not found", "تعذر العثور", "هذا الحساب غير موجود", "doesn't exist", "oops"]
            if any(kw in text_content for kw in not_found_keywords):
                return None
            title = soup.find('title')
            title_text = title.text.lower() if title else ""
            if "not found" in title_text or "error" in title_text:
                return None
                
            details = [
                f"✅ **سناب شات (Snapchat)**",
                f"🔗 الرابط: {url}",
                f"📊 تقييم اليوزر: {analyze_username_strength(username)}",
                f"👻 **القصص (Stories):** يمكنك فتح الرابط أعلاه لمشاهدة القصص العامة النشطة للحساب.",
                f"📌 عنوان الصفحة: {title.text.strip() if title else 'متوفر'}"
            ]
            return "\n".join(details)
    except Exception:
        pass
    return None

def check_instagram(username):
    url = f"https://www.instagram.com/{username}/"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0"}
    try:
        res = requests.get(url, headers=headers, timeout=8)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            title = soup.find('title')
            title_text = title.text.lower() if title else ""
            if "login" in title_text or title_text == "instagram" or not title_text:
                return None
                
            details = [
                f"✅ **إنستغرام (Instagram)**",
                f"🔗 الرابط: {url}",
                f"📊 تقييم اليوزر: {analyze_username_strength(username)}",
                f"📌 عنوان الصفحة: {title.text.strip() if title else 'متوفر'}"
            ]
            return "\n".join(details)
    except Exception:
        pass
    return None

def check_reddit(username):
    url = f"https://www.reddit.com/user/{username}/about.json"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=8)
        if res.status_code == 200:
            data = res.json().get('data', {})
            if data and "id" in data and data.get("name", "").lower() == username.lower():
                details = [
                    f"✅ **ريديت (Reddit)**",
                    f"🔗 الرابط: https://www.reddit.com/user/{username}",
                    f"📊 تقييم اليوزر: {analyze_username_strength(username)}",
                    f"🆔 معرف الحساب (ID): {data.get('id', 'غير متوفر')}",
                    f"🔥 الكارما الإجمالية: {data.get('total_karma', 0)}",
                    f"📅 تاريخ الانضمام (UTC): {data.get('created_utc', 'غير متوفر')}"
                ]
                return "\n".join(details)
    except Exception:
        pass
    return None

def check_general_platform(name, url_template, username):
    url = url_template.format(username)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0"}
    try:
        res = requests.get(url, headers=headers, timeout=8)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            text_content = soup.get_text().lower()
            not_found_keywords = ["page not found", "user not found", "عذراً", "هذا الحساب غير موجود", "does not exist", "not found", "404"]
            if any(kw in text_content for kw in not_found_keywords):
                return None
            title = soup.find('title')
            title_text = title.text.lower() if title else ""
            if "not found" in title_text or "error" in title_text or "404" in title_text:
                return None
                
            details = [
                f"✅ **{name}**",
                f"🔗 الرابط: {url}",
                f"📊 تقييم اليوزر: {analyze_username_strength(username)}"
            ]
            if title and title.text.strip():
                details.append(f"📌 عنوان الصفحة: {title.text.strip()}")
            return "\n".join(details)
    except Exception:
        pass
    return None

@bot.message_handler(commands=['start'])
def send_welcome(message):
    welcome_text = (
        "👋 **مرحباً بك في أداة البصمة الرقمية التفاعلية!**\n\n"
        "🔍 **أرسل اليوزر مباشرة** (يدعم الحروف، الأرقام، والشرطات السفلية `_` والنقاط `.`)."
    )
    bot.send_message(message.chat.id, welcome_text, parse_mode="Markdown")

@bot.message_handler(func=lambda message: True)
def search_username(message):
    username = message.text.strip().replace('@', '')
    if not username:
        return
        
    msg = bot.reply_to(message, f"🔍 جاري الفحص الشامل لـ (@{username})... يرجى الانتظار قليلاً.")
    
    platforms = {
        "snapchat": lambda: check_snapchat(username),
        "tiktok": lambda: check_tiktok(username),
        "instagram": lambda: check_instagram(username),
        "reddit": lambda: check_reddit(username),
        "twitter": lambda: check_general_platform("تويتر / إكس (Twitter)", "https://twitter.com/{}", username),
        "pinterest": lambda: check_general_platform("بنترست (Pinterest)", "https://www.pinterest.com/{}/", username),
        "twitch": lambda: check_general_platform("تويتش (Twitch)", "https://www.twitch.tv/{}", username)
    }
    
    # استبعاد غيت هاب تلقائياً إذا كان اليوزر يحتوي على شرطة سفلية لتفادي أخطاء النظام
    if '_' not in username:
        platforms["github"] = lambda: check_github(username)
    
    found_results = {}
    
    for key, func in platforms.items():
        try:
            res = func()
            if res:
                found_results[key] = res
        except Exception:
            continue
            
    bot.delete_message(message.chat.id, msg.message_id)
    
    if not found_results:
        bot.send_message(message.chat.id, f"❌ **لم يتم العثور على أي حسابات مطابقة لـ (@{username}).**", parse_mode="Markdown")
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
        "twitch": "💜 تويتتش"
    }
    
    buttons = []
    for key in found_results.keys():
        btn_text = platform_names.get(key, key)
        buttons.append(InlineKeyboardButton(btn_text, callback_data=f"show_{key}"))
        
    markup.add(*buttons)
    
    bot.send_message(
        message.chat.id,
        f"🎯 **تم العثور على الحساب (@{username}) في المنصات التالية:**\nاضغط على أي زر لعرض التفاصيل والمعلومات:",
        reply_markup=markup,
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("show_"))
def handle_platform_callback(call):
    chat_id = call.message.chat.id
    platform_key = call.data.replace("show_", "")
    
    user_data = USER_CACHE.get(chat_id)
    if user_data and platform_key in user_data:
        bot.answer_callback_query(call.id, "✅ يتم تحميل التفاصيل...")
        bot.send_message(chat_id, user_data[platform_key], parse_mode="Markdown")
    else:
        bot.answer_callback_query(call.id, "⚠️ انتهت صلاحية الجلسة، يرجى إعادة إرسال اليوزر من جديد.", show_alert=True)

if __name__ == "__main__":
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL)
    
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

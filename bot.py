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

@app.route('/')
def home():
    return "🤖 Strict OSINT Bot with Snapchat is active and running 24/7!"

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

def check_github_deep(username):
    url = f"https://api.github.com/users/{username}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=8)
        if res.status_code == 200:
            data = res.json()
            if "id" in data and data.get("login", "").lower() == username.lower():
                details = [f"✅ **غيت هاب (GitHub)**\n🔗 الرابط: https://github.com/{username}"]
                details.append(f"📊 تقييم اليوزر: {analyze_username_strength(username)}")
                details.append(f"🆔 المعرف الرقمي (ID): {data.get('id', 'غير متوفر')}")
                details.append(f"👤 الاسم الكامل: {data.get('name', 'غير محدد')}")
                details.append(f"🏢 الشركة: {data.get('company', 'لا يوجد')}")
                details.append(f"📍 الموقع: {data.get('location', 'غير محدد')}")
                details.append(f"📝 البايو: {data.get('bio', 'لا يوجد')}")
                details.append(f"📦 المستودعات العامة: {data.get('public_repos', 0)}")
                details.append(f"👥 المتابعين: {data.get('followers', 0)}")
                details.append(f"📅 تاريخ الإنشاء: {data.get('created_at', 'غير متوفر')}")
                return "\n".join(details)
    except Exception:
        pass
    return None

def check_tiktok_deep(username):
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
                            details = [f"✅ **تيك توك (TikTok)**\n🔗 الرابط: {url}"]
                            details.append(f"📊 تقييم اليوزر: {analyze_username_strength(username)}")
                            details.append(f"👤 الاسم الكامل: {info.get('nickname', 'غير محدد')}")
                            details.append(f"🆔 المعرّف (ID): {info.get('id', 'غير متوفر')}")
                            details.append(f"📝 البايو / النبذة: {info.get('signature', 'لا يوجد')}")
                            details.append(f"🔒 حساب خاص: {'نعم' if info.get('privateAccount') else 'لا'}")
                            details.append(f"✔️ موثق: {'نعم' if info.get('verified') else 'لا'}")
                            
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

def check_snapchat_strict(username):
    url = f"https://www.snapchat.com/add/{username}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0"}
    try:
        res = requests.get(url, headers=headers, timeout=8)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            text_content = soup.get_text().lower()
            
            # إذا كان الحساب غير موجود في سناب شات، تظهر عبارات تدل على الخطأ أو عدم العثور
            not_found_keywords = ["sorry", "not found", "تعذر العثور", "هذا الحساب غير موجود", "doesn't exist", "oops"]
            if any(kw in text_content for kw in not_found_keywords):
                return None
                
            title = soup.find('title')
            title_text = title.text.lower() if title else ""
            
            if "not found" in title_text or "error" in title_text:
                return None
                
            details = [f"✅ **سناب شات (Snapchat)**\n🔗 الرابط: {url}"]
            details.append(f"📊 تقييم اليوزر: {analyze_username_strength(username)}")
            if title and title.text.strip():
                details.append(f"📌 عنوان الصفحة: {title.text.strip()}")
                
            return "\n".join(details)
    except Exception:
        pass
    return None

def check_reddit_deep(username):
    url = f"https://www.reddit.com/user/{username}/about.json"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=8)
        if res.status_code == 200:
            data = res.json().get('data', {})
            if data and "id" in data and data.get("name", "").lower() == username.lower():
                details = [f"✅ **ريديت (Reddit)**\n🔗 الرابط: https://www.reddit.com/user/{username}"]
                details.append(f"📊 تقييم اليوزر: {analyze_username_strength(username)}")
                details.append(f"🆔 معرف الحساب (ID): {data.get('id', 'غير متوفر')}")
                details.append(f"🔥 الكارما الإجمالية: {data.get('total_karma', 0)}")
                details.append(f"📅 تاريخ الانضمام (UTC): {data.get('created_utc', 'غير متوفر')}")
                return "\n".join(details)
    except Exception:
        pass
    return None

def check_instagram_strict(username):
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
                
            details = [f"✅ **إنستغرام (Instagram)**\n🔗 الرابط: {url}"]
            details.append(f"📊 تقييم اليوزر: {analyze_username_strength(username)}")
            details.append(f"📌 عنوان الصفحة: {title.text.strip()}")
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
                
            details = [f"✅ **{name}**\n🔗 الرابط: {url}"]
            details.append(f"📊 تقييم اليوزر: {analyze_username_strength(username)}")
            
            if title and title.text.strip():
                details.append(f"📌 عنوان الصفحة: {title.text.strip()}")
                
            return "\n".join(details)
    except Exception:
        pass
    return None

@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = InlineKeyboardMarkup()
    markup.row_width = 2
    markup.add(
        InlineKeyboardButton("🔍 طريقة الاستخدام", callback_data="help_info"),
        InlineKeyboardButton("🛠️ حالة البوت", callback_data="bot_status")
    )
    
    welcome_text = (
        "👋 **مرحباً بك في أداة البصمة الرقمية (OSINT Strict Pro)!**\n\n"
        "🔍 **أرسل اليوزر مباشرة**، وسيتم فحص سناب شات وبقية المنصات بدقة للتأكد من وجود الحسابات."
    )
    bot.send_message(message.chat.id, welcome_text, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    if call.data == "help_info":
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "💡 **كيف تستخدم البوت؟**\nاكتب اسم المستخدم بدون @، والنتيجة ستعرض الحسابات الحقيقية فقط.", parse_mode="Markdown")
    elif call.data == "bot_status":
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "🟢 **البوت يعمل بنظام الفحص الدقيق 24/7.**", parse_mode="Markdown")

@bot.message_handler(func=lambda message: True)
def search_username(message):
    username = message.text.strip().replace('@', '')
    if not username:
        return
        
    msg = bot.reply_to(message, f"🔍 جاري الفحص والفلترة الدقيقة لـ (@{username})... يرجى الانتظار.")
    
    found_any = False
    
    functions_to_run = [
        lambda: check_snapchat_strict(username),
        lambda: check_github_deep(username),
        lambda: check_tiktok_deep(username),
        lambda: check_reddit_deep(username),
        lambda: check_instagram_strict(username),
        lambda: check_general_platform("تويتر / إكس (Twitter)", "https://twitter.com/{}", username),
        lambda: check_general_platform("بنترست (Pinterest)", "https://www.pinterest.com/{}/", username),
        lambda: check_general_platform("تويتش (Twitch)", "https://www.twitch.tv/{}", username)
    ]
    
    for func in functions_to_run:
        res = func()
        if res:
            found_any = True
            bot.send_message(message.chat.id, res, parse_mode="Markdown")
            
    bot.delete_message(message.chat.id, msg.message_id)
    
    if not found_any:
        bot.send_message(message.chat.id, f"❌ **لم يتم العثور على أي حسابات حقيقية أو نشطة مطابقة لـ (@{username}).**", parse_mode="Markdown")

if __name__ == "__main__":
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL)
    
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

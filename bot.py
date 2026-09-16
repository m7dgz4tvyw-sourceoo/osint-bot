import os
import requests
from bs4 import BeautifulSoup
import json
import threading
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask

TOKEN = "8974546244:AAGSIwbh9FmENOiKYP2tS33_Z-ixjPl0cl4"
bot = telebot.TeleBot(TOKEN)

app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 Advanced OSINT Bot is active and running 24/7!"

def run_bot():
    try:
        print("🤖 بوت تيليجرام يبدأ الاتصال...")
        bot.remove_webhook() # مسح أي تداخل أو اتصال قديم لمانع الخطأ 409
        bot.infinity_polling(none_stop=True, interval=1, timeout=20)
    except Exception as e:
        print(f"Error in bot polling: {e}")

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

def check_tiktok_deep(username):
    url = f"https://www.tiktok.com/@{username}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0"}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            details = [f"✅ **تيك توك (TikTok)**\n🔗 الرابط: {url}"]
            details.append(f"📊 تقييم اليوزر: {analyze_username_strength(username)}")
            
            sig_data = soup.find('script', id='SIGI_STATE')
            if sig_data:
                try:
                    data = json.loads(sig_data.string)
                    users = data.get('UserModule', {}).get('users', {})
                    for uid, info in users.items():
                        details.append(f"👤 الاسم الكامل: {info.get('nickname', 'غير محدد')}")
                        details.append(f"🆔 المعرّف (ID): {info.get('id', 'غير متوفر')}")
                        details.append(f"📝 البايو / النبذة: {info.get('signature', 'لا يوجد')}")
                        details.append(f"🔒 حساب خاص: {'نعم' if info.get('privateAccount') else 'لا'}")
                        details.append(f"✔️ موثق: {'نعم' if info.get('verified') else 'لا'}")
                    stats = data.get('UserModule', {}).get('stats', {})
                    for uid, stat in stats.items():
                        details.append(f"👥 المتابعين: {stat.get('followerCount', 'مخفي')}")
                        details.append(f"👤 يتابعهم: {stat.get('followingCount', 'مخفي')}")
                        details.append(f"❤️ إجمالي الإعجابات: {stat.get('heart', 'مخفي')}")
                        details.append(f"🎬 عدد الفيديوهات: {stat.get('videoCount', 'مخفي')}")
                except Exception:
                    pass
            return "\n".join(details)
    except Exception:
        pass
    return None

def check_instagram_deep(username):
    url = f"https://www.instagram.com/{username}/"
    headers = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            details = [f"✅ **إنستغرام (Instagram)**\n🔗 الرابط: {url}"]
            details.append(f"📊 تقييم اليوزر: {analyze_username_strength(username)}")
            
            meta_desc = soup.find('meta', property='og:description')
            if meta_desc:
                details.append(f"📌 معلومات الحساب والملخص:\n{meta_desc.get('content', '')}")
            
            title = soup.find('title')
            if title:
                details.append(f"📌 عنوان الصفحة: {title.text.strip()}")
            return "\n".join(details)
    except Exception:
        pass
    return None

def check_github_deep(username):
    url = f"https://api.github.com/users/{username}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=8)
        if res.status_code == 200:
            data = res.json()
            details = [f"✅ **غيت هاب (GitHub)**\n🔗 الرابط: https://github.com/{username}"]
            details.append(f"📊 تقييم اليوزر: {analyze_username_strength(username)}")
            details.append(f"👤 الاسم: {data.get('name', 'غير محدد')}")
            details.append(f"🏢 الشركة: {data.get('company', 'لا يوجد')}")
            details.append(f"📍 الموقع الجغرافي: {data.get('location', 'غير محدد')}")
            details.append(f"📝 نبذة المنشئ: {data.get('bio', 'لا يوجد')}")
            details.append(f"📦 المستودعات العامة: {data.get('public_repos', 0)}")
            details.append(f"👥 المتابعين: {data.get('followers', 0)}")
            details.append(f"👤 يتابعهم: {data.get('following', 0)}")
            details.append(f"📅 تاريخ إنشاء الحساب: {data.get('created_at', 'غير متوفر')}")
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
            details = [f"✅ **ريديت (Reddit)**\n🔗 الرابط: https://www.reddit.com/user/{username}"]
            details.append(f"📊 تقييم اليوزر: {analyze_username_strength(username)}")
            details.append(f"🆔 معرف الحساب (ID): {data.get('id', 'غير متوفر')}")
            details.append(f"⭐ الكارما الإجمالية: {data.get('total_karma', 0)}")
            details.append(f"📅 تاريخ الانضمام: {data.get('created_utc', 'غير متوفر')}")
            return "\n".join(details)
    except Exception:
        pass
    return check_general_platform("ريديت (Reddit)", "https://www.reddit.com/user/{}", username)

def check_snapchat_deep(username):
    url = f"https://www.snapchat.com/add/{username}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0"}
    try:
        res = requests.get(url, headers=headers, timeout=8)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            details = [f"✅ **سناب شات (Snapchat)**\n🔗 الرابط: {url}"]
            details.append(f"📊 تقييم اليوزر: {analyze_username_strength(username)}")
            title = soup.find('title')
            if title:
                details.append(f"📌 عنوان الحساب: {title.text.strip()}")
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
            details = [f"✅ **{name}**\n🔗 الرابط: {url}"]
            details.append(f"📊 تقييم اليوزر: {analyze_username_strength(username)}")
            title = soup.find('title')
            if title:
                details.append(f"📌 العنوان: {title.text.strip()}")
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
        "👋 **مرحباً بك في أداة البصمة الرقمية المتقدمة (OSINT Pro)!**\n\n"
        "🔍 **أرسل اليوزر (اسم المستخدم) مباشرة**، وسيقوم البوت باستخراج تقارير ومعلومات تفصيلية وشاملة لكل منصة."
    )
    bot.send_message(message.chat.id, welcome_text, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    if call.data == "help_info":
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "💡 **كيف تستخدم البوت؟**\nاكتب اسم المستخدم بدون علامة @ للحصول على تقرير استخباراتي عميق وشامل لكل الحسابات المرتبطة به.", parse_mode="Markdown")
    elif call.data == "bot_status":
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "🟢 **البوت يعمل بكفاءة 24/7 على السحابة ومستعد للفحص العميق.**", parse_mode="Markdown")

@bot.message_handler(func=lambda message: True)
def search_username(message):
    username = message.text.strip().replace('@', '')
    if not username:
        return
        
    msg = bot.reply_to(message, f"🔍 جاري جمع وتحليل البصمة الرقمية الشاملة لـ (@{username})... يرجى الانتظار.")
    
    found_any = False
    
    gh_res = check_github_deep(username)
    if gh_res:
        found_any = True
        bot.send_message(message.chat.id, gh_res, parse_mode="Markdown")

    tiktok_res = check_tiktok_deep(username)
    if tiktok_res:
        found_any = True
        bot.send_message(message.chat.id, tiktok_res, parse_mode="Markdown")

    ig_res = check_instagram_deep(username)
    if ig_res:
        found_any = True
        bot.send_message(message.chat.id, ig_res, parse_mode="Markdown")

    reddit_res = check_reddit_deep(username)
    if reddit_res:
        found_any = True
        bot.send_message(message.chat.id, reddit_res, parse_mode="Markdown")

    snap_res = check_snapchat_deep(username)
    if snap_res:
        found_any = True
        bot.send_message(message.chat.id, snap_res, parse_mode="Markdown")
        
    other_platforms = {
        "تويتر / إكس (Twitter)": "https://twitter.com/{}"
    }
    
    for name, template in other_platforms.items():
        res = check_general_platform(name, template, username)
        if res:
            found_any = True
            bot.send_message(message.chat.id, res, parse_mode="Markdown")
            
    if not found_any:
        bot.send_message(message.chat.id, f"❌ لم يتم العثور على أي حسابات نشطة مطابقة لـ (@{username}).", parse_mode="Markdown")
    else:
        bot.delete_message(message.chat.id, msg.message_id)

if __name__ == "__main__":
    t = threading.Thread(target=run_bot)
    t.daemon = True
    t.start()
    
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

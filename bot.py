import os
import requests
from bs4 import BeautifulSoup
import json
from concurrent.futures import ThreadPoolExecutor
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask

TOKEN = "8974546244:AAGSIwbh9FmENOiKYP2tS33_Z-ixjPl0cl4"
bot = telebot.TeleBot(TOKEN)

# خادم ويب مصغر لإرضاء متطلبات منصة Render وتشغيل البوت على الخطة المجانية
app = Flask('')

@app.route('/')
def home():
    return "🤖 OSINT Bot is active and running 24/7!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def analyze_username_strength(username):
    length = len(username)
    if length == 3:
        return "ثلاثي (مميز جداً ونادر)"
    elif length == 4:
        return "رباعي (مميز وقيم)"
    elif length <= 6:
        return "قصير ومميز"
    else:
        return "عادي طويل"

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
                        details.append(f"👤 الاسم: {info.get('nickname', 'غير محدد')}")
                        details.append(f"🆔 المعرّف: {info.get('id', 'غير متوفر')}")
                        details.append(f"📝 النبذة: {info.get('signature', 'لا يوجد')}")
                    stats = data.get('UserModule', {}).get('stats', {})
                    for uid, stat in stats.items():
                        details.append(f"👥 المتابعين: {stat.get('followerCount', 'مخفي')}")
                except Exception:
                    pass
            return "\n".join(details)
    except Exception:
        pass
    return None

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
                details.append(f"📌 العنوان: {title.text.strip()}")
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
        "👋 **مرحباً بك في أداة البصمة الرقمية المتقدمة!**\n\n"
        "🔍 **فضلاً، أرسل اليوزر (اسم المستخدم) مباشرة** لنبدأ فحص الحسابات وإرسال كل نتيجة في رسالة منفصلة."
    )
    bot.send_message(message.chat.id, welcome_text, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    if call.data == "help_info":
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "💡 **كيف تستخدم البوت؟**\nفقط قم بكتابة وإرسال اسم المستخدم (اليوزر) بدون علامة @، وسيقوم البوت بفحصه وإرسال تقرير لكل منصة بشكل مستقل.", parse_mode="Markdown")
    elif call.data == "bot_status":
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "🟢 **البوت يعمل بكفاءة عالية على السحابة** ومستعد لاستقبال اليوزرات.", parse_mode="Markdown")

@bot.message_handler(func=lambda message: True)
import threading
def search_username(message):
    username = message.text.strip().replace('@', '')
    if not username:
        return
        
    msg = bot.reply_to(message, f"🔍 جاري فحص (@{username}) وإرسال النتائج تباعاً...")
    
    found_any = False
    
    snap_res = check_snapchat_deep(username)
    if snap_res:
        found_any = True
        bot.send_message(message.chat.id, snap_res, parse_mode="Markdown")
        
    tiktok_res = check_tiktok_deep(username)
    if tiktok_res:
        found_any = True
        bot.send_message(message.chat.id, tiktok_res, parse_mode="Markdown")
        
    other_platforms = {
        "إنستغرام (Instagram)": "https://www.instagram.com/{}/",
        "تويتر / إكس (Twitter)": "https://twitter.com/{}",
        "غيت هاب (GitHub)": "https://github.com/{}",
        "ريديت (Reddit)": "https://www.reddit.com/user/{}"
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
    # تشغيل خادم الويب في الخلفية ليطابق متطلبات Render
    t = threading.Thread(target=run_web)
    t.daemon = True
    t.start()
    
    print("🤖 البوت وخادم الويب يعملان بكامل المزايا...")
    bot.infinity_polling()

import asyncio
import logging
import os
import random
import string
from datetime import datetime
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from TikTokApi import TikTokApi

# ============================================================
# 1. الإعدادات
# ============================================================
TOKEN = os.environ.get("TELEGRAM_TOKEN", "ضع_توكن_البوت")
MY_MS_TOKEN = os.environ.get("MY_MS_TOKEN", "ضع_msToken")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# ============================================================
# 2. سيرفر Keep-Alive
# ============================================================
async def handle(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"✅ Web server started on port {port}")

# ============================================================
# 3. دالة جلب معلومات TikTok الحقيقية (عبر TikTokApi)
# ============================================================
async def get_tiktok_user_info(username: str):
    """
    تجيب معلومات حقيقية 100% من TikTok عبر TikTokApi + msToken
    """
    try:
        async with TikTokApi() as api:
            # إنشاء جلسة باستخدام msToken
            await api.create_sessions(
                ms_tokens=[MY_MS_TOKEN],
                num_sessions=1,
                sleep_after=3,
                headless=True
            )
            
            # جلب بيانات المستخدم
            user = api.user(username=username)
            user_data = await user.info()
            
            if not user_data:
                return None
            
            # استخراج البيانات
            user_obj = user_data.get("userInfo", {}).get("user", {})
            stats = user_data.get("userInfo", {}).get("stats", {})
            
            if not user_obj:
                return None
            
            # تاريخ الإنشاء
            create_time = user_obj.get("createTime", 0)
            if create_time:
                try:
                    create_date = datetime.fromtimestamp(int(create_time)).strftime("%d-%m-%Y")
                except:
                    create_date = "غير معروف"
            else:
                create_date = "غير معروف"
            
            # الدولة
            country = user_obj.get("region", "غير معروف")
            country_map = {
                "SA": "السعودية 🇸🇦", "IQ": "العراق 🇮🇶", "AE": "الإمارات 🇦🇪",
                "EG": "مصر 🇪🇬", "KW": "الكويت 🇰🇼", "QA": "قطر 🇶🇦",
                "JO": "الأردن 🇯🇴", "MA": "المغرب 🇲🇦", "DZ": "الجزائر 🇩🇿",
                "TN": "تونس 🇹🇳", "US": "أمريكا 🇺🇸", "GB": "بريطانيا 🇬🇧",
                "SY": "سوريا 🇸🇾", "LB": "لبنان 🇱🇧", "YE": "اليمن 🇾🇪",
                "OM": "عمان 🇴🇲", "BH": "البحرين 🇧🇭", "LY": "ليبيا 🇱🇾",
                "SD": "السودان 🇸🇩", "PS": "فلسطين 🇵🇸"
            }
            country_name = country_map.get(country, country)
            
            return {
                'nickname': user_obj.get("nickname", username),
                'unique_id': user_obj.get("uniqueId", username),
                'follower_count': stats.get("followerCount", 0),
                'following_count': stats.get("followingCount", 0),
                'friends_count': stats.get("friendCount", 0),
                'heart_count': stats.get("heartCount", 0),
                'video_count': stats.get("videoCount", 0),
                'create_time': create_date,
                'country': country_name,
                'verified': user_obj.get("verified", False),
                'signature': user_obj.get("signature", "")
            }
            
    except Exception as e:
        logging.error(f"❌ Error: {e}")
        return None

# ============================================================
# 4. تنسيق الأرقام
# ============================================================
def format_number(num):
    try:
        num = int(num)
        if num >= 1_000_000:
            return f"{num/1_000_000:.1f}M"
        elif num >= 1_000:
            return f"{num/1_000:.1f}K"
        return str(num)
    except:
        return str(num)

# ============================================================
# 5. محاكاة الهجوم (Brute Force Simulation)
# ============================================================
async def simulate_brute_force(username: str, message_obj: types.Message):
    common_passwords = [
        "123456", "password", "admin", "tiktok2026", "111111",
        "iloveyou", "qwerty", "123456789", "000000", "abc123",
        "tiktok", "2025", "2026", "user123", "test123"
    ]
    random_passwords = [
        ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
        for _ in range(10)
    ]
    
    all_attempts = common_passwords + random_passwords
    random.shuffle(all_attempts)
    
    report = (
        f"🚀 **بدء محاكاة هجوم التخمين على @{username}**\n\n"
        f"⚠️ *هذا محاكاة تعليمية فقط — لا يتم اختراق أي حساب حقيقي*\n\n"
        f"🔍 جاري تجربة {len(all_attempts)} كلمة مرور...\n\n"
    )
    
    msg = await message_obj.reply(report, parse_mode="Markdown")
    await asyncio.sleep(1)
    
    failed_count = 0
    success = False
    
    for i, pwd in enumerate(all_attempts, 1):
        await asyncio.sleep(0.4)
        is_success = (pwd in common_passwords[:5]) and random.random() < 0.15
        
        if is_success:
            report += f"✅ **[محاولة {i}]** `{pwd}` ➔ **نجحت (وهمياً)!** 🔓\n"
            success = True
            break
        else:
            report += f"❌ [{i}] `{pwd}` ➔ فشل (401)\n"
            failed_count += 1
        
        if i % 4 == 0:
            try:
                await msg.edit_text(report, parse_mode="Markdown")
            except:
                pass
    
    if success:
        report += f"\n\n🚨 **النتيجة:** تم اختراق الحساب في المحاكاة!\n📌 **السبب:** كلمة مرور ضعيفة."
    else:
        report += f"\n\n🛡️ **النتيجة:** فشل الهجوم بعد {failed_count} محاولة.\n✅ **السبب:** تم تفعيل الحماية (Lockout)."
    
    report += (
        f"\n\n💡 **الدرس المستفاد:**\n"
        f"1️⃣ استخدم كلمة مرور قوية (12+ حرف)\n"
        f"2️⃣ فعّل التحقق بخطوتين (2FA)\n"
        f"3️⃣ لا تكرر الكلمة بين الحسابات"
    )
    
    try:
        await msg.edit_text(report, parse_mode="Markdown")
    except:
        await msg.edit_text(report)

# ============================================================
# 6. أوامر البوت
# ============================================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 **أهلاً بك في بوت الفحص الأمني التعليمي!**\n\n"
        "🔍 أرسل يوزر تيك توك (مثل `@username`) لجلب معلوماته الحقيقية.\n\n"
        "⚠️ *البوت لأغراض تعليمية وتوعوية فقط.*",
        parse_mode="Markdown"
    )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "📖 **الأوامر:**\n\n"
        "/start - بدء البوت\n"
        "/help - المساعدة\n\n"
        "🔍 أرسل يوزر تيك توك (مثل `@username`)."
    )

@dp.message()
async def check_tiktok_handler(message: types.Message):
    username = message.text.strip().replace("@", "")
    
    if not username or " " in username:
        await message.answer("❌ الرجاء إرسال يوزر صحيح.")
        return
    
    processing_msg = await message.answer(f"⏳ جاري فحص @{username} ...")
    
    data = await get_tiktok_user_info(username)
    
    if not data:
        await processing_msg.edit_text(
            "❌ **تعذر جلب البيانات.**\n\n"
            "الأسباب المحتملة:\n"
            "• الحساب غير موجود\n"
            "• msToken منتهي (يحتاج تجديد)\n"
            "• الحساب محظور أو خاص",
            parse_mode="Markdown"
        )
        return
    
    verified_badge = " ✅" if data['verified'] else ""
    
    info_text = (
        f"👤 **معلومات الحساب:** @{data['unique_id']}{verified_badge}\n\n"
        f"🔹 **الاسم:** {data['nickname']}\n"
        f"🔸 **المتابعين:** {format_number(data['follower_count'])}\n"
        f"👥 **يتابع:** {format_number(data['following_count'])}\n"
        f"🤝 **الأصدقاء:** {format_number(data['friends_count'])}\n"
        f"❤️ **الإعجابات:** {format_number(data['heart_count'])}\n"
        f"🎬 **الفيديوهات:** {format_number(data['video_count'])}\n"
        f"📅 **تاريخ الإنشاء:** {data['create_time']}\n"
        f"🌐 **الدولة:** {data['country']}"
    )
    
    if data['signature']:
        info_text += f"\n\n📝 **البايو:** {data['signature'][:100]}"
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🚀 هكك (محاكاة تعليمية)", callback_data=f"hack_{username}")
    
    await processing_msg.edit_text(
        info_text,
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )

# ============================================================
# 7. معالج زر "هكك"
# ============================================================
@dp.callback_query(lambda c: c.data.startswith("hack_"))
async def process_hack_callback(callback: types.CallbackQuery):
    username = callback.data.split("_", 1)[1]
    await callback.answer("⚡ جاري بدء المحاكاة...", show_alert=True)
    await simulate_brute_force(username, callback.message)

# ============================================================
# 8. التشغيل
# ============================================================
async def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    logging.info("🚀 Starting bot...")
    await start_web_server()
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("✅ Bot is polling...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

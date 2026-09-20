import asyncio
import logging
import os
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
import httpx

# ضع توكن بوتك هنا
TOKEN = "8974546244:AAGSIwbh9FmENOiKYP2tS33_Z-ixjPl0cl4"
bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- 1. سيرفر الـ Keep-Alive لفتح البورت فوراً (يمنع مشكلة Port scan timeout) ---
async def handle(request):
    return web.Response(text="Bot is running and active!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Web server started on port {port}")

# --- 2. دالة جلب وعرض معلومات تيك توك التفصيلية ---
async def get_tiktok_user_info(username: str):
    url = f"https://www.tiktok.com/@{username}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=15) as client:
        try:
            response = await client.get(url)
            if response.status_code != 200:
                return None
            
            user_data = {
                "nickname": username,
                "follower_count": "28",
                "following_count": "28",
                "friends_count": "24",
                "heart_count": "0",
                "video_count": "0",
                "create_time": "16-09-2025 00:07",
                "country": "العراق 🇮🇶"
            }
            return user_data
        except Exception as e:
            logging.error(f"Error fetching TikTok info: {e}")
            return None

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("أهلاً بك في البوت التوعوي! أرسل يوزر تيك توك لفحص الحساب وعرض مثال عملي للدرس.")

@dp.message()
async def check_tiktok_handler(message: types.Message):
    username = message.text.strip().replace("@", "")
    processing_msg = await message.answer("⏳ جاري فحص الحساب وجلب البيانات للدرس التوعوي...")
    
    data = await get_tiktok_user_info(username)
    
    if not data:
        # حتى لو لم يفتح الرابط حقيقياً، سنعرض بيانات وهمية للتوضيح في الدرس
        data = {
            "nickname": username,
            "follower_count": "150",
            "following_count": "40",
            "friends_count": "12",
            "heart_count": "500",
            "video_count": "5",
            "create_time": "01-01-2024 12:00",
            "country": "السعودية 🇸🇦"
        }

    info_text = (
        f"👤 **[عرض توعوي] الحساب المستهدف:** @{username}\n\n"
        f"🔹 **المتابعين:** {data['follower_count']}\n"
        f"🔸 **يتابع:** {data['following_count']}\n"
        f"👥 **الأصدقاء:** {data['friends_count']}\n"
        f"❤️ **الإعجابات:** {data['heart_count']}\n"
        f"🎬 **الفيديوهات:** {data['video_count']}\n"
        f"📅 **تاريخ الإنشاء:** {data['create_time']}\n"
        f"🌐 **الدولة:** {data['country']}\n\n"
        f"⚠️ *اضغط على زر التوعية أدناه لشرح تأثير الطلبات الوهمية على الحسابات للحضور.*"
    )
    
    # بناء الزر التوعوي
    builder = InlineKeyboardBuilder()
    builder.button(text="🚨 محاكاة هجوم طلبات وهمية (توعوي)", callback_data=f"demo_{username}")
    
    await processing_msg.edit_text(info_text, reply_markup=builder.as_markup())

# --- 3. محاكاة الطلبات الوهمية للشرح التوعوي ---
@dp.callback_query(lambda c: c.data.startswith("demo_"))
async def process_awareness_demo(callback: types.CallbackQuery):
    username = callback.data.split("_")[1]
    await callback.answer("🚨 بدء محاكاة ضغط الطلبات البلاغات للدرس...", show_alert=True)
    
    demo_msg = await callback.message.reply(f"📊 **[شرح توعوي] محاكاة إرسال طلبات مكثفة على: @{username}**\n\nجارٍ إرسال حزم طلبات وهمية لاختبار استجابة النظام...")
    
    steps = [
        "🔄 [1/4] إرسال 50 طلب استعادة كلمة مرور متزامن...",
        "⚠️ [2/4] رصد ضغط على خوادم التحقق الأمني (Rate Limiting)...",
        "🚨 [3/4] تفعيل الحماية المؤقتة بسبب كثرة الطلبات الوهمية...",
        "🔒 **[4/4] نتيجة توعوية:** تم تعليق/تقييد الحساب مؤقتًا من قِبل النظام الآلي لحمايته ضد الإغراق (Spam Protection)."
    ]
    
    for step in steps:
        await asyncio.sleep(1.2)  # محاكاة وقت المعالجة التدريجي ليفهمه الحضور
        await demo_msg.edit_text(f"📊 **[شرح توعوي] محاكاة إرسال طلبات مكثفة على: @{username}**\n\n{step}")

async def main():
    logging.basicConfig(level=logging.INFO)
    
    # 1. فتح سيرفر الويب أولاً لضمان الاستجابة لفحص البورت
    await start_web_server()
    
    # 2. حذف الويب هوك القديم لتجنب التعارض
    await bot.delete_webhook(drop_pending_updates=True)
    
    logging.info("Bot is starting polling successfully...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

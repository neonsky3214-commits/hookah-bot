"""
Запускает и Mini App (HTML на порту 8080) и бота одновременно.
"""
import asyncio
import os
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
import json
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "0"))
WEBAPP_URL = os.environ.get("WEBAPP_URL", "")
PORT = int(os.environ.get("PORT", 8080))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


def format_booking(data: dict) -> str:
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    return (
        f"🔔 <b>Новое бронирование!</b>\n\n"
        f"🌿 <b>Зона:</b> {data.get('zone','—')}\n"
        f"🪑 <b>Столик:</b> {data.get('table','—')}\n"
        f"📅 <b>Дата:</b> {data.get('date','—')}\n"
        f"🕐 <b>Время:</b> {data.get('time','—')}\n"
        f"👥 <b>Гостей:</b> {data.get('guests','—')}\n"
        f"👤 <b>Имя:</b> {data.get('name','—')}\n"
        f"📞 <b>Телефон:</b> {data.get('phone','—')}\n\n"
        f"🕓 <b>Создано:</b> {now}"
    )


@dp.message(Command("start"))
async def cmd_start(message: Message):
    url = WEBAPP_URL or "https://example.com"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="📅 Забронировать столик",
            web_app=WebAppInfo(url=url)
        )
    ]])
    await message.answer(
        "🌿 <b>Добро пожаловать в Livan Lounge!</b>\n\nНажмите кнопку ниже, чтобы забронировать столик.",
        parse_mode="HTML",
        reply_markup=keyboard
    )


@dp.message(F.web_app_data)
async def handle_webapp_data(message: Message):
    try:
        data = json.loads(message.web_app_data.data)
        text = format_booking(data)
        source = f"@{message.from_user.username or message.from_user.full_name}"
        text += f"\n📌 <b>Источник:</b> {source}"
        await bot.send_message(ADMIN_CHAT_ID, text, parse_mode="HTML")
        await message.answer("✅ <b>Бронь принята! Ждём вас!</b>", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error: {e}")
        await message.answer("⚠️ Что-то пошло не так. Попробуйте ещё раз.")


# ─── Веб-сервер для Mini App ──────────────────────────────────────────────────

async def serve_index(request):
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "index.html")
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    return web.Response(text=content, content_type="text/html")


async def run_web():
    app = web.Application()
    app.router.add_get("/", serve_index)
    app.router.add_get("/index.html", serve_index)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Web server started on port {PORT}")


async def main():
    await run_web()
    logger.info("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

"""
LIWAN Bot — Mini App + Gallery + Menu + Bookings + PostgreSQL
"""
import asyncio
import json
import logging
import os
from datetime import datetime
import asyncpg
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN    = os.environ.get("BOT_TOKEN", "")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "0"))
WEBAPP_URL   = os.environ.get("WEBAPP_URL", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
PORT         = int(os.environ.get("PORT", 8080))
ADMIN_IDS    = [int(x) for x in os.environ.get("ADMIN_IDS", "0").split(",") if x.strip().isdigit()]

bot     = Bot(token=BOT_TOKEN)
dp      = Dispatcher(storage=MemoryStorage())
db_pool = None


# ─── FSM ──────────────────────────────────────────────────────────────────────

class UploadGallery(StatesGroup):
    waiting = State()

class UploadMenu(StatesGroup):
    waiting = State()


# ─── БД ───────────────────────────────────────────────────────────────────────

async def init_db(pool):
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id         SERIAL PRIMARY KEY,
                zone       TEXT NOT NULL,
                table_num  INTEGER NOT NULL,
                book_date  TEXT NOT NULL,
                book_time  TEXT NOT NULL,
                guests     INTEGER NOT NULL,
                name       TEXT NOT NULL,
                phone      TEXT NOT NULL,
                tg_user    TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                status     TEXT DEFAULT 'active'
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS gallery (
                id         SERIAL PRIMARY KEY,
                file_id    TEXT NOT NULL,
                caption    TEXT,
                added_at   TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS menu_photos (
                id         SERIAL PRIMARY KEY,
                file_id    TEXT NOT NULL,
                page_num   INTEGER DEFAULT 1,
                added_at   TIMESTAMP DEFAULT NOW()
            )
        """)
    logger.info("DB initialized")


async def save_booking(pool, data: dict, tg_user: str):
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO bookings (zone,table_num,book_date,book_time,guests,name,phone,tg_user)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING id
        """, data.get("zone"), int(data.get("table",0)), data.get("date"),
            data.get("time"), int(data.get("guests",1)),
            data.get("name"), data.get("phone"), tg_user)
        return row["id"]


async def get_taken_tables(pool, book_date: str, zone: str):
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT table_num FROM bookings
            WHERE book_date=$1 AND zone=$2 AND status='active'
        """, book_date, zone)
        return [r["table_num"] for r in rows]


async def get_gallery(pool):
    async with pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM gallery ORDER BY added_at DESC")


async def get_menu(pool):
    async with pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM menu_photos ORDER BY page_num, added_at")


# ─── Команды бота ─────────────────────────────────────────────────────────────

def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📅 Забронировать"), KeyboardButton(text="🖼 Галерея")],
            [KeyboardButton(text="📋 Меню"),          KeyboardButton(text="📞 Контакты")]
        ],
        resize_keyboard=True
    )

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🌿 <b>Добро пожаловать в LIWAN!</b>\n\nВыберите действие:",
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )


# ─── Бронирование ─────────────────────────────────────────────────────────────

@dp.message(F.text == "📅 Забронировать")
async def btn_book(message: Message):
    url = WEBAPP_URL or "https://example.com"
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Открыть форму бронирования", web_app=WebAppInfo(url=url))
    ]])
    await message.answer("Нажмите кнопку ниже:", reply_markup=kb)


@dp.message(F.web_app_data)
async def handle_webapp(message: Message):
    try:
        data = json.loads(message.web_app_data.data)
        tg_user = f"@{message.from_user.username or message.from_user.full_name}"
        booking_id = await save_booking(db_pool, data, tg_user)
        now = datetime.now().strftime("%d.%m.%Y %H:%M")
        text = (
            f"🔔 <b>Новая бронь #{booking_id}</b>\n\n"
            f"🌿 {data.get('zone','—')} · Столик №{data.get('table','—')}\n"
            f"📅 {data.get('date','—')} · {data.get('time','—')}\n"
            f"👥 {data.get('guests','—')} гост.\n"
            f"👤 {data.get('name','—')} · {data.get('phone','—')}\n"
            f"📌 {tg_user} · {now}"
        )
        await bot.send_message(ADMIN_CHAT_ID, text, parse_mode="HTML")
        await message.answer(f"✅ <b>Бронь #{booking_id} принята!</b>", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Webapp error: {e}")
        await message.answer("⚠️ Ошибка. Попробуйте ещё раз.")


# ─── Галерея ──────────────────────────────────────────────────────────────────

@dp.message(F.text == "🖼 Галерея")
async def btn_gallery(message: Message):
    rows = await get_gallery(db_pool)
    if not rows:
        await message.answer("📭 Галерея пока пуста.")
        return
    media_ids = [r["file_id"] for r in rows]
    # Отправляем альбомами по 10
    from aiogram.types import InputMediaPhoto
    for i in range(0, len(media_ids), 10):
        chunk = media_ids[i:i+10]
        media = [InputMediaPhoto(media=fid) for fid in chunk]
        await bot.send_media_group(message.chat.id, media)


@dp.message(F.text == "📋 Меню")
async def btn_menu(message: Message):
    rows = await get_menu(db_pool)
    if not rows:
        await message.answer("📭 Меню пока не загружено.")
        return
    from aiogram.types import InputMediaPhoto
    media_ids = [r["file_id"] for r in rows]
    for i in range(0, len(media_ids), 10):
        chunk = media_ids[i:i+10]
        media = [InputMediaPhoto(media=fid) for fid in chunk]
        await bot.send_media_group(message.chat.id, media)


@dp.message(F.text == "📞 Контакты")
async def btn_contacts(message: Message):
    await message.answer(
        "📍 <b>LIWAN</b>\n\n"
        "🕐 Ежедневно: 15:00 — 02:00\n"
        "📞 +7 (xxx) xxx-xx-xx\n"
        "📱 @liwan_hookah",
        parse_mode="HTML"
    )


# ─── Админ: загрузка фото ─────────────────────────────────────────────────────

@dp.message(Command("addphoto"))
async def cmd_addphoto(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return
    await state.set_state(UploadGallery.waiting)
    await message.answer(
        "📸 Отправьте фото для галереи.\n"
        "Можно несколько сразу. Когда закончите — /done",
        reply_markup=ReplyKeyboardRemove()
    )


@dp.message(UploadGallery.waiting, F.photo)
async def upload_gallery_photo(message: Message, state: FSMContext):
    file_id = message.photo[-1].file_id
    caption = message.caption or ""
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO gallery (file_id, caption) VALUES ($1,$2)",
            file_id, caption
        )
    await message.answer("✅ Фото добавлено в галерею.")


@dp.message(Command("addmenu"))
async def cmd_addmenu(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return
    await state.set_state(UploadMenu.waiting)
    await message.answer(
        "🍽 Отправьте фото страниц меню.\n"
        "Порядок важен — отправляйте по одному. Когда закончите — /done",
        reply_markup=ReplyKeyboardRemove()
    )


@dp.message(UploadMenu.waiting, F.photo)
async def upload_menu_photo(message: Message, state: FSMContext):
    file_id = message.photo[-1].file_id
    async with db_pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM menu_photos")
        await conn.execute(
            "INSERT INTO menu_photos (file_id, page_num) VALUES ($1,$2)",
            file_id, int(count) + 1
        )
    count_new = await db_pool.fetchval("SELECT COUNT(*) FROM menu_photos")
    await message.answer(f"✅ Страница {count_new} меню добавлена.")


@dp.message(Command("done"))
async def cmd_done(message: Message, state: FSMContext):
    current = await state.get_state()
    await state.clear()
    if current == UploadGallery.waiting:
        await message.answer("✅ Галерея сохранена.", reply_markup=main_keyboard())
    elif current == UploadMenu.waiting:
        await message.answer("✅ Меню сохранено.", reply_markup=main_keyboard())
    else:
        await message.answer("Готово.", reply_markup=main_keyboard())


@dp.message(Command("cleargallery"))
async def cmd_cleargallery(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM gallery")
    await message.answer("🗑 Галерея очищена.")


@dp.message(Command("clearmenu"))
async def cmd_clearmenu(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM menu_photos")
    await message.answer("🗑 Меню очищено.")


# ─── Брони ────────────────────────────────────────────────────────────────────

@dp.message(Command("bookings"))
async def cmd_bookings(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT * FROM bookings WHERE status='active'
            ORDER BY book_date, book_time LIMIT 20
        """)
    if not rows:
        await message.answer("📭 Активных броней нет.")
        return
    text = "📋 <b>Активные брони:</b>\n\n"
    for r in rows:
        text += (
            f"<b>#{r['id']}</b> · {r['book_date']} {r['book_time']}\n"
            f"🌿 {r['zone']} · №{r['table_num']} · {r['guests']} чел.\n"
            f"👤 {r['name']} · {r['phone']}\n"
            f"──────────\n"
        )
    await message.answer(text, parse_mode="HTML")


@dp.message(Command("cancelbook"))
async def cmd_cancelbook(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Укажи номер: <code>/cancelbook 42</code>", parse_mode="HTML")
        return
    bid = int(parts[1])
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM bookings WHERE id=$1", bid)
        if not row:
            await message.answer(f"Бронь #{bid} не найдена.")
            return
        await conn.execute("UPDATE bookings SET status='cancelled' WHERE id=$1", bid)
    await message.answer(f"✅ Бронь #{bid} отменена.")


# ─── API для Mini App ─────────────────────────────────────────────────────────

async def api_taken(request):
    book_date = request.rel_url.query.get("date", "")
    zone = request.rel_url.query.get("zone", "")
    if not book_date or not zone or not db_pool:
        return web.json_response({"taken": []})
    taken = await get_taken_tables(db_pool, book_date, zone)
    return web.json_response({"taken": taken})


async def serve_index(request):
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "index.html"), "r", encoding="utf-8") as f:
        return web.Response(text=f.read(), content_type="text/html")


# ─── Запуск ───────────────────────────────────────────────────────────────────

async def main():
    global db_pool
    if DATABASE_URL:
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        await init_db(db_pool)
        logger.info("PostgreSQL connected")
    else:
        logger.warning("No DATABASE_URL")

    app = web.Application()
    app.router.add_get("/", serve_index)
    app.router.add_get("/index.html", serve_index)
    app.router.add_get("/api/taken", api_taken)

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    logger.info(f"Web server on :{PORT}")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

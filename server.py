"""
LIWAN Bot — полная версия
Фичи: брони, события, меню, галерея, ароматы, лояльность,
       напоминания, отмена брони гостем, оценки, рассылка,
       стоп-лист, статистика, выгрузка пользователей
"""
import asyncio
import json
import logging
import os
import io
from datetime import datetime, timedelta
import asyncpg
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    CallbackQuery, BufferedInputFile
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN     = os.environ.get("BOT_TOKEN", "")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "0"))
WEBAPP_URL    = os.environ.get("WEBAPP_URL", "")
DATABASE_URL  = os.environ.get("DATABASE_URL", "")
PORT          = int(os.environ.get("PORT", 8080))
ADMIN_IDS     = [int(x) for x in os.environ.get("ADMIN_IDS", "0").split(",") if x.strip().isdigit()]

bot     = Bot(token=BOT_TOKEN)
dp      = Dispatcher(storage=MemoryStorage())
db_pool = None


# ─── FSM ──────────────────────────────────────────────────────────────────────

class UploadGallery(StatesGroup):
    waiting = State()

class UploadMenu(StatesGroup):
    waiting = State()

class AddEvent(StatesGroup):
    title       = State()
    date_str    = State()
    time_str    = State()
    description = State()
    entry       = State()
    photo       = State()

class BlockSlot(StatesGroup):
    waiting = State()

class Broadcast(StatesGroup):
    waiting = State()


# ─── БД ───────────────────────────────────────────────────────────────────────

async def init_db(pool):
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id           SERIAL PRIMARY KEY,
                zone         TEXT NOT NULL,
                table_num    INTEGER NOT NULL,
                book_date    TEXT NOT NULL,
                book_time    TEXT NOT NULL,
                guests       INTEGER NOT NULL,
                name         TEXT NOT NULL,
                phone        TEXT NOT NULL,
                tg_user      TEXT,
                tg_user_id   BIGINT,
                reminder_sent BOOLEAN DEFAULT FALSE,
                rating       INTEGER,
                review       TEXT,
                created_at   TIMESTAMP DEFAULT NOW(),
                status       TEXT DEFAULT 'active'
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS gallery (
                id       SERIAL PRIMARY KEY,
                file_id  TEXT NOT NULL,
                caption  TEXT,
                added_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS menu_photos (
                id       SERIAL PRIMARY KEY,
                file_id  TEXT NOT NULL,
                page_num INTEGER DEFAULT 1,
                added_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id            SERIAL PRIMARY KEY,
                title         TEXT NOT NULL,
                date_str      TEXT NOT NULL,
                time_str      TEXT NOT NULL,
                description   TEXT,
                entry_info    TEXT,
                photo_file_id TEXT,
                active        BOOLEAN DEFAULT TRUE,
                created_at    TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_flavors (
                id          SERIAL PRIMARY KEY,
                tg_user_id  BIGINT NOT NULL UNIQUE,
                tg_username TEXT,
                flavors     TEXT NOT NULL DEFAULT '',
                updated_at  TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id            SERIAL PRIMARY KEY,
                tg_user_id    BIGINT UNIQUE,
                tg_username   TEXT,
                name          TEXT NOT NULL,
                phone         TEXT NOT NULL,
                email         TEXT,
                age_confirmed BOOLEAN DEFAULT TRUE,
                registered_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS blocked_slots (
                id         SERIAL PRIMARY KEY,
                block_date TEXT NOT NULL,
                time_from  TEXT NOT NULL,
                time_to    TEXT NOT NULL,
                reason     TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        # Migrations
        for sql in [
            'ALTER TABLE bookings ADD COLUMN IF NOT EXISTS tg_user_id BIGINT',
            'ALTER TABLE bookings ADD COLUMN IF NOT EXISTS reminder_sent BOOLEAN DEFAULT FALSE',
            'ALTER TABLE bookings ADD COLUMN IF NOT EXISTS rating INTEGER',
            'ALTER TABLE bookings ADD COLUMN IF NOT EXISTS review TEXT',
            'ALTER TABLE events ADD COLUMN IF NOT EXISTS photo_file_id TEXT',
        ]:
            try:
                await conn.execute(sql)
            except:
                pass
    logger.info("DB initialized")


# ─── Хелперы ──────────────────────────────────────────────────────────────────

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📅 Забронировать"), KeyboardButton(text="🖼 Галерея")],
            [KeyboardButton(text="📋 Меню"),          KeyboardButton(text="📞 Контакты")]
        ],
        resize_keyboard=True
    )

def get_blocked_slots(book_time: str, duration_minutes: int = 120, step: int = 30) -> list:
    try:
        dt = datetime.strptime(book_time, "%H:%M")
    except:
        return [book_time]
    slots = []
    for i in range(0, duration_minutes, step):
        slots.append((dt + timedelta(minutes=i)).strftime("%H:%M"))
    return slots

def time_to_minutes(t: str) -> int:
    try:
        h, m = map(int, t.split(":"))
        return h * 60 + m
    except:
        return 0


# ─── /start ───────────────────────────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    url = WEBAPP_URL or "https://example.com"
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🌿 Открыть LIWAN", web_app=WebAppInfo(url=url))]],
        resize_keyboard=True
    )
    await message.answer(
        "🌿 <b>Добро пожаловать в LIWAN!</b>\n\nНажмите кнопку ниже чтобы открыть приложение.",
        parse_mode="HTML", reply_markup=kb
    )


# ─── Бронирование (web_app_data) ──────────────────────────────────────────────

@dp.message(F.web_app_data)
async def handle_webapp(message: Message):
    try:
        data = json.loads(message.web_app_data.data)
        tg_user = f"@{message.from_user.username or message.from_user.full_name}"
        tg_user_id = message.from_user.id
        flavors_text = ""
        if db_pool:
            fl = await db_pool.fetchrow("SELECT flavors FROM user_flavors WHERE tg_user_id=$1", tg_user_id)
            if fl and fl["flavors"]:
                flavors_text = fl["flavors"]
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO bookings (zone,table_num,book_date,book_time,guests,name,phone,tg_user,tg_user_id)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id
            """,
                str(data.get("zone") or "—"),
                int(data.get("table") or 0),
                str(data.get("date") or "—"),
                str(data.get("time") or "—"),
                int(data.get("guests") or 1),
                str(data.get("name") or "—"),
                str(data.get("phone") or "—"),
                tg_user, tg_user_id
            )
            booking_id = row["id"]
        now = datetime.now().strftime("%d.%m.%Y %H:%M")
        flavors_line = f"\n🌸 <b>Ароматы:</b> {flavors_text}" if flavors_text else ""
        text = (
            f"🔔 <b>Новая бронь #{booking_id}</b>\n\n"
            f"🌿 {data.get('zone','—')} · Столик №{data.get('table','—')}\n"
            f"📅 {data.get('date','—')} · {data.get('time','—')}\n"
            f"👥 {data.get('guests','—')} гост.\n"
            f"👤 {data.get('name','—')} · {data.get('phone','—')}\n"
            f"📌 {tg_user} · {now}{flavors_line}"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❌ Отменить бронь", callback_data=f"cancel_{booking_id}")
        ]])
        await bot.send_message(ADMIN_CHAT_ID, text, parse_mode="HTML", reply_markup=kb)
        await message.answer(f"✅ <b>Бронь #{booking_id} принята!</b>", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Webapp error: {e}")
        await message.answer("⚠️ Ошибка. Попробуйте ещё раз.")


# ─── Callback: отмена брони ───────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("cancel_"))
async def cb_cancel(callback: CallbackQuery):
    bid = int(callback.data.split("_")[1])
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM bookings WHERE id=$1", bid)
        if not row:
            await callback.answer("Бронь не найдена")
            return
        await conn.execute("UPDATE bookings SET status='cancelled' WHERE id=$1", bid)
    await callback.message.edit_text(
        callback.message.text + "\n\n❌ <b>Отменено</b>",
        parse_mode="HTML"
    )
    # Notify guest if tg_user_id known
    if row.get("tg_user_id"):
        try:
            await bot.send_message(
                row["tg_user_id"],
                f"❌ <b>Ваша бронь #{bid} отменена.</b>\n\nЕсли это ошибка — свяжитесь с нами @loungeliwan",
                parse_mode="HTML"
            )
        except:
            pass
    await callback.answer("Бронь отменена")


@dp.callback_query(F.data.startswith("rate_"))
async def cb_rate(callback: CallbackQuery):
    parts = callback.data.split("_")
    bid = int(parts[1])
    rating = int(parts[2])
    stars = "⭐" * rating
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE bookings SET rating=$1 WHERE id=$2", rating, bid)
    await callback.message.edit_text(
        f"Спасибо за оценку! {stars}\n\nЕсли хотите оставить отзыв — просто напишите нам @loungeliwan",
        parse_mode="HTML"
    )
    # Notify admin
    row = await db_pool.fetchrow("SELECT * FROM bookings WHERE id=$1", bid)
    if row:
        await bot.send_message(
            ADMIN_CHAT_ID,
            f"⭐ <b>Оценка визита #{bid}</b>\n{stars} ({rating}/5)\n👤 {row['name']}",
            parse_mode="HTML"
        )
    await callback.answer()


# ─── Галерея / Меню ───────────────────────────────────────────────────────────

@dp.message(F.text == "🖼 Галерея")
async def btn_gallery(message: Message):
    rows = await db_pool.fetch("SELECT file_id FROM gallery ORDER BY added_at DESC")
    if not rows:
        await message.answer("📭 Галерея пока пуста.")
        return
    from aiogram.types import InputMediaPhoto
    ids = [r["file_id"] for r in rows]
    for i in range(0, len(ids), 10):
        await bot.send_media_group(message.chat.id, [InputMediaPhoto(media=fid) for fid in ids[i:i+10]])

@dp.message(F.text == "📋 Меню")
async def btn_menu(message: Message):
    rows = await db_pool.fetch("SELECT file_id FROM menu_photos ORDER BY page_num, added_at")
    if not rows:
        await message.answer("📭 Меню пока не загружено.")
        return
    from aiogram.types import InputMediaPhoto
    ids = [r["file_id"] for r in rows]
    for i in range(0, len(ids), 10):
        await bot.send_media_group(message.chat.id, [InputMediaPhoto(media=fid) for fid in ids[i:i+10]])

@dp.message(F.text == "📞 Контакты")
async def btn_contacts(message: Message):
    await message.answer(
        "📍 <b>LIWAN</b>\n\nАвиаконструктора Миля 3А\nПереход напротив «Сыроварни»\n\n🕐 14:00 — 02:00\n📱 @loungeliwan",
        parse_mode="HTML"
    )


# ─── Загрузка фото ────────────────────────────────────────────────────────────

@dp.message(Command("addphoto"))
async def cmd_addphoto(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await state.set_state(UploadGallery.waiting)
    await message.answer("📸 Отправьте фото для галереи. Когда закончите — /done", reply_markup=ReplyKeyboardRemove())

@dp.message(UploadGallery.waiting, F.photo)
async def upload_gallery_photo(message: Message):
    fid = message.photo[-1].file_id
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO gallery (file_id, caption) VALUES ($1,$2)", fid, message.caption or "")
    await message.answer("✅ Фото добавлено в галерею.")

@dp.message(Command("addmenu"))
async def cmd_addmenu(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await state.set_state(UploadMenu.waiting)
    await message.answer("🍽 Отправьте страницы меню по порядку. Когда закончите — /done", reply_markup=ReplyKeyboardRemove())

@dp.message(UploadMenu.waiting, F.photo)
async def upload_menu_photo(message: Message):
    fid = message.photo[-1].file_id
    async with db_pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM menu_photos")
        await conn.execute("INSERT INTO menu_photos (file_id, page_num) VALUES ($1,$2)", fid, int(count)+1)
    count2 = await db_pool.fetchval("SELECT COUNT(*) FROM menu_photos")
    await message.answer(f"✅ Страница {count2} меню добавлена.")

@dp.message(Command("done"))
async def cmd_done(message: Message, state: FSMContext):
    current = await state.get_state()
    await state.clear()
    if current == UploadGallery.waiting:
        await message.answer("✅ Галерея сохранена.", reply_markup=main_keyboard())
    elif current == UploadMenu.waiting:
        await message.answer("✅ Меню сохранено.", reply_markup=main_keyboard())
    elif current and "AddEvent" in str(current):
        await message.answer("❌ Добавление события отменено.", reply_markup=main_keyboard())
    elif current and "BlockSlot" in str(current):
        await message.answer("❌ Отменено.", reply_markup=main_keyboard())
    elif current and "Broadcast" in str(current):
        await message.answer("❌ Рассылка отменена.", reply_markup=main_keyboard())
    else:
        await message.answer("Готово.", reply_markup=main_keyboard())


# ─── События ──────────────────────────────────────────────────────────────────

@dp.message(Command("addevent"))
async def cmd_addevent(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return
    await state.set_state(AddEvent.title)
    await message.answer("🎉 <b>Добавление события</b>\n\nВведите <b>название</b>:", parse_mode="HTML", reply_markup=ReplyKeyboardRemove())

@dp.message(AddEvent.title)
async def event_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text.strip())
    await state.set_state(AddEvent.date_str)
    await message.answer("Введите <b>дату</b> (например: <code>Суббота · 26 апреля</code>):", parse_mode="HTML")

@dp.message(AddEvent.date_str)
async def event_date(message: Message, state: FSMContext):
    await state.update_data(date_str=message.text.strip())
    await state.set_state(AddEvent.time_str)
    await message.answer("Введите <b>время</b> (например: <code>21:00</code>):", parse_mode="HTML")

@dp.message(AddEvent.time_str)
async def event_time(message: Message, state: FSMContext):
    await state.update_data(time_str=message.text.strip())
    await state.set_state(AddEvent.description)
    await message.answer("Введите <b>описание</b>:", parse_mode="HTML")

@dp.message(AddEvent.description)
async def event_desc(message: Message, state: FSMContext):
    await state.update_data(description=message.text.strip())
    await state.set_state(AddEvent.entry)
    await message.answer("Информация о <b>входе</b> (например: <code>Вход свободный</code>):", parse_mode="HTML")

@dp.message(AddEvent.entry)
async def event_entry(message: Message, state: FSMContext):
    await state.update_data(entry=message.text.strip())
    await state.set_state(AddEvent.photo)
    await message.answer("🖼 Отправьте <b>фото афиши</b>.\n\nЕсли фото нет — напишите <code>нет</code>", parse_mode="HTML")

@dp.message(AddEvent.photo)
async def event_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    photo_file_id = message.photo[-1].file_id if message.photo else None
    await state.clear()
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO events (title, date_str, time_str, description, entry_info, photo_file_id)
            VALUES ($1,$2,$3,$4,$5,$6) RETURNING id
        """, data["title"], data["date_str"], data["time_str"],
            data["description"], data["entry"], photo_file_id)
    await message.answer(
        f"✅ Событие <b>#{row['id']} «{data['title']}»</b> добавлено!",
        parse_mode="HTML", reply_markup=main_keyboard()
    )

@dp.message(Command("events"))
async def cmd_events(message: Message):
    if not is_admin(message.from_user.id): return
    rows = await db_pool.fetch("SELECT * FROM events WHERE active=TRUE ORDER BY created_at DESC LIMIT 10")
    if not rows:
        await message.answer("📭 Событий нет.")
        return
    text = "🎉 <b>Активные события:</b>\n\n"
    for r in rows:
        text += f"<b>#{r['id']}</b> · {r['date_str']} · {r['time_str']}\n{r['title']}\n──────\n"
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("delevent"))
async def cmd_delevent(message: Message):
    if not is_admin(message.from_user.id): return
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Укажи номер: <code>/delevent 5</code>", parse_mode="HTML")
        return
    eid = int(parts[1])
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE events SET active=FALSE WHERE id=$1", eid)
    await message.answer(f"✅ Событие #{eid} удалено.")


# ─── Брони ────────────────────────────────────────────────────────────────────

@dp.message(Command("bookings"))
async def cmd_bookings(message: Message):
    if not is_admin(message.from_user.id): return
    rows = await db_pool.fetch("SELECT * FROM bookings WHERE status='active' ORDER BY book_date,book_time LIMIT 20")
    if not rows:
        await message.answer("📭 Активных броней нет.")
        return
    text = "📋 <b>Активные брони:</b>\n\n"
    for r in rows:
        text += f"<b>#{r['id']}</b> · {r['book_date']} {r['book_time']}\n🌿 {r['zone']} · №{r['table_num']} · {r['guests']} чел.\n👤 {r['name']} · {r['phone']}\n──────\n"
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("cancelbook"))
async def cmd_cancelbook(message: Message):
    if not is_admin(message.from_user.id): return
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
    # Notify guest
    if row.get("tg_user_id"):
        try:
            await bot.send_message(
                row["tg_user_id"],
                f"❌ <b>Ваша бронь #{bid} отменена администратором.</b>\n\nПо вопросам: @loungeliwan",
                parse_mode="HTML"
            )
        except:
            pass
    await message.answer(f"✅ Бронь #{bid} отменена.")


# ─── Статистика ───────────────────────────────────────────────────────────────

@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if not is_admin(message.from_user.id): return
    async with db_pool.acquire() as conn:
        total = await conn.fetchval("SELECT COUNT(*) FROM bookings WHERE status='active'")
        week = await conn.fetchval("SELECT COUNT(*) FROM bookings WHERE created_at > NOW() - INTERVAL '7 days' AND status='active'")
        today = await conn.fetchval("SELECT COUNT(*) FROM bookings WHERE book_date=$1 AND status='active'", datetime.now().strftime("%d %b").lower())
        avg_guests = await conn.fetchval("SELECT ROUND(AVG(guests),1) FROM bookings WHERE status='active'")
        top_table = await conn.fetchrow("SELECT table_num, COUNT(*) as cnt FROM bookings WHERE status='active' GROUP BY table_num ORDER BY cnt DESC LIMIT 1")
        top_time = await conn.fetchrow("SELECT book_time, COUNT(*) as cnt FROM bookings WHERE status='active' GROUP BY book_time ORDER BY cnt DESC LIMIT 1")
        users_count = await conn.fetchval("SELECT COUNT(*) FROM users")
        avg_rating = await conn.fetchval("SELECT ROUND(AVG(rating),1) FROM bookings WHERE rating IS NOT NULL")
    text = (
        f"📊 <b>Статистика LIWAN</b>\n\n"
        f"📋 Всего броней: <b>{total}</b>\n"
        f"📅 За 7 дней: <b>{week}</b>\n"
        f"👥 Среднее гостей: <b>{avg_guests or '—'}</b>\n"
        f"🏆 Топ столик: <b>№{top_table['table_num'] if top_table else '—'}</b>\n"
        f"⏰ Топ время: <b>{top_time['book_time'] if top_time else '—'}</b>\n"
        f"👤 Пользователей: <b>{users_count}</b>\n"
        f"⭐ Средняя оценка: <b>{avg_rating or '—'}/5</b>"
    )
    await message.answer(text, parse_mode="HTML")


# ─── Стоп-лист ────────────────────────────────────────────────────────────────

@dp.message(Command("block"))
async def cmd_block(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    parts = message.text.split(maxsplit=3)
    # /block 26 апр 19:00 22:00
    if len(parts) >= 4:
        date_str = parts[1] + " " + parts[2]
        times = parts[3].split()
        if len(times) >= 2:
            async with db_pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO blocked_slots (block_date, time_from, time_to, reason)
                    VALUES ($1, $2, $3, $4)
                """, date_str, times[0], times[1], "Закрыто")
            await message.answer(f"✅ Слоты {date_str} с {times[0]} по {times[1]} заблокированы.")
            return
    await message.answer(
        "Формат: <code>/block 26 апр 19:00 22:00</code>\n\nЗаблокирует все слоты в этом диапазоне.",
        parse_mode="HTML"
    )

@dp.message(Command("unblock"))
async def cmd_unblock(message: Message):
    if not is_admin(message.from_user.id): return
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM blocked_slots ORDER BY created_at DESC LIMIT 10")
    if not rows:
        await message.answer("Стоп-лист пуст.")
        return
    text = "🚫 <b>Заблокированные слоты:</b>\n\n"
    for r in rows:
        text += f"<b>#{r['id']}</b> · {r['block_date']} · {r['time_from']}–{r['time_to']}\n"
    text += "\nУдалить: <code>/unblock_id [номер]</code>"
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("unblock_id"))
async def cmd_unblock_id(message: Message):
    if not is_admin(message.from_user.id): return
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Укажи номер: <code>/unblock_id 3</code>", parse_mode="HTML")
        return
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM blocked_slots WHERE id=$1", int(parts[1]))
    await message.answer(f"✅ Блокировка #{parts[1]} снята.")


# ─── Рассылка ─────────────────────────────────────────────────────────────────

@dp.message(Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await state.set_state(Broadcast.waiting)
    count = await db_pool.fetchval("SELECT COUNT(*) FROM users WHERE tg_user_id IS NOT NULL")
    await message.answer(
        f"📢 <b>Рассылка</b>\n\nПолучат: <b>{count}</b> пользователей\n\nНапишите текст сообщения (можно с эмодзи и форматированием).\n\nОтмена: /done",
        parse_mode="HTML", reply_markup=ReplyKeyboardRemove()
    )

@dp.message(Broadcast.waiting)
async def do_broadcast(message: Message, state: FSMContext):
    await state.clear()
    rows = await db_pool.fetch("SELECT tg_user_id FROM users WHERE tg_user_id IS NOT NULL")
    sent = 0
    failed = 0
    text = message.text or message.caption or ""
    for r in rows:
        try:
            await bot.send_message(r["tg_user_id"], f"📢 <b>LIWAN</b>\n\n{text}", parse_mode="HTML")
            sent += 1
            await asyncio.sleep(0.05)
        except:
            failed += 1
    await message.answer(f"✅ Рассылка завершена\n📤 Отправлено: {sent}\n❌ Не доставлено: {failed}", reply_markup=main_keyboard())


# ─── Пользователи ─────────────────────────────────────────────────────────────

@dp.message(Command("users"))
async def cmd_users(message: Message):
    if not is_admin(message.from_user.id): return
    rows = await db_pool.fetch("SELECT name, phone, email, tg_username, registered_at FROM users ORDER BY registered_at DESC LIMIT 1000")
    if not rows:
        await message.answer("📭 Пользователей нет.")
        return
    csv_data = "Имя,Телефон,Email,Telegram,Дата\n"
    for r in rows:
        csv_data += f"{r['name']},{r['phone']},{r['email'] or '—'},@{r['tg_username'] or '—'},{r['registered_at'].strftime('%d.%m.%Y %H:%M')}\n"
    await message.answer_document(
        BufferedInputFile(csv_data.encode('utf-8-sig'), filename="liwan_users.csv"),
        caption=f"👥 Всего пользователей: {len(rows)}"
    )


# ─── Очистка ──────────────────────────────────────────────────────────────────

@dp.message(Command("cleargallery"))
async def cmd_cleargallery(message: Message):
    if not is_admin(message.from_user.id): return
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM gallery")
    await message.answer("🗑 Галерея очищена.")

@dp.message(Command("clearmenu"))
async def cmd_clearmenu(message: Message):
    if not is_admin(message.from_user.id): return
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM menu_photos")
    await message.answer("🗑 Меню очищено.")


# ─── Помощь ───────────────────────────────────────────────────────────────────

@dp.message(Command("help"))
async def cmd_help(message: Message):
    if is_admin(message.from_user.id):
        await message.answer(
            "📖 <b>Команды администратора:</b>\n\n"
            "<b>Брони:</b>\n"
            "/bookings — список активных броней\n"
            "/cancelbook [id] — отменить бронь\n\n"
            "<b>Статистика:</b>\n"
            "/stats — общая статистика\n"
            "/users — выгрузить базу пользователей (CSV)\n\n"
            "<b>Меню:</b>\n"
            "/addmenu — загрузить фото меню\n"
            "/clearmenu — очистить меню\n\n"
            "<b>Галерея:</b>\n"
            "/addphoto — загрузить фото\n"
            "/cleargallery — очистить галерею\n\n"
            "<b>События:</b>\n"
            "/addevent — добавить событие\n"
            "/events — список событий\n"
            "/delevent [id] — удалить событие\n\n"
            "<b>Стоп-лист:</b>\n"
            "/block [дата] [с] [до] — заблокировать слоты\n"
            "/unblock — список блокировок\n"
            "/unblock_id [id] — снять блокировку\n\n"
            "<b>Рассылка:</b>\n"
            "/broadcast — отправить сообщение всем гостям\n\n"
            "/done — завершить загрузку фото",
            parse_mode="HTML"
        )
    else:
        await message.answer("/start — открыть приложение")


# ─── Напоминания (фоновая задача) ─────────────────────────────────────────────

async def reminder_task():
    """Каждые 5 минут проверяем брони на ближайший час"""
    await asyncio.sleep(30)  # Подождать старт
    while True:
        try:
            if db_pool:
                now = datetime.now()
                target = now + timedelta(hours=1)
                target_time = target.strftime("%H:%M")
                today_date = now.strftime("%-d %b").lower()
                # Упрощённая проверка — ищем брони на сегодня на ближайшее время
                rows = await db_pool.fetch("""
                    SELECT * FROM bookings
                    WHERE status='active'
                    AND reminder_sent=FALSE
                    AND book_time=$1
                    AND tg_user_id IS NOT NULL
                """, target_time)
                for r in rows:
                    try:
                        await bot.send_message(
                            r["tg_user_id"],
                            f"⏰ <b>Напоминание о брони</b>\n\n"
                            f"Через час вас ждём в LIWAN!\n"
                            f"🌿 {r['zone']} · Столик №{r['table_num']}\n"
                            f"🕐 {r['book_date']} · {r['book_time']}\n\n"
                            f"Ждём вас! 🙌",
                            parse_mode="HTML"
                        )
                        async with db_pool.acquire() as conn:
                            await conn.execute("UPDATE bookings SET reminder_sent=TRUE WHERE id=$1", r["id"])
                    except:
                        pass
        except Exception as e:
            logger.error(f"Reminder task error: {e}")
        await asyncio.sleep(300)  # Каждые 5 минут


async def rating_task():
    """Через 2 часа после брони просим оценку"""
    await asyncio.sleep(60)
    while True:
        try:
            if db_pool:
                threshold = datetime.now() - timedelta(hours=2)
                rows = await db_pool.fetch("""
                    SELECT * FROM bookings
                    WHERE status='active'
                    AND rating IS NULL
                    AND tg_user_id IS NOT NULL
                    AND created_at < $1
                    AND created_at > $1 - INTERVAL '30 minutes'
                """, threshold)
                for r in rows:
                    try:
                        kb = InlineKeyboardMarkup(inline_keyboard=[[
                            InlineKeyboardButton(text="⭐", callback_data=f"rate_{r['id']}_1"),
                            InlineKeyboardButton(text="⭐⭐", callback_data=f"rate_{r['id']}_2"),
                            InlineKeyboardButton(text="⭐⭐⭐", callback_data=f"rate_{r['id']}_3"),
                            InlineKeyboardButton(text="⭐⭐⭐⭐", callback_data=f"rate_{r['id']}_4"),
                            InlineKeyboardButton(text="⭐⭐⭐⭐⭐", callback_data=f"rate_{r['id']}_5"),
                        ]])
                        await bot.send_message(
                            r["tg_user_id"],
                            f"🌿 <b>Как прошёл ваш вечер в LIWAN?</b>\n\nОцените визит — это поможет нам стать лучше!",
                            parse_mode="HTML",
                            reply_markup=kb
                        )
                    except:
                        pass
        except Exception as e:
            logger.error(f"Rating task error: {e}")
        await asyncio.sleep(600)  # Каждые 10 минут


# ─── API ──────────────────────────────────────────────────────────────────────

async def api_taken(request):
    book_date = request.rel_url.query.get("date", "")
    zone = request.rel_url.query.get("zone", "")
    book_time = request.rel_url.query.get("time", None)
    if not book_date or not zone or not db_pool:
        return web.json_response({"taken": []})
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT table_num, book_time FROM bookings
            WHERE book_date=$1 AND zone=$2 AND status='active'
        """, book_date, zone)
        # Check blocked slots
        blocked_rows = await conn.fetch("""
            SELECT time_from, time_to FROM blocked_slots WHERE block_date=$1
        """, book_date)
    taken = []
    if book_time:
        for r in rows:
            if book_time in get_blocked_slots(r['book_time']):
                taken.append(r['table_num'])
        # If slot is blocked entirely — return all tables as taken
        req_min = time_to_minutes(book_time)
        for br in blocked_rows:
            from_min = time_to_minutes(br['time_from'])
            to_min = time_to_minutes(br['time_to'])
            if from_min <= req_min < to_min:
                return web.json_response({"taken": list(range(1, 13)), "blocked": True})
    else:
        taken = [r['table_num'] for r in rows]
    return web.json_response({"taken": taken})


async def api_booking_post(request):
    try:
        data = await request.json()
        tg_user_id = int(data.get("tg_user_id") or 0)
        tg_username = str(data.get("tg_username") or "")
        tg_user = f"@{tg_username}" if tg_username and tg_username != "guest" else "web"
        flavors_text = ""
        if db_pool and tg_user_id:
            fl = await db_pool.fetchrow("SELECT flavors FROM user_flavors WHERE tg_user_id=$1", tg_user_id)
            if fl and fl["flavors"]:
                flavors_text = fl["flavors"]
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO bookings (zone,table_num,book_date,book_time,guests,name,phone,tg_user,tg_user_id)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id
            """,
                str(data.get("zone") or "—"),
                int(data.get("table") or 0),
                str(data.get("date") or "—"),
                str(data.get("time") or "—"),
                int(data.get("guests") or 1),
                str(data.get("name") or "—"),
                str(data.get("phone") or "—"),
                tg_user,
                tg_user_id if tg_user_id else None
            )
            booking_id = row["id"]
        now = datetime.now().strftime("%d.%m.%Y %H:%M")
        flavors_line = f"\n🌸 <b>Ароматы:</b> {flavors_text}" if flavors_text else ""
        text = (
            f"🔔 <b>Новая бронь #{booking_id}</b>\n\n"
            f"🌿 {data.get('zone','—')} · Столик №{data.get('table','—')}\n"
            f"📅 {data.get('date','—')} · {data.get('time','—')}\n"
            f"👥 {data.get('guests','—')} гост.\n"
            f"👤 {data.get('name','—')} · {data.get('phone','—')}\n"
            f"📌 {tg_user} · {now}{flavors_line}"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel_{booking_id}")
        ]])
        await bot.send_message(ADMIN_CHAT_ID, text, parse_mode="HTML", reply_markup=kb)
        return web.json_response({"ok": True, "id": booking_id})
    except Exception as e:
        logger.error(f"API booking error: {e}")
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def api_cancel_booking(request):
    """Guest cancels own booking"""
    try:
        data = await request.json()
        bid = int(data.get("booking_id") or 0)
        tg_user_id = int(data.get("tg_user_id") or 0)
        if not bid or not tg_user_id or not db_pool:
            return web.json_response({"ok": False})
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM bookings WHERE id=$1 AND tg_user_id=$2", bid, tg_user_id)
            if not row:
                return web.json_response({"ok": False, "error": "Not found"})
            await conn.execute("UPDATE bookings SET status='cancelled' WHERE id=$1", bid)
        await bot.send_message(ADMIN_CHAT_ID, f"❌ <b>Гость отменил бронь #{bid}</b>\n👤 {row['name']}", parse_mode="HTML")
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})


async def api_events(request):
    if not db_pool:
        return web.json_response({"events": []})
    rows = await db_pool.fetch("SELECT * FROM events WHERE active=TRUE ORDER BY created_at DESC LIMIT 10")
    events = []
    for r in rows:
        events.append({
            "id": r["id"], "title": r["title"],
            "date_str": r["date_str"], "time_str": r["time_str"],
            "description": r["description"] or "",
            "entry_info": r["entry_info"] or "",
            "photo_url": f"/api/event-photo/{r['photo_file_id']}" if r.get("photo_file_id") else None
        })
    return web.json_response({"events": events})


async def _proxy_tg_file(file_id: str):
    import aiohttp as aiohttp_lib
    file = await bot.get_file(file_id)
    file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
    async with aiohttp_lib.ClientSession() as session:
        async with session.get(file_url) as resp:
            data = await resp.read()
            ct = resp.headers.get("Content-Type", "image/jpeg")
            return data, ct


async def api_event_photo(request):
    file_id = request.match_info.get("file_id", "")
    if not file_id: return web.Response(status=404)
    try:
        data, ct = await _proxy_tg_file(file_id)
        return web.Response(body=data, content_type=ct, headers={"Cache-Control": "public, max-age=86400"})
    except Exception as e:
        logger.error(f"Event photo error: {e}")
        return web.Response(status=404)


async def api_menu(request):
    if not db_pool: return web.json_response({"count": 0})
    count = await db_pool.fetchval("SELECT COUNT(*) FROM menu_photos")
    return web.json_response({"count": int(count)})


async def api_menu_photos(request):
    if not db_pool: return web.json_response({"photos": []})
    rows = await db_pool.fetch("SELECT file_id, page_num FROM menu_photos ORDER BY page_num, added_at")
    return web.json_response({"photos": [{"page_num": r["page_num"], "url": f"/api/menu-photo/{r['file_id']}"} for r in rows]})


async def api_menu_photo(request):
    file_id = request.match_info.get("file_id", "")
    if not file_id: return web.Response(status=404)
    try:
        data, ct = await _proxy_tg_file(file_id)
        return web.Response(body=data, content_type=ct, headers={"Cache-Control": "public, max-age=86400"})
    except:
        return web.Response(status=404)


async def api_get_flavors(request):
    tg_user_id = request.rel_url.query.get("tg_user_id", "")
    if not tg_user_id or not db_pool: return web.json_response({"flavors": ""})
    try:
        row = await db_pool.fetchrow("SELECT flavors FROM user_flavors WHERE tg_user_id=$1", int(tg_user_id))
        return web.json_response({"flavors": row["flavors"] if row else ""})
    except:
        return web.json_response({"flavors": ""})


async def api_save_flavors(request):
    try:
        data = await request.json()
        tg_user_id = int(data.get("tg_user_id", 0))
        tg_username = str(data.get("tg_username", ""))
        flavors = str(data.get("flavors", "")).strip()[:500]
        if not tg_user_id or not db_pool: return web.json_response({"ok": False})
        async with db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO user_flavors (tg_user_id, tg_username, flavors, updated_at)
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (tg_user_id) DO UPDATE SET flavors=$3, tg_username=$2, updated_at=NOW()
            """, tg_user_id, tg_username, flavors)
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})


async def api_my_bookings(request):
    tg_user_id = request.rel_url.query.get("tg_user_id", "")
    if not tg_user_id or not db_pool: return web.json_response({"bookings": []})
    try:
        rows = await db_pool.fetch("""
            SELECT id, zone, table_num, book_date, book_time, guests, name, status, created_at
            FROM bookings WHERE tg_user_id=$1 ORDER BY created_at DESC LIMIT 20
        """, int(tg_user_id))
        return web.json_response({"bookings": [
            {"id": r["id"], "zone": r["zone"], "table_num": r["table_num"],
             "book_date": r["book_date"], "book_time": r["book_time"],
             "guests": r["guests"], "name": r["name"], "status": r["status"]}
            for r in rows
        ]})
    except Exception as e:
        return web.json_response({"bookings": []})


async def api_register(request):
    try:
        data = await request.json()
        tg_user_id = int(data.get("tg_user_id") or 0)
        tg_username = str(data.get("tg_username") or "")
        name = str(data.get("name") or "").strip()
        phone = str(data.get("phone") or "").strip()
        email = str(data.get("email") or "").strip()
        if not name or not phone or not tg_user_id:
            return web.json_response({"ok": False, "error": "Missing fields"})
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO users (tg_user_id, tg_username, name, phone, email, age_confirmed)
                VALUES ($1,$2,$3,$4,$5,TRUE)
                ON CONFLICT (tg_user_id) DO UPDATE SET name=$3, phone=$4, email=$5, tg_username=$2
                RETURNING id
            """, tg_user_id, tg_username, name, phone, email)
        now = datetime.now().strftime("%d.%m.%Y %H:%M")
        try:
            await bot.send_message(ADMIN_CHAT_ID,
                f"👤 <b>Новый гость</b>\n\n👤 {name}\n📞 {phone}\n📧 {email or '—'}\n📌 @{tg_username} · {now}",
                parse_mode="HTML")
        except:
            pass
        return web.json_response({"ok": True, "id": row["id"]})
    except Exception as e:
        logger.error(f"Register error: {e}")
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def api_check_user(request):
    tg_user_id = request.rel_url.query.get("tg_user_id", "")
    if not tg_user_id or not db_pool: return web.json_response({"registered": False})
    try:
        row = await db_pool.fetchrow("SELECT id, name FROM users WHERE tg_user_id=$1", int(tg_user_id))
        if row: return web.json_response({"registered": True, "name": row["name"]})
        return web.json_response({"registered": False})
    except:
        return web.json_response({"registered": False})



async def serve_rules(request):
    here = os.path.dirname(os.path.abspath(__file__))
    pdf_path = os.path.join(here, "rules.pdf")
    if not os.path.exists(pdf_path):
        return web.Response(text="Файл не найден", status=404)
    with open(pdf_path, "rb") as f:
        return web.Response(
            body=f.read(),
            content_type="application/pdf",
            headers={"Content-Disposition": "inline; filename=rules.pdf"}
        )

async def serve_index(request):
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "index.html"), "r", encoding="utf-8") as f:
        return web.Response(text=f.read(), content_type="text/html", headers={"Cache-Control": "no-cache"})


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
    app.router.add_get("/rules", serve_rules)
    app.router.add_get("/index.html", serve_index)
    app.router.add_get("/api/taken", api_taken)
    app.router.add_post("/api/booking", api_booking_post)
    app.router.add_post("/api/cancel-booking", api_cancel_booking)
    app.router.add_get("/api/my-bookings", api_my_bookings)
    app.router.add_get("/api/flavors", api_get_flavors)
    app.router.add_post("/api/flavors", api_save_flavors)
    app.router.add_post("/api/register", api_register)
    app.router.add_get("/api/check-user", api_check_user)
    app.router.add_get("/api/events", api_events)
    app.router.add_get("/api/event-photo/{file_id}", api_event_photo)
    app.router.add_get("/api/menu", api_menu)
    app.router.add_get("/api/menu-photos", api_menu_photos)
    app.router.add_get("/api/menu-photo/{file_id}", api_menu_photo)

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    logger.info(f"Web server on :{PORT}")

    # Background tasks
    asyncio.create_task(reminder_task())
    asyncio.create_task(rating_task())

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

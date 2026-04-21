"""
LIWAN Bot — полная версия с событиями, меню, галереей, бронями
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

class AddEvent(StatesGroup):
    title = State()
    date_str = State()
    time_str = State()
    description = State()
    entry = State()
    photo = State()


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
                id          SERIAL PRIMARY KEY,
                title       TEXT NOT NULL,
                date_str    TEXT NOT NULL,
                time_str    TEXT NOT NULL,
                description TEXT,
                entry_info  TEXT,
                active        BOOLEAN DEFAULT TRUE,
                photo_file_id TEXT,
                created_at    TIMESTAMP DEFAULT NOW()
            )
        """)
        try:
            await conn.execute('ALTER TABLE events ADD COLUMN IF NOT EXISTS photo_file_id TEXT')
        except:
            pass
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_flavors (
                id         SERIAL PRIMARY KEY,
                tg_user_id BIGINT NOT NULL,
                tg_username TEXT,
                flavors    TEXT NOT NULL DEFAULT '',
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)
        try:
            await conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_user_flavors_tg ON user_flavors(tg_user_id)')
        except:
            pass
        try:
            await conn.execute('ALTER TABLE bookings ADD COLUMN IF NOT EXISTS tg_user_id BIGINT')
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
    from datetime import timedelta
    try:
        dt = datetime.strptime(book_time, "%H:%M")
    except:
        return [book_time]
    slots = []
    for i in range(0, duration_minutes, step):
        slots.append((dt + timedelta(minutes=i)).strftime("%H:%M"))
    return slots


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
        parse_mode="HTML",
        reply_markup=kb
    )


# ─── Бронирование ─────────────────────────────────────────────────────────────

@dp.message(F.web_app_data)
async def handle_webapp(message: Message):
    try:
        data = json.loads(message.web_app_data.data)
        tg_user = f"@{message.from_user.username or message.from_user.full_name}"
        tg_user_id = message.from_user.id
        # Get user flavors to include in notification
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
                tg_user,
                tg_user_id
            )
            booking_id = row["id"]
        now = datetime.now().strftime("%d.%m.%Y %H:%M")
        flavors_line = f"\n🌸 <b>Любимые ароматы:</b> {flavors_text}" if flavors_text else ""
        text = (
            f"🔔 <b>Новая бронь #{booking_id}</b>\n\n"
            f"🌿 {data.get('zone','—')} · Столик №{data.get('table','—')}\n"
            f"📅 {data.get('date','—')} · {data.get('time','—')}\n"
            f"👥 {data.get('guests','—')} гост.\n"
            f"👤 {data.get('name','—')} · {data.get('phone','—')}\n"
            f"📌 {tg_user} · {now}"
            f"{flavors_line}"
        )
        await bot.send_message(ADMIN_CHAT_ID, text, parse_mode="HTML")
        await message.answer(f"✅ <b>Бронь #{booking_id} принята!</b>", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Webapp error: {e}")
        await message.answer("⚠️ Ошибка. Попробуйте ещё раз.")


# ─── Галерея / Меню ───────────────────────────────────────────────────────────

@dp.message(F.text == "🖼 Галерея")
async def btn_gallery(message: Message):
    rows = await db_pool.fetchval("SELECT COUNT(*) FROM gallery")
    if not rows:
        await message.answer("📭 Галерея пока пуста.")
        return
    all_rows = await db_pool.fetch("SELECT file_id FROM gallery ORDER BY added_at DESC")
    from aiogram.types import InputMediaPhoto
    ids = [r["file_id"] for r in all_rows]
    for i in range(0, len(ids), 10):
        chunk = ids[i:i+10]
        await bot.send_media_group(message.chat.id, [InputMediaPhoto(media=fid) for fid in chunk])

@dp.message(F.text == "📋 Меню")
async def btn_menu(message: Message):
    rows = await db_pool.fetch("SELECT file_id FROM menu_photos ORDER BY page_num, added_at")
    if not rows:
        await message.answer("📭 Меню пока не загружено.")
        return
    from aiogram.types import InputMediaPhoto
    ids = [r["file_id"] for r in rows]
    for i in range(0, len(ids), 10):
        chunk = ids[i:i+10]
        await bot.send_media_group(message.chat.id, [InputMediaPhoto(media=fid) for fid in chunk])

@dp.message(F.text == "📞 Контакты")
async def btn_contacts(message: Message):
    await message.answer(
        "📍 <b>LIWAN</b>\n\n🕐 Ежедневно: 15:00 — 02:00\n📱 @liwan_hookah",
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
    elif current and "AddEvent" in current:
        await message.answer("❌ Добавление события отменено.", reply_markup=main_keyboard())
    else:
        await message.answer("Готово.", reply_markup=main_keyboard())


# ─── События ──────────────────────────────────────────────────────────────────

@dp.message(Command("addevent"))
async def cmd_addevent(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return
    await state.set_state(AddEvent.title)
    await message.answer(
        "🎉 <b>Добавление события</b>\n\nВведите <b>название</b> события:",
        parse_mode="HTML", reply_markup=ReplyKeyboardRemove()
    )

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
    await message.answer("Введите <b>описание</b> события:", parse_mode="HTML")

@dp.message(AddEvent.description)
async def event_desc(message: Message, state: FSMContext):
    await state.update_data(description=message.text.strip())
    await state.set_state(AddEvent.entry)
    await message.answer("Введите <b>информацию о входе</b> (например: <code>Вход свободный</code> или <code>По приглашениям</code>):", parse_mode="HTML")

@dp.message(AddEvent.entry)
async def event_entry(message: Message, state: FSMContext):
    await state.update_data(entry=message.text.strip())
    await state.set_state(AddEvent.photo)
    await message.answer(
        "🖼 Отправьте <b>фото афиши</b> события.\n\nЕсли фото нет — напишите <code>нет</code>",
        parse_mode="HTML"
    )

@dp.message(AddEvent.photo)
async def event_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    photo_file_id = None
    if message.photo:
        photo_file_id = message.photo[-1].file_id
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


# ─── Брони (для админа) ───────────────────────────────────────────────────────

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
    await message.answer(f"✅ Бронь #{bid} отменена.")


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
            "/done — завершить загрузку фото",
            parse_mode="HTML"
        )
    else:
        await message.answer("/start — открыть приложение")


# ─── API для Mini App ─────────────────────────────────────────────────────────

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
    taken = []
    if book_time:
        for r in rows:
            blocked = get_blocked_slots(r['book_time'])
            if book_time in blocked:
                taken.append(r['table_num'])
    else:
        taken = [r['table_num'] for r in rows]
    return web.json_response({"taken": taken})


async def api_events(request):
    if not db_pool:
        return web.json_response({"events": []})
    rows = await db_pool.fetch("SELECT * FROM events WHERE active=TRUE ORDER BY created_at DESC LIMIT 10")
    events = []
    for r in rows:
        photo_url = None
        if r.get("photo_file_id"):
            photo_url = f"/api/event-photo/{r['photo_file_id']}"
        events.append({
            "id": r["id"],
            "title": r["title"],
            "date_str": r["date_str"],
            "time_str": r["time_str"],
            "description": r["description"] or "",
            "entry_info": r["entry_info"] or "",
            "photo_url": photo_url
        })
    return web.json_response({"events": events})


async def api_event_photo(request):
    file_id = request.match_info.get("file_id", "")
    if not file_id:
        return web.Response(status=404)
    try:
        import aiohttp as aiohttp_lib
        file = await bot.get_file(file_id)
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
        async with aiohttp_lib.ClientSession() as session:
            async with session.get(file_url) as resp:
                data = await resp.read()
                ct = resp.headers.get("Content-Type", "image/jpeg")
                return web.Response(body=data, content_type=ct,
                    headers={"Cache-Control": "public, max-age=86400"})
    except Exception as e:
        logger.error(f"Photo proxy error: {e}")
        return web.Response(status=404)


async def api_menu(request):
    if not db_pool:
        return web.json_response({"count": 0})
    count = await db_pool.fetchval("SELECT COUNT(*) FROM menu_photos")
    return web.json_response({"count": int(count)})



async def api_booking_post(request):
    """Fallback endpoint when tg.sendData is not available"""
    try:
        data = await request.json()
        # Get user info from Telegram init data if available
        tg_user = "web"
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO bookings (zone,table_num,book_date,book_time,guests,name,phone,tg_user)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING id
            """,
                str(data.get("zone") or "—"),
                int(data.get("table") or 0),
                str(data.get("date") or "—"),
                str(data.get("time") or "—"),
                int(data.get("guests") or 1),
                str(data.get("name") or "—"),
                str(data.get("phone") or "—"),
                tg_user
            )
            booking_id = row["id"]
        now = datetime.now().strftime("%d.%m.%Y %H:%M")
        text = (
            f"🔔 <b>Новая бронь #{booking_id}</b>\n\n"
            f"🌿 {data.get('zone','—')} · Столик №{data.get('table','—')}\n"
            f"📅 {data.get('date','—')} · {data.get('time','—')}\n"
            f"👥 {data.get('guests','—')} гост.\n"
            f"👤 {data.get('name','—')} · {data.get('phone','—')}\n"
            f"🕓 {now}"
        )
        await bot.send_message(ADMIN_CHAT_ID, text, parse_mode="HTML")
        return web.json_response({"ok": True, "id": booking_id})
    except Exception as e:
        logger.error(f"API booking error: {e}")
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def api_menu_photos(request):
    """Return menu photos as proxied images"""
    if not db_pool:
        return web.json_response({"photos": []})
    rows = await db_pool.fetch("SELECT file_id, page_num FROM menu_photos ORDER BY page_num, added_at")
    photos = []
    for r in rows:
        photos.append({
            "page_num": r["page_num"],
            "url": f"/api/menu-photo/{r['file_id']}"
        })
    return web.json_response({"photos": photos})


async def api_menu_photo(request):
    """Proxy Telegram menu photo to browser"""
    file_id = request.match_info.get("file_id", "")
    if not file_id:
        return web.Response(status=404)
    try:
        import aiohttp as aiohttp_lib
        file = await bot.get_file(file_id)
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
        async with aiohttp_lib.ClientSession() as session:
            async with session.get(file_url) as resp:
                data = await resp.read()
                ct = resp.headers.get("Content-Type", "image/jpeg")
                return web.Response(body=data, content_type=ct,
                    headers={"Cache-Control": "public, max-age=86400"})
    except Exception as e:
        logger.error(f"Menu photo proxy error: {e}")
        return web.Response(status=404)


async def api_get_flavors(request):
    """Get user flavors by tg_user_id"""
    tg_user_id = request.rel_url.query.get("tg_user_id", "")
    if not tg_user_id or not db_pool:
        return web.json_response({"flavors": ""})
    try:
        row = await db_pool.fetchrow(
            "SELECT flavors FROM user_flavors WHERE tg_user_id=$1", int(tg_user_id)
        )
        return web.json_response({"flavors": row["flavors"] if row else ""})
    except Exception as e:
        return web.json_response({"flavors": ""})


async def api_save_flavors(request):
    """Save user flavors"""
    try:
        data = await request.json()
        tg_user_id = int(data.get("tg_user_id", 0))
        tg_username = str(data.get("tg_username", ""))
        flavors = str(data.get("flavors", "")).strip()[:500]
        if not tg_user_id or not db_pool:
            return web.json_response({"ok": False})
        async with db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO user_flavors (tg_user_id, tg_username, flavors, updated_at)
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (tg_user_id) DO UPDATE
                SET flavors=$3, tg_username=$2, updated_at=NOW()
            """, tg_user_id, tg_username, flavors)
        return web.json_response({"ok": True})
    except Exception as e:
        logger.error(f"Save flavors error: {e}")
        return web.json_response({"ok": False, "error": str(e)})


async def api_my_bookings(request):
    """Get bookings history for user"""
    tg_user_id = request.rel_url.query.get("tg_user_id", "")
    if not tg_user_id or not db_pool:
        return web.json_response({"bookings": []})
    try:
        # Match by tg_user_id stored in tg_user field or by exact match
        rows = await db_pool.fetch("""
            SELECT id, zone, table_num, book_date, book_time, guests, name, status, created_at
            FROM bookings
            WHERE tg_user_id=$1
            ORDER BY created_at DESC LIMIT 20
        """, int(tg_user_id))
        bookings = []
        for r in rows:
            bookings.append({
                "id": r["id"],
                "zone": r["zone"],
                "table_num": r["table_num"],
                "book_date": r["book_date"],
                "book_time": r["book_time"],
                "guests": r["guests"],
                "name": r["name"],
                "status": r["status"],
            })
        return web.json_response({"bookings": bookings})
    except Exception as e:
        logger.error(f"My bookings error: {e}")
        return web.json_response({"bookings": []})

async def serve_index(request):
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "index.html"), "r", encoding="utf-8") as f:
        return web.Response(text=f.read(), content_type="text/html", headers={
            "Cache-Control": "no-cache"
        })


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
    app.router.add_post("/api/booking", api_booking_post)
    app.router.add_get("/api/my-bookings", api_my_bookings)
    app.router.add_get("/api/flavors", api_get_flavors)
    app.router.add_post("/api/flavors", api_save_flavors)
    app.router.add_get("/api/events", api_events)
    app.router.add_get("/api/event-photo/{file_id}", api_event_photo)
    app.router.add_get("/api/menu", api_menu)
    app.router.add_get("/api/menu-photos", api_menu_photos)
    app.router.add_get("/api/menu-photo/{file_id}", api_menu_photo)

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    logger.info(f"Web server on :{PORT}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

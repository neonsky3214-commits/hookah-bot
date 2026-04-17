import asyncio
import logging
from datetime import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)
from config import BOT_TOKEN, ADMIN_CHAT_ID, ADMIN_IDS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ─── States ───────────────────────────────────────────────────────────────────

class BookingGuest(StatesGroup):
    name = State()
    phone = State()
    date = State()
    time = State()
    guests = State()
    confirm = State()

class BookingAdmin(StatesGroup):
    name = State()
    phone = State()
    date = State()
    time = State()
    guests = State()
    confirm = State()

# ─── Helpers ──────────────────────────────────────────────────────────────────

def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📅 Забронировать столик")]],
        resize_keyboard=True
    )

def cancel_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True
    )

def confirm_inline(prefix: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"{prefix}:confirm"),
            InlineKeyboardButton(text="❌ Отменить", callback_data=f"{prefix}:cancel"),
        ]
    ])

def format_booking(data: dict) -> str:
    return (
        f"👤 <b>Имя:</b> {data['name']}\n"
        f"📞 <b>Телефон:</b> {data['phone']}\n"
        f"📅 <b>Дата:</b> {data['date']}\n"
        f"🕐 <b>Время:</b> {data['time']}\n"
        f"👥 <b>Количество гостей:</b> {data['guests']}"
    )

def validate_date(text: str) -> bool:
    try:
        datetime.strptime(text.strip(), "%d.%m.%Y")
        return True
    except ValueError:
        return False

def validate_time(text: str) -> bool:
    try:
        datetime.strptime(text.strip(), "%H:%M")
        return True
    except ValueError:
        return False

# ─── /start ───────────────────────────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "🌿 Добро пожаловать в кальянную!\n\n"
        "Нажмите кнопку ниже, чтобы забронировать столик.",
        reply_markup=main_keyboard()
    )

# ─── Guest booking flow ───────────────────────────────────────────────────────

@dp.message(F.text == "📅 Забронировать столик")
async def guest_start_booking(message: Message, state: FSMContext):
    await state.set_state(BookingGuest.name)
    await message.answer(
        "Отлично! Давайте оформим бронь.\n\n"
        "Введите ваше <b>имя</b>:",
        parse_mode="HTML",
        reply_markup=cancel_keyboard()
    )

@dp.message(BookingGuest.name, F.text != "❌ Отмена")
async def guest_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await state.set_state(BookingGuest.phone)
    await message.answer("Введите ваш <b>номер телефона</b>:", parse_mode="HTML")

@dp.message(BookingGuest.phone, F.text != "❌ Отмена")
async def guest_phone(message: Message, state: FSMContext):
    await state.update_data(phone=message.text.strip())
    await state.set_state(BookingGuest.date)
    await message.answer(
        "Введите <b>дату</b> бронирования в формате <code>ДД.ММ.ГГГГ</code>\n"
        "Например: <code>25.07.2025</code>",
        parse_mode="HTML"
    )

@dp.message(BookingGuest.date, F.text != "❌ Отмена")
async def guest_date(message: Message, state: FSMContext):
    if not validate_date(message.text):
        await message.answer("⚠️ Неверный формат. Введите дату как <code>ДД.ММ.ГГГГ</code>", parse_mode="HTML")
        return
    await state.update_data(date=message.text.strip())
    await state.set_state(BookingGuest.time)
    await message.answer(
        "Введите <b>время</b> в формате <code>ЧЧ:ММ</code>\n"
        "Например: <code>19:30</code>",
        parse_mode="HTML"
    )

@dp.message(BookingGuest.time, F.text != "❌ Отмена")
async def guest_time(message: Message, state: FSMContext):
    if not validate_time(message.text):
        await message.answer("⚠️ Неверный формат. Введите время как <code>ЧЧ:ММ</code>", parse_mode="HTML")
        return
    await state.update_data(time=message.text.strip())
    await state.set_state(BookingGuest.guests)
    await message.answer("Сколько <b>гостей</b> будет? Введите число:", parse_mode="HTML")

@dp.message(BookingGuest.guests, F.text != "❌ Отмена")
async def guest_guests(message: Message, state: FSMContext):
    if not message.text.strip().isdigit():
        await message.answer("⚠️ Пожалуйста, введите число.")
        return
    await state.update_data(guests=message.text.strip())
    data = await state.get_data()
    await state.set_state(BookingGuest.confirm)
    await message.answer(
        f"📋 <b>Проверьте данные бронирования:</b>\n\n{format_booking(data)}",
        parse_mode="HTML",
        reply_markup=confirm_inline("guest")
    )

@dp.callback_query(BookingGuest.confirm, F.data == "guest:confirm")
async def guest_confirm(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        "✅ <b>Бронь принята!</b> Ждём вас!\n\n"
        "Если понадобится изменить — просто напишите нам.",
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )
    # Notify admins
    await notify_admins(data, source=f"@{callback.from_user.username or callback.from_user.full_name}")
    await callback.answer()

@dp.callback_query(BookingGuest.confirm, F.data == "guest:cancel")
async def guest_cancel_confirm(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("❌ Бронирование отменено.", reply_markup=main_keyboard())
    await callback.answer()

# ─── Cancel at any step ───────────────────────────────────────────────────────

@dp.message(F.text == "❌ Отмена", StateFilter(BookingGuest, BookingAdmin))
async def cancel_booking(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Бронирование отменено.", reply_markup=main_keyboard())

# ─── Admin manual booking (/newbook) ─────────────────────────────────────────

@dp.message(Command("newbook"))
async def admin_newbook(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ Нет доступа.")
        return
    await state.set_state(BookingAdmin.name)
    await message.answer("Введите <b>имя гостя</b>:", parse_mode="HTML", reply_markup=cancel_keyboard())

@dp.message(BookingAdmin.name, F.text != "❌ Отмена")
async def admin_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await state.set_state(BookingAdmin.phone)
    await message.answer("Введите <b>номер телефона</b>:", parse_mode="HTML")

@dp.message(BookingAdmin.phone, F.text != "❌ Отмена")
async def admin_phone(message: Message, state: FSMContext):
    await state.update_data(phone=message.text.strip())
    await state.set_state(BookingAdmin.date)
    await message.answer("Введите <b>дату</b> (формат <code>ДД.ММ.ГГГГ</code>):", parse_mode="HTML")

@dp.message(BookingAdmin.date, F.text != "❌ Отмена")
async def admin_date(message: Message, state: FSMContext):
    if not validate_date(message.text):
        await message.answer("⚠️ Неверный формат. Введите дату как <code>ДД.ММ.ГГГГ</code>", parse_mode="HTML")
        return
    await state.update_data(date=message.text.strip())
    await state.set_state(BookingAdmin.time)
    await message.answer("Введите <b>время</b> (формат <code>ЧЧ:ММ</code>):", parse_mode="HTML")

@dp.message(BookingAdmin.time, F.text != "❌ Отмена")
async def admin_time(message: Message, state: FSMContext):
    if not validate_time(message.text):
        await message.answer("⚠️ Неверный формат. Введите время как <code>ЧЧ:ММ</code>", parse_mode="HTML")
        return
    await state.update_data(time=message.text.strip())
    await state.set_state(BookingAdmin.guests)
    await message.answer("Введите <b>количество гостей</b>:", parse_mode="HTML")

@dp.message(BookingAdmin.guests, F.text != "❌ Отмена")
async def admin_guests(message: Message, state: FSMContext):
    if not message.text.strip().isdigit():
        await message.answer("⚠️ Пожалуйста, введите число.")
        return
    await state.update_data(guests=message.text.strip())
    data = await state.get_data()
    await state.set_state(BookingAdmin.confirm)
    await message.answer(
        f"📋 <b>Проверьте данные бронирования:</b>\n\n{format_booking(data)}",
        parse_mode="HTML",
        reply_markup=confirm_inline("admin")
    )

@dp.callback_query(BookingAdmin.confirm, F.data == "admin:confirm")
async def admin_confirm(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("✅ Бронь добавлена и отправлена в канал.", reply_markup=main_keyboard())
    await notify_admins(data, source=f"Администратор @{callback.from_user.username or callback.from_user.full_name}")
    await callback.answer()

@dp.callback_query(BookingAdmin.confirm, F.data == "admin:cancel")
async def admin_cancel_confirm(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("❌ Бронирование отменено.", reply_markup=main_keyboard())
    await callback.answer()

# ─── Notify admins ────────────────────────────────────────────────────────────

async def notify_admins(data: dict, source: str):
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    text = (
        f"🔔 <b>Новое бронирование!</b>\n\n"
        f"{format_booking(data)}\n\n"
        f"📌 <b>Источник:</b> {source}\n"
        f"🕓 <b>Создано:</b> {now}"
    )
    try:
        await bot.send_message(ADMIN_CHAT_ID, text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Failed to notify admins: {e}")

# ─── Help ─────────────────────────────────────────────────────────────────────

@dp.message(Command("help"))
async def cmd_help(message: Message):
    is_admin = message.from_user.id in ADMIN_IDS
    text = (
        "📖 <b>Команды бота:</b>\n\n"
        "/start — Главное меню\n"
        "/help — Справка\n"
    )
    if is_admin:
        text += "/newbook — Внести бронь вручную (только для админов)\n"
    await message.answer(text, parse_mode="HTML")

# ─── Run ──────────────────────────────────────────────────────────────────────

async def main():
    logger.info("Bot started")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

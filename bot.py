import asyncio
import secrets
import sqlite3
import csv
import io
import json
import logging
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, WebAppInfo, FSInputFile
from aiogram.enums.parse_mode import ParseMode

# ========== КОНФИГУРАЦИЯ ==========
BOT_TOKEN = '8676556947:AAEpE6bixCFc5qDS_RafwmdU-ahEA_lIcLo'
ADMIN_IDS = [123456789]
DB_PATH = "lucky_bot.db"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ========== РАБОТА С БАЗОЙ ДАННЫХ (SQLite) ==========
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            subscribed BOOLEAN DEFAULT 0,
            promo_received INTEGER DEFAULT 0
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS promocodes (
            code TEXT PRIMARY KEY,
            user_id INTEGER,
            discount INTEGER,
            valid_until TIMESTAMP,
            is_used BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS daily_attempts (
            user_id INTEGER,
            attempt_date TEXT,
            PRIMARY KEY (user_id, attempt_date)
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('daily_limit', '2')")
    conn.commit()
    conn.close()

async def register_user(user_id, username, first_name):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?, ?, ?)",
                (user_id, username, first_name))
    conn.commit()
    conn.close()

async def can_play_today(user_id):
    today = datetime.now().date().isoformat()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key = 'daily_limit'")
    limit = int(cur.fetchone()[0])
    cur.execute("SELECT 1 FROM daily_attempts WHERE user_id = ? AND attempt_date = ?", (user_id, today))
    can_play = cur.fetchone() is None
    conn.close()
    return can_play, limit

async def register_attempt(user_id):
    today = datetime.now().date().isoformat()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO daily_attempts (user_id, attempt_date) VALUES (?, ?)", (user_id, today))
    conn.commit()
    conn.close()

async def generate_promocode(user_id, discount=10):
    code = f"LUCKY_{secrets.token_hex(4).upper()}"
    valid_until = (datetime.now() + timedelta(days=7)).isoformat()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT INTO promocodes (code, user_id, discount, valid_until) VALUES (?, ?, ?, ?)",
                (code, user_id, discount, valid_until))
    conn.commit()
    conn.close()
    return code

# ========== КОМАНДЫ БОТА ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await register_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    # Кнопка для запуска веб-приложения
    web_app_button = KeyboardButton(
        text="🎮 Веб-игра",
        web_app=WebAppInfo(url="https://ВАША_ССЫЛКА_НА_ИГРУ/index.html")  # Ссылку получим позже
    )
    keyboard = ReplyKeyboardMarkup(
        keyboard=[[web_app_button], ["🎁 Мои промокоды", "🔔 Подписка на новости"]],
        resize_keyboard=True
    )
    await message.answer(
        "🎲 *Добро пожаловать в LuckyDiscountBot!*\n\n"
        "Ты можешь получить скидку в «ТехноМаркет», сыграв в нашу веб-игру.\n"
        "Правила простые: угадай число от 1 до 10 за 3 попытки.\n\n"
        "Удачи! 🍀",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=keyboard
    )

@dp.message(F.text == "🎁 Мои промокоды")
async def my_promocodes(message: types.Message):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT code, discount, valid_until FROM promocodes WHERE user_id = ? AND is_used = 0 AND valid_until > datetime('now')", (message.from_user.id,))
    promos = cur.fetchall()
    conn.close()
    if not promos:
        await message.answer("У тебя пока нет активных промокодов. Сыграй и получи скидку!")
        return
    text = "🎫 *Твои активные промокоды:*\n\n"
    for code, discount, valid_until in promos:
        text += f"`{code}` — скидка {discount}% (до {valid_until[:10]})\n"
    await message.answer(text, parse_mode=ParseMode.MARKDOWN_V2)

@dp.message(F.text == "🔔 Подписка на новости")
async def subscribe(message: types.Message):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE users SET subscribed = 1 WHERE user_id = ?", (message.from_user.id,))
    conn.commit()
    conn.close()
    await message.answer("🎉 Спасибо! Ты подписан на новости о скидках и акциях.")

# *** ГЛАВНЫЙ ОБРАБОТЧИК: ПОЛУЧАЕТ ДАННЫЕ ИЗ ИГРЫ И ВЫДАЁТ ПРОМОКОД ***
@dp.message(F.web_app_data)
async def handle_web_app_data(message: types.Message):
    user_id = message.from_user.id
    data = json.loads(message.web_app_data.data)
    is_win = data.get('win', False)

    # 1. Проверка ежедневного лимита попыток
    can_play, limit = await can_play_today(user_id)
    if not can_play:
        await message.answer(f"❌ Ты уже использовал {limit} попытки сегодня. Возвращайся завтра!")
        return

    # 2. Регистрируем попытку
    await register_attempt(user_id)

    # 3. Если победа — генерируем промокод
    if is_win:
        promo = await generate_promocode(user_id)
        await message.answer(
            f"🎉 *Поздравляем! Ты выиграл скидку 10%!*\n\n"
            f"Твой промокод: `{promo}`\n"
            f"Действует 7 дней.\n\n"
            f"Ждём тебя в «ТехноМаркет» 🛒",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    else:
        await message.answer(f"😔 Не повезло. Загаданное число было {data.get('secret')}. Попробуй ещё раз завтра!")

# ========== АДМИН-ПАНЕЛЬ (КОМАНДЫ ДЛЯ ВАС) ==========
@dp.message(Command("stats"))
async def stats(message: types.Message):
    if message.from_user.id not in ADMIN_IDS: return
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    users = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM promocodes")
    promos = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM promocodes WHERE is_used = 1")
    used = cur.fetchone()[0]
    await message.answer(f"📊 *Статистика*\n👥 Пользователей: {users}\n🎫 Выдано промокодов: {promos}\n✅ Использовано: {used}",
                         parse_mode=ParseMode.MARKDOWN_V2)
    conn.close()

@dp.message(Command("set_limit"))
async def set_limit(message: types.Message):
    if message.from_user.id not in ADMIN_IDS: return
    try:
        new_limit = int(message.text.split()[1])
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("UPDATE settings SET value = ? WHERE key = 'daily_limit'", (str(new_limit),))
        conn.commit()
        conn.close()
        await message.answer(f"✅ Лимит попыток в день изменён на {new_limit}.")
    except: await message.answer("Использование: /set_limit <число>")

@dp.message(Command("gen_promo"))
async def gen_promo(message: types.Message):
    if message.from_user.id not in ADMIN_IDS: return
    try:
        target_id = int(message.text.split()[1])
        promo = await generate_promocode(target_id)
        await message.answer(f"✅ Промокод для {target_id}: `{promo}`", parse_mode=ParseMode.MARKDOWN_V2)
    except: await message.answer("Использование: /gen_promo <user_id>")

@dp.message(Command("report"))
async def report(message: types.Message):
    if message.from_user.id not in ADMIN_IDS: return
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id, username, registered_at, promo_received FROM users")
    data = cur.fetchall()
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(["user_id", "username", "registered_at", "promo_received"])
    writer.writerows(data)
    output.seek(0)
    with open("report.csv", "w", encoding="utf-8") as f:
        f.write(output.getvalue())
    await message.answer_document(FSInputFile("report.csv", filename="users_report.csv"))

@dp.message(Command("broadcast"))
async def broadcast(message: types.Message):
    if message.from_user.id not in ADMIN_IDS: return
    text = message.text.replace("/broadcast", "").strip()
    if not text: await message.answer("Укажите текст рассылки после команды.")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE subscribed = 1")
    users = cur.fetchall()
    conn.close()
    sent = 0
    for (user_id,) in users:
        try: await bot.send_message(user_id, f"📢 *Новость магазина*\n\n{text}", parse_mode=ParseMode.MARKDOWN_V2); sent += 1
        except: pass
    await message.answer(f"✅ Рассылка завершена. Отправлено {sent} из {len(users)} подписчикам.")

# ========== ЗАПУСК БОТА ==========
async def main():
    init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
import random
import sqlite3
import time
from contextlib import contextmanager

import telebot
from telebot import types

TOKEN = '8771040353:AAGf9ZxAFODQ6XbadtF4lZTiAx4Kaq28a9w'
DB_PATH = 'bot.db'
bot = telebot.TeleBot(TOKEN)

sessions = {}  # user_id -> {"game": str, ...state}  (эфемерно, в памяти)
START_BALANCE = 1000
BET_AMOUNTS = [10, 50, 100, 500]

RPS_CHOICES = ["Камень", "Ножницы", "Бумага"]
RPS_BEATS = {"Камень": "Ножницы", "Ножницы": "Бумага", "Бумага": "Камень"}


# ---------- DB ----------

@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id    INTEGER PRIMARY KEY,
                username   TEXT,
                balance    INTEGER NOT NULL DEFAULT 1000,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS game_history (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id   INTEGER NOT NULL,
                game      TEXT NOT NULL,
                bet       INTEGER,
                result    TEXT,
                delta     INTEGER,
                played_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_history_user ON game_history(user_id)")


def ensure_user(uid, username=None):
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, username, balance) VALUES (?, ?, ?)",
            (uid, username, START_BALANCE),
        )
        if username:
            conn.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, uid))


def get_balance(uid):
    ensure_user(uid)
    with db() as conn:
        row = conn.execute("SELECT balance FROM users WHERE user_id = ?", (uid,)).fetchone()
        return row["balance"]


def change_balance(uid, delta):
    with db() as conn:
        conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (delta, uid))
        row = conn.execute("SELECT balance FROM users WHERE user_id = ?", (uid,)).fetchone()
        return row["balance"]


def log_game(uid, game, bet, result, delta):
    with db() as conn:
        conn.execute(
            "INSERT INTO game_history (user_id, game, bet, result, delta) VALUES (?, ?, ?, ?, ?)",
            (uid, game, bet, result, delta),
        )


def get_stats(uid):
    with db() as conn:
        row = conn.execute("""
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(CASE WHEN delta > 0 THEN 1 ELSE 0 END), 0) AS wins,
                COALESCE(SUM(CASE WHEN delta < 0 THEN 1 ELSE 0 END), 0) AS losses,
                COALESCE(SUM(delta), 0) AS net
            FROM game_history
            WHERE user_id = ?
        """, (uid,)).fetchone()
        return dict(row) if row else {"total": 0, "wins": 0, "losses": 0, "net": 0}


# ---------- Keyboards ----------

def main_menu():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🎯 Угадай число", callback_data="game:guess"),
        types.InlineKeyboardButton("✊ Камень-ножницы-бумага", callback_data="game:rps"),
        types.InlineKeyboardButton("🎲 Кости", callback_data="game:dice"),
        types.InlineKeyboardButton("🪙 Монетка", callback_data="game:coin"),
    )
    kb.add(types.InlineKeyboardButton("📊 Статистика", callback_data="stats"))
    return kb


def rps_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=3)
    kb.add(*[types.InlineKeyboardButton(c, callback_data=f"rps:{c}") for c in RPS_CHOICES])
    kb.add(types.InlineKeyboardButton("⬅️ В меню", callback_data="menu"))
    return kb


def back_keyboard():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("⬅️ В меню", callback_data="menu"))
    return kb


def bet_keyboard(game, balance):
    kb = types.InlineKeyboardMarkup(row_width=4)
    buttons = [
        types.InlineKeyboardButton(str(a), callback_data=f"bet:{game}:{a}")
        for a in BET_AMOUNTS if a <= balance
    ]
    if buttons:
        kb.add(*buttons)
    if balance <= 0:
        kb.add(types.InlineKeyboardButton("💰 Пополнить +1000", callback_data="topup"))
    kb.add(types.InlineKeyboardButton("⬅️ В меню", callback_data="menu"))
    return kb


def coin_side_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🦅 Орёл", callback_data="coin_pick:heads"),
        types.InlineKeyboardButton("🪙 Решка", callback_data="coin_pick:tails"),
    )
    kb.add(types.InlineKeyboardButton("⬅️ В меню", callback_data="menu"))
    return kb


def dice_pick_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("⬇️ Меньше (1-3) ×2", callback_data="dice_pick:low"),
        types.InlineKeyboardButton("⬆️ Больше (4-6) ×2", callback_data="dice_pick:high"),
    )
    kb.add(types.InlineKeyboardButton("🎯 Точное число ×6", callback_data="dice_pick:exact"))
    kb.add(types.InlineKeyboardButton("⬅️ В меню", callback_data="menu"))
    return kb


def dice_exact_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=3)
    kb.add(*[types.InlineKeyboardButton(str(i), callback_data=f"dice_exact:{i}") for i in range(1, 7)])
    kb.add(types.InlineKeyboardButton("⬅️ В меню", callback_data="menu"))
    return kb


# ---------- Handlers ----------

@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
    ensure_user(message.from_user.id, message.from_user.username)
    bot.send_message(
        message.chat.id,
        f"Привет! Это бот с мини-играми.\nБаланс: {get_balance(message.from_user.id)} 💰\n\nВыбери игру:",
        reply_markup=main_menu(),
    )


@bot.message_handler(commands=["play", "menu"])
def send_menu(message):
    ensure_user(message.from_user.id, message.from_user.username)
    bot.send_message(
        message.chat.id,
        f"Баланс: {get_balance(message.from_user.id)} 💰\nВыбери игру:",
        reply_markup=main_menu(),
    )


@bot.message_handler(commands=["balance"])
def cmd_balance(message):
    bot.reply_to(message, f"💰 Баланс: {get_balance(message.from_user.id)}")


@bot.message_handler(commands=["stats"])
def cmd_stats(message):
    bot.reply_to(message, format_stats(message.from_user.id))


def format_stats(uid):
    s = get_stats(uid)
    bal = get_balance(uid)
    winrate = f"{s['wins'] / s['total'] * 100:.1f}%" if s['total'] else "—"
    return (
        f"📊 Статистика\n"
        f"Партий: {s['total']}\n"
        f"Побед: {s['wins']} | Поражений: {s['losses']}\n"
        f"Винрейт: {winrate}\n"
        f"Чистый итог: {s['net']:+d} 💰\n"
        f"Баланс: {bal} 💰"
    )


@bot.callback_query_handler(func=lambda c: c.data == "menu")
def cb_menu(call):
    sessions.pop(call.from_user.id, None)
    bot.edit_message_text(
        f"Баланс: {get_balance(call.from_user.id)} 💰\nВыбери игру:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=main_menu(),
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data == "stats")
def cb_stats(call):
    bot.edit_message_text(
        format_stats(call.from_user.id),
        call.message.chat.id,
        call.message.message_id,
        reply_markup=back_keyboard(),
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data == "topup")
def cb_topup(call):
    uid = call.from_user.id
    new_bal = change_balance(uid, 1000)
    bot.answer_callback_query(call.id, f"+1000! Баланс: {new_bal}")
    bot.edit_message_text(
        f"Баланс пополнен. Теперь: {new_bal} 💰\nВыбери игру:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=main_menu(),
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("game:"))
def cb_game(call):
    ensure_user(call.from_user.id, call.from_user.username)
    game = call.data.split(":", 1)[1]
    uid = call.from_user.id

    if game == "guess":
        bal = get_balance(uid)
        sessions[uid] = {"game": "guess"}
        bot.edit_message_text(
            f"🎯 Угадай число. Баланс: {bal} 💰\n"
            f"Лимит: 10 попыток. Выплаты:\n"
            f"• 1 попытка → +10× ставки\n"
            f"• 2–3 → +3×\n"
            f"• 4–6 → +1×\n"
            f"• 7–10 → возврат ставки\n"
            f"• не угадал → −ставка\n\n"
            f"Выбери ставку:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=bet_keyboard("guess", bal),
        )

    elif game == "rps":
        bal = get_balance(uid)
        sessions[uid] = {"game": "rps"}
        bot.edit_message_text(
            f"✊ Камень-ножницы-бумага. Баланс: {bal} 💰\n"
            f"Победа ×2, ничья — возврат ставки, поражение — минус ставка.\n\nВыбери ставку:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=bet_keyboard("rps", bal),
        )

    elif game == "coin":
        sessions[uid] = {"game": "coin"}
        bal = get_balance(uid)
        bot.edit_message_text(
            f"🪙 Монетка. Баланс: {bal} 💰\nВыбери ставку:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=bet_keyboard("coin", bal),
        )

    elif game == "dice":
        sessions[uid] = {"game": "dice"}
        bal = get_balance(uid)
        bot.edit_message_text(
            f"🎲 Кости. Баланс: {bal} 💰\nВыбери ставку:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=bet_keyboard("dice", bal),
        )

    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("bet:"))
def cb_bet(call):
    _, game, amount = call.data.split(":")
    amount = int(amount)
    uid = call.from_user.id

    if get_balance(uid) < amount:
        bot.answer_callback_query(call.id, "Недостаточно монет!")
        return

    sessions[uid] = {"game": game, "bet": amount}

    if game == "coin":
        bot.edit_message_text(
            f"🪙 Ставка: {amount} 💰. Выбери сторону:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=coin_side_keyboard(),
        )
    elif game == "dice":
        bot.edit_message_text(
            f"🎲 Ставка: {amount} 💰. На что ставишь?",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=dice_pick_keyboard(),
        )
    elif game == "rps":
        bot.edit_message_text(
            f"✊ Ставка: {amount} 💰. Твой ход:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=rps_keyboard(),
        )
    elif game == "guess":
        sessions[uid].update({"secret": random.randint(1, 100), "tries": 0})
        bot.edit_message_text(
            f"🎯 Я загадал число от 1 до 100. Ставка: {amount} 💰.\n"
            f"Отправь свой вариант сообщением.",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=back_keyboard(),
        )

    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("coin_pick:"))
def cb_coin_pick(call):
    uid = call.from_user.id
    state = sessions.get(uid, {})
    if state.get("game") != "coin" or "bet" not in state:
        bot.answer_callback_query(call.id, "Ставка потеряна, запусти заново через /menu")
        return

    side = call.data.split(":")[1]
    bet = state["bet"]
    result = random.choice(["heads", "tails"])
    result_ru = "Орёл 🦅" if result == "heads" else "Решка 🪙"

    delta = bet if side == result else -bet
    new_bal = change_balance(uid, delta)
    log_game(uid, "coin", bet, f"{side}/{result}", delta)

    outcome = f"🎉 Победа! +{bet}" if delta > 0 else f"😞 Мимо. {delta}"
    sessions.pop(uid, None)
    bot.edit_message_text(
        f"🪙 Выпало: {result_ru}\n{outcome}\n\nБаланс: {new_bal} 💰",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=main_menu(),
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("dice_pick:"))
def cb_dice_pick(call):
    uid = call.from_user.id
    state = sessions.get(uid, {})
    if state.get("game") != "dice" or "bet" not in state:
        bot.answer_callback_query(call.id, "Ставка потеряна, запусти заново через /menu")
        return

    pick = call.data.split(":")[1]

    if pick == "exact":
        state["mode"] = "exact"
        bot.edit_message_text(
            f"🎯 Ставка {state['bet']} 💰 на точное число (×6). Выбери:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=dice_exact_keyboard(),
        )
        bot.answer_callback_query(call.id)
        return

    bet = state["bet"]
    dice_msg = bot.send_dice(call.message.chat.id, emoji="🎲")
    value = dice_msg.dice.value
    time.sleep(3.5)

    won = (pick == "low" and value <= 3) or (pick == "high" and value >= 4)
    delta = bet if won else -bet
    new_bal = change_balance(uid, delta)
    log_game(uid, "dice", bet, f"{pick}/{value}", delta)

    outcome = f"🎉 Победа! +{bet}" if delta > 0 else f"😞 Мимо. {delta}"
    sessions.pop(uid, None)
    bot.send_message(
        call.message.chat.id,
        f"Выпало: {value}\n{outcome}\n\nБаланс: {new_bal} 💰",
        reply_markup=main_menu(),
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("dice_exact:"))
def cb_dice_exact(call):
    uid = call.from_user.id
    state = sessions.get(uid, {})
    if state.get("game") != "dice" or "bet" not in state:
        bot.answer_callback_query(call.id, "Ставка потеряна, запусти заново через /menu")
        return

    guess = int(call.data.split(":")[1])
    bet = state["bet"]

    dice_msg = bot.send_dice(call.message.chat.id, emoji="🎲")
    value = dice_msg.dice.value
    time.sleep(3.5)

    if guess == value:
        delta = bet * 5
        outcome = f"🎉 Точно в цель! +{delta}"
    else:
        delta = -bet
        outcome = f"😞 Мимо ({guess} ≠ {value}). {delta}"

    new_bal = change_balance(uid, delta)
    log_game(uid, "dice_exact", bet, f"{guess}/{value}", delta)

    sessions.pop(uid, None)
    bot.send_message(
        call.message.chat.id,
        f"Выпало: {value}\n{outcome}\n\nБаланс: {new_bal} 💰",
        reply_markup=main_menu(),
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("rps:"))
def cb_rps(call):
    uid = call.from_user.id
    state = sessions.get(uid, {})
    if state.get("game") != "rps" or "bet" not in state:
        bot.answer_callback_query(call.id, "Сначала сделай ставку через /menu")
        return

    user_choice = call.data.split(":", 1)[1]
    bot_choice = random.choice(RPS_CHOICES)
    bet = state["bet"]

    if user_choice == bot_choice:
        delta = 0
        verdict = "Ничья 🤝 (ставка возвращена)"
    elif RPS_BEATS[user_choice] == bot_choice:
        delta = bet
        verdict = f"🎉 Ты победил! +{bet}"
    else:
        delta = -bet
        verdict = f"😎 Я победил. {delta}"

    new_bal = change_balance(uid, delta) if delta else get_balance(uid)
    log_game(uid, "rps", bet, f"{user_choice}/{bot_choice}", delta)
    sessions.pop(uid, None)

    bot.edit_message_text(
        f"Ты: {user_choice}\nЯ: {bot_choice}\n\n{verdict}\nБаланс: {new_bal} 💰",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=main_menu(),
    )
    bot.answer_callback_query(call.id)


GUESS_LIMIT = 10


def guess_multiplier(tries):
    if tries == 1:
        return 10
    if tries <= 3:
        return 3
    if tries <= 6:
        return 1
    return 0  # 7-10: возврат ставки


@bot.message_handler(func=lambda m: sessions.get(m.from_user.id, {}).get("game") == "guess")
def guess_handler(message):
    uid = message.from_user.id
    state = sessions[uid]
    if "secret" not in state:
        return
    try:
        guess = int(message.text.strip())
    except (ValueError, AttributeError):
        bot.reply_to(message, "Введи целое число от 1 до 100.")
        return

    state["tries"] += 1
    tries = state["tries"]
    bet = state.get("bet", 0)

    if guess == state["secret"]:
        mult = guess_multiplier(tries)
        delta = mult * bet
        new_bal = change_balance(uid, delta) if delta else get_balance(uid)
        log_game(uid, "guess", bet, f"win/{tries}t", delta)
        sessions.pop(uid, None)
        prize = f" (+{delta} 💰)" if delta > 0 else " (возврат ставки)"
        bot.send_message(
            message.chat.id,
            f"🎉 Угадал за {tries}!{prize}\nБаланс: {new_bal} 💰",
            reply_markup=main_menu(),
        )
        return

    if tries >= GUESS_LIMIT:
        delta = -bet
        new_bal = change_balance(uid, delta)
        log_game(uid, "guess", bet, f"lose/{tries}t", delta)
        secret = state["secret"]
        sessions.pop(uid, None)
        bot.send_message(
            message.chat.id,
            f"💀 Попытки кончились. Я загадал: {secret}. {delta}\nБаланс: {new_bal} 💰",
            reply_markup=main_menu(),
        )
        return

    hint = "Больше ⬆️" if guess < state["secret"] else "Меньше ⬇️"
    bot.reply_to(message, f"{hint}  (попытка {tries}/{GUESS_LIMIT})")


if __name__ == "__main__":
    init_db()
    bot.infinity_polling()

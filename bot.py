import io
import json
import os
import threading
import time
from flask import Flask
from telebot import TeleBot, types

BOT_TOKEN = "9186799921:AAEuUD816t6wBXCf-5HrpUCXn84qkV6ELR4"
DB_UP_CHAT_ID = "-4364950604"  # Канал/чат для закреплённого сообщения DB Up
DATA_FILE = "bot_data.json"
ACTIVE_WINDOW_SECONDS = 7 * 24 * 3600  # Считаем активным, если заходил за последние 7 дней

# Укажи свой Telegram ID (число), например: ADMIN_IDS = {6795794125}
ADMIN_IDS: set[int] = set()

bot = TeleBot(BOT_TOKEN)
app = Flask(__name__)
lock = threading.Lock()


def load_data() -> dict:
    if not os.path.exists(DATA_FILE):
        return {
            "users": {},         # str(user_id) -> dict
            "registrations": [], # list of registration events
            "db_up_msg_id": None
        }
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("users", {})
    data.setdefault("registrations", [])
    data.setdefault("db_up_msg_id", None)
    return data


def save_data(data: dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def is_admin(user_id: int) -> bool:
    return True if not ADMIN_IDS else (user_id in ADMIN_IDS)


def format_user_line(u: dict) -> str:
    uid = u.get("id")
    username = u.get("username")
    full_name = u.get("full_name") or "Без имени"
    tag = f"@{username}" if username else full_name
    return f"user: {tag} ({uid})"


def build_db_up_text(data: dict) -> str:
    users = list(data["users"].values())
    users.sort(key=lambda x: x.get("id", 0))

    lines = ["🟢 DB Up", ""]
    if not users:
        lines.append("user: пока нет пользователей")
    else:
        for u in users:
            lines.append(format_user_line(u))
    return "\n".join(lines)


def update_and_pin_db_up(data: dict) -> None:
    text = build_db_up_text(data)
    msg_id = data.get("db_up_msg_id")

    try:
        if msg_id:
            bot.edit_message_text(
                chat_id=DB_UP_CHAT_ID,
                message_id=msg_id,
                text=text
            )
        else:
            sent = bot.send_message(chat_id=DB_UP_CHAT_ID, text=text)
            data["db_up_msg_id"] = sent.message_id
            save_data(data)
            try:
                bot.pin_chat_message(
                    chat_id=DB_UP_CHAT_ID,
                    message_id=sent.message_id,
                    disable_notification=True
                )
            except Exception:
                pass
    except Exception:
        # Если старое сообщение было удалено или ещё не создано — отправляем и закрепляем заново
        try:
            sent = bot.send_message(chat_id=DB_UP_CHAT_ID, text=text)
            data["db_up_msg_id"] = sent.message_id
            save_data(data)
            bot.pin_chat_message(
                chat_id=DB_UP_CHAT_ID,
                message_id=sent.message_id,
                disable_notification=True
            )
        except Exception:
            pass


def touch_user(tg_user: types.User, is_new_reg: bool = False) -> dict:
    now = int(time.time())
    uid_str = str(tg_user.id)

    with lock:
        data = load_data()
        existing = data["users"].get(uid_str)

        full_name = " ".join(filter(None, [tg_user.first_name, tg_user.last_name])).strip()
        if not existing:
            existing = {
                "id": tg_user.id,
                "username": tg_user.username or "",
                "full_name": full_name,
                "registered_at": now,
                "last_seen": now,
                "active": True
            }
            data["users"][uid_str] = existing
            data["registrations"].append({
                "id": tg_user.id,
                "username": tg_user.username or "",
                "full_name": full_name,
                "registered_at": now
            })
        else:
            existing["username"] = tg_user.username or existing.get("username", "")
            existing["full_name"] = full_name or existing.get("full_name", "")
            existing["last_seen"] = now
            existing["active"] = True
            if is_new_reg:
                data["registrations"].append({
                    "id": tg_user.id,
                    "username": existing["username"],
                    "full_name": existing["full_name"],
                    "registered_at": now
                })

        save_data(data)
        update_and_pin_db_up(data)
        return existing


def get_stats_text(data: dict) -> str:
    now = int(time.time())
    users = list(data["users"].values())
    total = len(users)

    active_users = []
    inactive_users = []
    for u in users:
        last_seen = u.get("last_seen", 0)
        is_recent = (now - last_seen) <= ACTIVE_WINDOW_SECONDS and u.get("active", True)
        if is_recent:
            active_users.append(u)
        else:
            inactive_users.append(u)

    return (
        "📊 Статистика пользователей\n"
        f"• Всего пользователей: {total}\n"
        f"• Активных: {len(active_users)}\n"
        f"• Неактивных: {len(inactive_users)}\n"
        f"• Всего регистраций в логе: {len(data['registrations'])}"
    )


@app.route("/")
def health():
    return "Bot is alive", 200


@bot.message_handler(commands=["start"])
def cmd_start(message: types.Message):
    touch_user(message.from_user, is_new_reg=False)
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("✅ Подтвердить регистрацию", callback_data="reg_confirm"),
        types.InlineKeyboardButton("❌ Отмена", callback_data="reg_cancel")
    )
    bot.send_message(
        message.chat.id,
        "Привет! Подтверди регистрацию кнопкой ниже:",
        reply_markup=markup
    )


@bot.callback_query_handler(func=lambda call: call.data in ("reg_confirm", "reg_cancel"))
def handle_reg_callback(call: types.CallbackQuery):
    if call.data == "reg_cancel":
        bot.edit_message_text(
            "Регистрация отменена.",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id
        )
        return

    touch_user(call.from_user, is_new_reg=True)
    bot.edit_message_text(
        "✅ Регистрация подтверждена! Ты добавлен в список.",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id
    )


@bot.message_handler(commands=["admin"])
def cmd_admin(message: types.Message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "Нет доступа.")
        return

    touch_user(message.from_user)
    text = (
        "🛠 Админ-команды:\n"
        "/users — экспорт всех пользователей\n"
        "/export_reg — экспорт регистраций\n"
        "/stats — статистика (активные / неактивные)\n"
        "/dbup — обновить и закрепить DB Up"
    )
    bot.send_message(message.chat.id, text)


@bot.message_handler(commands=["users"])
def cmd_users(message: types.Message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "Нет доступа.")
        return

    with lock:
        data = load_data()
        users = list(data["users"].values())

    if not users:
        bot.send_message(message.chat.id, "Пользователей пока нет.")
        return

    lines = [format_user_line(u) for u in users]
    content = "\n".join(lines).encode("utf-8")
    doc = io.BytesIO(content)
    doc.name = "users_export.txt"
    bot.send_document(message.chat.id, doc, caption=f"Все пользователи ({len(users)})")


@bot.message_handler(commands=["export_reg"])
def cmd_export_reg(message: types.Message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "Нет доступа.")
        return

    with lock:
        data = load_data()
        regs = data["registrations"]

    if not regs:
        bot.send_message(message.chat.id, "Регистраций пока нет.")
        return

    lines = []
    for idx, r in enumerate(regs, start=1):
        tag = f"@{r['username']}" if r.get("username") else (r.get("full_name") or "Без имени")
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r.get("registered_at", 0)))
        lines.append(f"{idx}. user: {tag} ({r.get('id')}) | {ts}")

    content = "\n".join(lines).encode("utf-8")
    doc = io.BytesIO(content)
    doc.name = "registrations_export.txt"
    bot.send_document(message.chat.id, doc, caption=f"Экспорт регистраций ({len(regs)})")


@bot.message_handler(commands=["stats"])
def cmd_stats(message: types.Message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "Нет доступа.")
        return

    with lock:
        data = load_data()
        text = get_stats_text(data)

    bot.send_message(message.chat.id, text)


@bot.message_handler(commands=["dbup"])
def cmd_dbup(message: types.Message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "Нет доступа.")
        return

    with lock:
        data = load_data()
        update_and_pin_db_up(data)

    bot.send_message(message.chat.id, "DB Up обновлён и закреплён.")


@bot.message_handler(func=lambda m: True)
def handle_any_message(message: types.Message):
    touch_user(message.from_user, is_new_reg=False)


def run_polling():
    with lock:
        data = load_data()
        update_and_pin_db_up(data)
    bot.infinity_polling(skip_pending=True, timeout=20, long_polling_timeout=20)


threading.Thread(target=run_polling, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


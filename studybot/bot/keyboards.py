from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def due_keyboard(card_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Reveal", callback_data=f"reveal:{card_id}")],
        [
            InlineKeyboardButton("⏰ 1h", callback_data=f"snooze:1h:{card_id}"),
            InlineKeyboardButton("🌙 Tonight", callback_data=f"snooze:tonight:{card_id}"),
            InlineKeyboardButton("📅 Tomorrow", callback_data=f"snooze:tomorrow:{card_id}"),
        ],
    ])


def answer_keyboard(card_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔴 Again", callback_data=f"ans:1:{card_id}"),
        InlineKeyboardButton("🟠 Hard", callback_data=f"ans:3:{card_id}"),
        InlineKeyboardButton("🟢 Good", callback_data=f"ans:4:{card_id}"),
        InlineKeyboardButton("🔵 Easy", callback_data=f"ans:5:{card_id}"),
    ]])

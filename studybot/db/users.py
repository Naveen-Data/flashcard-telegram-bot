"""Canonical users and Telegram identity linking."""
from sqlalchemy import text
from studybot.db.connection import get_connection

def get_or_create_telegram_user(telegram_user_id: int, telegram_chat_id: int, telegram_username: str | None, display_name: str | None) -> int:
    with get_connection() as conn:
        uid = conn.execute(text("SELECT user_id FROM telegram_accounts WHERE telegram_user_id=:tid"), {"tid":telegram_user_id}).scalar()
        if uid is not None:
            conn.execute(text("UPDATE telegram_accounts SET telegram_chat_id=:cid,telegram_username=:name,updated_at=now() WHERE telegram_user_id=:tid"), {"cid":telegram_chat_id,"name":telegram_username,"tid":telegram_user_id})
            return int(uid)
        uid = conn.execute(text("INSERT INTO users(display_name) VALUES(:name) RETURNING id"), {"name":display_name}).scalar_one()
        conn.execute(text("INSERT INTO telegram_accounts(user_id,telegram_user_id,telegram_chat_id,telegram_username) VALUES(:uid,:tid,:cid,:name)"), {"uid":uid,"tid":telegram_user_id,"cid":telegram_chat_id,"name":telegram_username})
        return int(uid)

def get_user_id_for_telegram(telegram_user_id: int, telegram_chat_id: int) -> int | None:
    with get_connection() as conn:
        value = conn.execute(text("SELECT user_id FROM telegram_accounts WHERE telegram_user_id=:tid AND telegram_chat_id=:cid"), {"tid":telegram_user_id,"cid":telegram_chat_id}).scalar()
        return int(value) if value is not None else None

def list_notification_targets() -> list[dict]:
    with get_connection() as conn:
        return [dict(row) for row in conn.execute(text("SELECT user_id,id AS telegram_account_id,telegram_chat_id FROM telegram_accounts WHERE telegram_chat_id IS NOT NULL")).mappings()]

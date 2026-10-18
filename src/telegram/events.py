from src.application.validation import safe_html
from src.infrastructure.database.models import (
    ConversationMessage,
    ExpertApplication,
    ExpertProfile,
    User,
    WithdrawalRequest,
)


def _safe_limit(value: str, limit: int) -> str:
    result: list[str] = []
    length = 0
    for char in value:
        escaped = safe_html(char)
        if length + len(escaped) > limit - 1:
            result.append("…")
            break
        result.append(escaped)
        length += len(escaped)
    return "".join(result)


def user_registered(user: User) -> str:
    username = f"@{safe_html(user.username)}" if user.username else "отсутствует"
    referrer = str(user.referrer_id) if user.referrer_id else "отсутствует"
    return (
        "#UserRegisteredEvent\n\n"
        "<b>🔅 Событие: Новый пользователь!</b>\n\n"
        "<b>👤 Пользователь:</b>\n<blockquote>"
        f"• <b>ID</b>: <code>{user.telegram_id}</code>\n"
        f"• <b>Username</b>: {username}\n"
        f"• <b>Имя в Telegram</b>: {safe_html(user.telegram_name)}\n"
        f"• <b>ФИО</b>: {safe_html(user.full_name or 'не указано')}\n"
        f"• <b>Телефон</b>: {safe_html(user.phone or 'не указан')}\n"
        f"• <b>Дата регистрации</b>: {user.created_at:%d.%m.%Y %H:%M}\n"
        f"• <b>Пригласитель</b>: {referrer}"
        "</blockquote>"
    )


def expert_application(application: ExpertApplication, user: User) -> str:
    return (
        "#ExpertApplicationEvent\n\n"
        "<b>🔅 Событие: Новая заявка эксперта!</b>\n\n"
        "<blockquote>"
        f"• <b>Пользователь</b>: {safe_html(user.full_name or user.telegram_name)}\n"
        f"• <b>Telegram ID</b>: <code>{user.telegram_id}</code>\n"
        f"• <b>ФИО эксперта</b>: {safe_html(application.full_name)}\n"
        f"• <b>Специализация</b>: {safe_html(application.specialization)}\n"
        f"• <b>Опыт</b>: {_safe_limit(application.short_bio, 220)}"
        "</blockquote>\n\n"
        f"{_safe_limit(application.bio, 300)}"
    )


def new_message(message: ConversationMessage, client: User) -> str:
    username = f"@{safe_html(client.username)}" if client.username else f"ID {client.telegram_id}"
    return (
        "#NewMessageEvent\n\n"
        "<b>🔅 Событие: Новое сообщение!</b>\n\n"
        "<b>👤 Клиент:</b>\n<blockquote>"
        f"• <b>ФИО</b>: {safe_html(client.full_name or client.telegram_name)}\n"
        f"• <b>Телефон</b>: {safe_html(client.phone or 'не указан')}\n"
        f"• <b>Telegram</b>: {username}"
        "</blockquote>\n\n"
        "<b>💬 Сообщение:</b>\n"
        f"<blockquote>{safe_html(message.text)}</blockquote>"
    )


def answer_ready(expert: ExpertProfile) -> str:
    return f"Эксперт <b>{safe_html(expert.display_name)}</b> ответил на ваш вопрос."


def withdrawal_requested(request: WithdrawalRequest, expert: ExpertProfile, user: User) -> str:
    return (
        "#WithdrawalRequestedEvent\n\n"
        "<b>🔅 Событие: Запрос на вывод средств!</b>\n\n"
        "<blockquote>"
        f"• <b>Эксперт</b>: {safe_html(expert.display_name)}\n"
        f"• <b>Telegram ID</b>: <code>{user.telegram_id}</code>\n"
        f"• <b>Телефон</b>: {safe_html(request.phone_snapshot)}\n"
        f"• <b>Банк</b>: {safe_html(request.bank_name)}\n"
        f"• <b>Сумма</b>: {request.amount:.2f} ₽\n"
        f"• <b>Дата</b>: {request.created_at:%d.%m.%Y %H:%M}"
        "</blockquote>"
    )

from src.application.dto import MenuDescriptor
from src.core.enums import Role
from src.core.exceptions import AccessDenied


def require_role(actual: Role, *allowed: Role) -> None:
    if actual not in allowed:
        raise AccessDenied("Недостаточно прав для выполнения операции")


def menu_for_role(role: Role, *, can_message: bool = False) -> MenuDescriptor:
    if role is Role.OWNER:
        return MenuDescriptor(
            role=role,
            title="⚙️ Панель владельца",
            buttons=(
                "Статистика",
                "Пользователи",
                "Эксперты",
                "Заявки экспертов",
                "Тарифы",
                "Подписки",
                "Транзакции",
                "Платежные системы",
                "Комиссии",
                "Заявки на вывод",
                "Рассылки",
                "Реферальная система",
                "Уведомления",
                "Настройки",
            ),
        )
    if role is Role.EXPERT:
        return MenuDescriptor(
            role=role,
            title="🩺 Кабинет эксперта",
            buttons=(
                "Новые сообщения",
                "Диалоги",
                "Мои тарифы",
                "Мой профиль",
                "Статистика",
                "Баланс",
                "Вывести средства",
                "Поддержка",
            ),
        )
    buttons = [
        "Получить доступ к эксперту",
        "Стать экспертом",
        "Мои подписки",
        "Реферальная программа",
        "Поддержка",
    ]
    return MenuDescriptor(role=role, title="Добро пожаловать!", buttons=tuple(buttons))

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.validation import safe_html
from src.core.enums import ReferralAccrual, ReferralRewardKind
from src.core.exceptions import ValidationError
from src.infrastructure.database.models import AuditLog, ReferralReward, Settings, User
from src.telegram.states import OwnerReferralForm

from .context import OwnerRouterContext


def keyboard(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=data) for text, data in row]
            for row in rows
        ]
    )


def build_owner_referrals_router(context: OwnerRouterContext) -> Router:
    router = Router(name="owner-referrals")

    async def render(event: Message | CallbackQuery, session: AsyncSession) -> None:
        settings = await session.get(Settings, 1)
        if settings is None:
            raise ValidationError("Настройки не найдены")
        reward_suffix = (
            "% от оплаты" if settings.referral_reward_kind is ReferralRewardKind.PERCENT else "₽"
        )
        accrual = (
            "только с первой оплаты"
            if settings.referral_accrual is ReferralAccrual.FIRST_PAYMENT
            else "с каждой оплаты"
        )
        rows = [
            [
                (
                    "🟢 Система включена" if settings.referral_enabled else "🔴 Система выключена",
                    "owner:ref_toggle",
                )
            ],
            [(f"Уровней: {settings.referral_levels}", "owner:ref_levels")],
            [(f"Начисление: {accrual}", "owner:ref_accrual")],
            [
                (
                    "Награда: процент"
                    if settings.referral_reward_kind is ReferralRewardKind.PERCENT
                    else "Награда: фиксированная",
                    "owner:ref_kind",
                )
            ],
            [
                (
                    f"1 уровень: {settings.referral_level_1:g} {reward_suffix}",
                    "owner:ref_value:1",
                )
            ],
        ]
        if settings.referral_levels == 2:
            rows.append(
                [
                    (
                        f"2 уровень: {settings.referral_level_2:g} {reward_suffix}",
                        "owner:ref_value:2",
                    )
                ]
            )
        rows.extend(
            [
                [("📊 Статистика", "owner:ref_stats")],
                [("🕓 Последние начисления", "owner:ref_recent")],
                [("🏆 Топ рефереров", "owner:ref_top")],
                [("⬅️ В панель управления", "owner:panel")],
            ]
        )
        await context.show(
            event,
            "<b>👥 Реферальная система</b>\n\n"
            "Настройки применяются только к новым начислениям. "
            "Ранее созданные связи и начисления сохраняются.",
            keyboard(rows),
        )

    @router.callback_query(F.data == "owner:referrals")
    async def referrals(callback: CallbackQuery, state: FSMContext) -> None:
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            await render(callback, session)
        await state.clear()
        await callback.answer()

    toggle_callbacks = {
        "owner:ref_toggle",
        "owner:ref_levels",
        "owner:ref_accrual",
        "owner:ref_kind",
    }

    @router.callback_query(F.data.in_(toggle_callbacks))
    async def toggle(callback: CallbackQuery) -> None:
        action = callback.data or ""
        async with context.session_factory() as session:
            owner = await context.owner(session, callback.from_user.id)
            settings = await session.get(Settings, 1, with_for_update=True)
            if settings is None:
                raise ValidationError("Настройки не найдены")
            before: str | int | bool
            after: str | int | bool
            if action == "owner:ref_toggle":
                before = settings.referral_enabled
                settings.referral_enabled = not settings.referral_enabled
                after = settings.referral_enabled
            elif action == "owner:ref_levels":
                before = settings.referral_levels
                settings.referral_levels = 2 if settings.referral_levels == 1 else 1
                after = settings.referral_levels
            elif action == "owner:ref_accrual":
                before = settings.referral_accrual.value
                settings.referral_accrual = (
                    ReferralAccrual.EVERY_PAYMENT
                    if settings.referral_accrual is ReferralAccrual.FIRST_PAYMENT
                    else ReferralAccrual.FIRST_PAYMENT
                )
                after = settings.referral_accrual.value
            else:
                before = settings.referral_reward_kind.value
                settings.referral_reward_kind = (
                    ReferralRewardKind.FIXED
                    if settings.referral_reward_kind is ReferralRewardKind.PERCENT
                    else ReferralRewardKind.PERCENT
                )
                after = settings.referral_reward_kind.value
            session.add(
                AuditLog(
                    actor_id=owner.id,
                    action=f"referral.{action.rsplit(':', 1)[1]}",
                    entity_type="settings",
                    entity_id="1",
                    data={"before": before, "after": after},
                )
            )
            await session.commit()
            await render(callback, session)
        await callback.answer("Настройка изменена")

    @router.callback_query(F.data.startswith("owner:ref_value:"))
    async def value_start(callback: CallbackQuery, state: FSMContext) -> None:
        level = int((callback.data or "").rsplit(":", 1)[1])
        if level not in {1, 2}:
            raise ValidationError("Неизвестный уровень")
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            settings = await session.get(Settings, 1)
            if settings is None:
                raise ValidationError("Настройки не найдены")
            unit = (
                "процент"
                if settings.referral_reward_kind is ReferralRewardKind.PERCENT
                else "сумму в рублях"
            )
        await state.clear()
        await state.update_data(referral_level=level)
        await state.set_state(OwnerReferralForm.value)
        await context.show(
            callback,
            f"Введите {unit} награды для {level}-го уровня:",
            keyboard([[("⬅️ Назад", "owner:referrals")]]),
        )
        await callback.answer()

    @router.message(OwnerReferralForm.value, F.text)
    async def value_preview(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        try:
            value = Decimal((message.text or "").replace(",", "."))
        except InvalidOperation as error:
            raise ValidationError("Введите корректное число") from error
        data = await state.get_data()
        level = int(data["referral_level"])
        async with context.session_factory() as session:
            await context.owner(session, message.from_user.id)
            settings = await session.get(Settings, 1)
            if settings is None:
                raise ValidationError("Настройки не найдены")
            if value < 0:
                raise ValidationError("Значение не может быть отрицательным")
            if settings.referral_reward_kind is ReferralRewardKind.PERCENT and value > 100:
                raise ValidationError("Процент должен быть от 0 до 100")
        value = value.quantize(Decimal("0.001"))
        await state.update_data(referral_value=str(value))
        await state.set_state(OwnerReferralForm.confirm)
        await context.show(
            message,
            f"Сохранить награду {level}-го уровня: <b>{value:g}</b>?",
            keyboard(
                [
                    [("✅ Сохранить", "owner:ref_value_confirm")],
                    [("⬅️ Назад", f"owner:ref_value:{level}")],
                    [("❌ Отменить", "owner:referrals")],
                ]
            ),
        )

    @router.callback_query(OwnerReferralForm.confirm, F.data == "owner:ref_value_confirm")
    async def value_save(callback: CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        level = int(data["referral_level"])
        value = Decimal(str(data["referral_value"]))
        async with context.session_factory() as session:
            owner = await context.owner(session, callback.from_user.id)
            settings = await session.get(Settings, 1, with_for_update=True)
            if settings is None:
                raise ValidationError("Настройки не найдены")
            previous = settings.referral_level_1 if level == 1 else settings.referral_level_2
            if level == 1:
                settings.referral_level_1 = value
            else:
                settings.referral_level_2 = value
            session.add(
                AuditLog(
                    actor_id=owner.id,
                    action="referral.value_change",
                    entity_type="settings",
                    entity_id="1",
                    data={"level": level, "before": str(previous), "after": str(value)},
                )
            )
            await session.commit()
            await state.clear()
            await render(callback, session)
        await callback.answer("Настройка сохранена")

    @router.callback_query(F.data == "owner:ref_stats")
    async def stats(callback: CallbackQuery) -> None:
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            invited = int(
                await session.scalar(
                    select(func.count(User.id)).where(User.referrer_id.is_not(None))
                )
                or 0
            )
            rewarded_users = int(
                await session.scalar(
                    select(func.count(func.distinct(ReferralReward.referred_id))).where(
                        ReferralReward.reversed_at.is_(None)
                    )
                )
                or 0
            )
            rewards_count, rewards_sum = (
                await session.execute(
                    select(
                        func.count(ReferralReward.id),
                        func.coalesce(func.sum(ReferralReward.amount), 0),
                    ).where(ReferralReward.reversed_at.is_(None))
                )
            ).one()
            reversed_sum = await session.scalar(
                select(func.coalesce(func.sum(ReferralReward.amount), 0)).where(
                    ReferralReward.reversed_at.is_not(None)
                )
            )
            await context.show(
                callback,
                "<b>📊 Статистика рефералов</b>\n\n"
                f"• Приглашено пользователей: {invited}\n"
                f"• Совершили оплату: {rewarded_users}\n"
                f"• Активных начислений: {rewards_count}\n"
                f"• Начислено: {Decimal(rewards_sum or 0):.2f} ₽\n"
                f"• Отменено возвратами: {Decimal(reversed_sum or 0):.2f} ₽",
                keyboard([[("⬅️ Назад", "owner:referrals")]]),
            )
        await callback.answer()

    @router.callback_query(F.data == "owner:ref_recent")
    async def recent(callback: CallbackQuery) -> None:
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            rewards = list(
                await session.scalars(
                    select(ReferralReward).order_by(ReferralReward.created_at.desc()).limit(20)
                )
            )
            lines = ["<b>🕓 Последние реферальные начисления</b>"]
            for reward in rewards:
                referrer = await session.get(User, reward.referrer_id)
                referred = await session.get(User, reward.referred_id)
                referrer_name = safe_html(
                    (referrer.full_name or referrer.telegram_name) if referrer else "—"
                )
                referred_name = safe_html(
                    (referred.full_name or referred.telegram_name) if referred else "—"
                )
                lines.append(
                    f"\n• {referrer_name} ← {referred_name}: {reward.amount:.2f} ₽, "
                    f"уровень {reward.level}{' (отменено)' if reward.reversed_at else ''}"
                )
            if not rewards:
                lines.append("\n\nНачислений пока нет.")
            await context.show(
                callback,
                "".join(lines),
                keyboard([[("⬅️ Назад", "owner:referrals")]]),
            )
        await callback.answer()

    @router.callback_query(F.data == "owner:ref_top")
    async def top(callback: CallbackQuery) -> None:
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            rows = (
                await session.execute(
                    select(
                        ReferralReward.referrer_id,
                        func.count(ReferralReward.id),
                        func.coalesce(func.sum(ReferralReward.amount), 0),
                    )
                    .where(ReferralReward.reversed_at.is_(None))
                    .group_by(ReferralReward.referrer_id)
                    .order_by(func.sum(ReferralReward.amount).desc())
                    .limit(20)
                )
            ).all()
            lines = ["<b>🏆 Топ рефереров</b>"]
            for position, (user_id, count, total) in enumerate(rows, start=1):
                user = await session.get(User, user_id)
                name = safe_html((user.full_name or user.telegram_name) if user else "—")
                lines.append(f"\n{position}. {name} — {count} начисл., {Decimal(total or 0):.2f} ₽")
            if not rows:
                lines.append("\n\nНачислений пока нет.")
            await context.show(
                callback,
                "".join(lines),
                keyboard([[("⬅️ Назад", "owner:referrals")]]),
            )
        await callback.answer()

    return router

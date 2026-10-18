from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from src.application.services import MessagingService, notification_enabled
from src.application.validation import safe_html, validate_text
from src.core.enums import ConversationTopicSide, ConversationTopicStatus, MessageStatus, Role
from src.core.exceptions import ValidationError
from src.infrastructure.database.models import (
    Conversation,
    ConversationMessage,
    ConversationTopic,
    ExpertProfile,
    User,
)
from src.infrastructure.telegram_notifications import send_notification
from src.telegram import events
from src.telegram.routers.context import FeatureRouterContext
from src.telegram.routers.helpers import back_menu, keyboard
from src.telegram.states import ClientQuestion, ExpertAnswer


def build_consultations_router(context: FeatureRouterContext) -> Router:
    router = Router(name="build_consultations_router")

    @router.callback_query(F.data == "client:message")
    async def legacy_consultations(callback: CallbackQuery) -> None:
        await context.show(
            callback,
            "Откройте нужную консультацию в списке тем Telegram. "
            "Переписка с каждым экспертом находится в отдельной теме.",
            back_menu(),
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("client:conversation:"))
    async def client_show_topic(callback: CallbackQuery, bot: Bot) -> None:
        conversation_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            user = await context.user(session, callback.from_user.id)
            conversation = await session.get(Conversation, conversation_id)
            if conversation is None or conversation.client_id != user.id:
                raise ValidationError("Консультация не найдена")
            topic = await session.scalar(
                select(ConversationTopic).where(
                    ConversationTopic.conversation_id == conversation.id,
                    ConversationTopic.side == ConversationTopicSide.CLIENT,
                )
            )
            if (
                topic is not None
                and topic.status is ConversationTopicStatus.READY
                and topic.message_thread_id is not None
            ):
                await send_notification(
                    bot,
                    chat_id=topic.telegram_chat_id,
                    message_thread_id=topic.message_thread_id,
                    text="📌 Продолжайте консультацию в этой теме.",
                )
                await callback.answer("Тема поднята в списке чатов", show_alert=True)
                return
        from src.infrastructure.taskiq.tasks import (  # noqa: PLC0415
            provision_conversation_topics_task,
        )

        await provision_conversation_topics_task.kiq(conversation_id)
        await callback.answer("Создание темы поставлено в очередь", show_alert=True)

    @router.callback_query(F.data.startswith("client:ask:"))
    async def client_question_start(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await context.show(
            callback,
            "Переписка перенесена в отдельные Telegram-темы. "
            "Откройте тему нужного эксперта в списке тем Telegram и напишите прямо в ней.",
            back_menu(),
        )
        await callback.answer("Используйте тему консультации")

    @router.message(ClientQuestion.text, F.text)
    async def client_question_preview(message: Message, state: FSMContext) -> None:
        text = validate_text(message.text or "")
        await state.update_data(text=text)
        await state.set_state(ClientQuestion.confirm)
        await context.show(
            message,
            f"<b>Проверьте сообщение</b>\n\n<blockquote>{safe_html(text)}</blockquote>",
            keyboard(
                [
                    [("✅ Отправить", "question:confirm")],
                    [("✏️ Изменить", "question:edit"), ("❌ Отменить", "menu")],
                ]
            ),
        )

    @router.callback_query(ClientQuestion.confirm, F.data == "question:edit")
    async def client_question_edit(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(ClientQuestion.text)
        await context.show(callback, "Введите сообщение заново:")
        await callback.answer()

    @router.callback_query(ClientQuestion.confirm, F.data == "question:confirm")
    async def client_question_send(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
        data = await state.get_data()
        async with context.session_factory() as session:
            user = await context.user(session, callback.from_user.id)
            message = await MessagingService(session).send_question(
                user, int(data["expert_id"]), str(data["text"])
            )
            expert = await session.get(ExpertProfile, int(data["expert_id"]))
            expert_user = await session.get(User, expert.user_id if expert else 0)
            if expert_user and await notification_enabled(session, "new_message"):
                await send_notification(
                    bot,
                    expert_user.telegram_id,
                    events.new_message(message, user),
                    reply_markup=keyboard(
                        [
                            [("✍️ Ответить", f"expert:reply:{message.id}")],
                            [("📖 Открыть историю", f"dialog:history:{message.conversation_id}")],
                            [("👤 Открыть клиента", f"expert:client:{user.id}")],
                        ]
                    ),
                )
                await MessagingService(session).mark_delivered(message.id)
        await state.clear()
        await context.show(callback, "Сообщение отправлено эксперту.", back_menu())
        await callback.answer()

    @router.callback_query(F.data.startswith("expert:reply:"))
    async def expert_reply_start(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
        message_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            actor = await context.user(session, callback.from_user.id)
            if actor.role is not Role.EXPERT:
                raise ValidationError("Отвечать может только эксперт")
            source_message = await session.get(ConversationMessage, message_id)
            if source_message is None:
                raise ValidationError("Сообщение не найдено")
            topic = await session.scalar(
                select(ConversationTopic).where(
                    ConversationTopic.conversation_id == source_message.conversation_id,
                    ConversationTopic.side == ConversationTopicSide.EXPERT,
                    ConversationTopic.participant_user_id == actor.id,
                )
            )
        await state.clear()
        if topic and topic.status is ConversationTopicStatus.READY and topic.message_thread_id:
            await send_notification(
                bot,
                chat_id=topic.telegram_chat_id,
                message_thread_id=topic.message_thread_id,
                text="📌 Ответьте клиенту прямо в этой теме.",
            )
            await callback.answer("Тема клиента поднята в списке", show_alert=True)
            return
        from src.infrastructure.taskiq.tasks import (  # noqa: PLC0415
            provision_conversation_topics_task,
        )

        await provision_conversation_topics_task.kiq(source_message.conversation_id)
        await callback.answer("Тема создаётся, попробуйте снова немного позже", show_alert=True)

    @router.message(ExpertAnswer.text, F.text)
    async def expert_answer_preview(message: Message, state: FSMContext) -> None:
        text = validate_text(message.text or "")
        await state.update_data(text=text)
        await state.set_state(ExpertAnswer.confirm)
        await context.show(
            message,
            f"<b>Проверьте ответ</b>\n\n<blockquote>{safe_html(text)}</blockquote>",
            keyboard([[("✅ Отправить", "answer:confirm")], [("❌ Отменить", "menu")]]),
        )

    @router.callback_query(ExpertAnswer.confirm, F.data == "answer:confirm")
    async def expert_answer_send(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
        data = await state.get_data()
        async with context.session_factory() as session:
            actor = await context.user(session, callback.from_user.id)
            answer = await MessagingService(session).reply(
                actor, int(data["question_id"]), str(data["text"])
            )
            conversation = await session.get(Conversation, answer.conversation_id)
            client = await session.get(User, conversation.client_id if conversation else 0)
            expert = await session.scalar(
                select(ExpertProfile).where(ExpertProfile.user_id == actor.id)
            )
            if client and expert and await notification_enabled(session, "answer"):
                await send_notification(
                    bot,
                    client.telegram_id,
                    events.answer_ready(expert),
                    reply_markup=keyboard([[("📨 Открыть ответ", f"client:answer:{answer.id}")]]),
                )
                await MessagingService(session).mark_delivered(answer.id)
        await state.clear()
        await context.show(callback, "Ответ отправлен клиенту.", back_menu())
        await callback.answer()

    @router.callback_query(F.data.startswith("client:answer:"))
    async def client_open_answer(callback: CallbackQuery) -> None:
        answer_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            actor = await context.user(session, callback.from_user.id)
            answer = await session.get(ConversationMessage, answer_id)
            question = await session.get(ConversationMessage, answer.reply_to_id if answer else 0)
            conversation = await session.get(Conversation, answer.conversation_id if answer else 0)
            if not answer or not question or not conversation or conversation.client_id != actor.id:
                raise ValidationError("Ответ не найден")
            answer.status = MessageStatus.READ
            from src.application.services import utcnow  # noqa: PLC0415

            answer.read_at = utcnow()
            await session.commit()
            await context.show(
                callback,
                "<b>Ваш вопрос:</b>\n"
                f"<blockquote>{safe_html(question.text)}</blockquote>\n\n"
                "<b>Ответ эксперта:</b>\n"
                f"<blockquote>{safe_html(answer.text)}</blockquote>",
                back_menu(),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("dialog:history:"))
    async def dialog_history(callback: CallbackQuery) -> None:
        conversation_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            actor = await context.user(session, callback.from_user.id)
            history = await MessagingService(session).history(actor, conversation_id)
            current = "<b>История диалога</b>\n\n"
            omitted = 0
            for item in reversed(history):
                author = "Клиент" if item.sender_role is Role.CLIENT else "Эксперт"
                line = f"<b>{author}:</b> {safe_html(item.text)}\n\n"
                if len(current) + len(line) > 3900:
                    omitted += 1
                else:
                    current = line + current.removeprefix("<b>История диалога</b>\n\n")
                    current = "<b>История диалога</b>\n\n" + current
            if not history:
                current += "История пуста"
            elif omitted:
                current += f"<i>Не показано старых сообщений: {omitted}</i>"
            history_markup = back_menu()
            if actor.role is Role.EXPERT:
                last_question = next(
                    (item for item in reversed(history) if item.sender_role is Role.CLIENT),
                    None,
                )
                if last_question:
                    history_markup = keyboard(
                        [
                            [("✍️ Написать клиенту", f"expert:reply:{last_question.id}")],
                            [("⬅️ Главное меню", "menu")],
                        ]
                    )
            await context.show(callback, current, history_markup)
        await callback.answer()

    return router

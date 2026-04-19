import structlog
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from bot.handlers.basic import help_command, start
from bot.handlers.query import birthday_command, upcoming_command
from bot.handlers.weight import weight_command
from bot.scheduler import create_scheduler, rebuild_upcoming_async
from shared.config import settings

log = structlog.get_logger()


async def _handle_korean_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """한글 명령어 라우팅."""
    if update.message is None or not update.message.text:
        return

    # 그룹/채널 ID 로그
    chat_id = update.message.chat_id
    chat_type = update.message.chat.type
    log.info(
        "메시지 수신",
        chat_id=chat_id,
        chat_type=chat_type,
        text=update.message.text[:30],
    )

    text = update.message.text
    if text.startswith("/몸무게"):
        # /몸무게로 시작하면 weight_command 호출
        context.args = text[4:].strip().split() if len(text) > 4 else []
        await weight_command(update, context)
    elif text.startswith("/다음일정"):
        await upcoming_command(update, context)
    elif text.startswith("/내생일"):
        await birthday_command(update, context)


def main() -> None:
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ]
    )

    scheduler = create_scheduler()

    app = Application.builder().token(settings.telegram_bot_token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    # 한글 명령어 처리 (MessageHandler 사용)
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            _handle_korean_command,
        )
    )

    async def on_startup(app: Application) -> None:  # type: ignore[type-arg]
        scheduler.start()
        # 시작 시 1회 rebuild — DB에 예정 알림 채우기
        await rebuild_upcoming_async()
        log.info("KinKeeper 봇 시작", mode="polling")

    async def on_shutdown(app: Application) -> None:  # type: ignore[type-arg]
        scheduler.shutdown(wait=False)
        log.info("KinKeeper 봇 종료")

    app.post_init = on_startup
    app.post_shutdown = on_shutdown

    log.info("polling 시작")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

import structlog
from telegram.ext import Application, CommandHandler

from bot.handlers.basic import help_command, start
from bot.handlers.query import birthday_command, upcoming_command
from bot.handlers.weight import weight_command
from bot.scheduler import create_scheduler, rebuild_upcoming_async
from shared.config import settings

log = structlog.get_logger()


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
    app.add_handler(CommandHandler("몸무게", weight_command))
    app.add_handler(CommandHandler("다음일정", upcoming_command))
    app.add_handler(CommandHandler("내생일", birthday_command))

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

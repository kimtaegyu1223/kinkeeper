from telegram import Update
from telegram.ext import ContextTypes

from shared.config import settings


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(
        "안녕하세요! 👨‍👩‍👧‍👦 <b>KinKeeper</b>입니다.\n\n"
        "가족 알림 시스템에 연결됐습니다.\n"
        "사용 가능한 명령어를 보려면 /help 를 입력하세요.",
        parse_mode="HTML",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    commands = [
        "<b>KinKeeper 명령어</b>",
        "",
        "/start — 시작",
        "/help — 명령어 목록",
    ]
    if settings.weight_feature_enabled:
        commands.append("/몸무게 [숫자] — 몸무게 기록 (예: /몸무게 67.2)")
    commands.extend(
        [
            "/다음일정 [일수] — 예정 알림 확인 (예: /다음일정 60)",
            "",
            "일정 추가/수정은 관리자 웹에서 합니다.",
        ]
    )
    await update.message.reply_text(
        "\n".join(commands),
        parse_mode="HTML",
    )

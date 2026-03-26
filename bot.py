from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

EKB_TZ = ZoneInfo("Asia/Yekaterinburg")

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import Config
from storage import Storage
from telegraph_api import TelegraphClient, build_article_content

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.DEBUG,
)
logger = logging.getLogger(__name__)

MONTHS_RU = [
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [["📋 Итог дня", "📊 Счётчик", "🗑 Очистить"]],
    resize_keyboard=True,
)


def format_date_ru(d: datetime) -> str:
    return f"{d.day} {MONTHS_RU[d.month]} {d.year}"


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(  # type: ignore[union-attr]
        "Привет! Отправляй мне фото еды в течение дня.\n"
        "Можешь добавить подпись к фото.\n\n"
        "Когда будешь готов — нажми «📋 Итог дня», и я создам "
        "статью на Telegraph со всеми фото за сегодня.",
        reply_markup=MAIN_KEYBOARD,
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.photo:
        return

    storage: Storage = context.bot_data["storage"]
    user_id = update.effective_user.id  # type: ignore[union-attr]

    photo = update.message.photo[-1]
    caption = update.message.caption

    entry_id = storage.save_entry(user_id, photo.file_id, caption)

    entries = storage.get_today_entries(user_id)
    now_str = datetime.now(EKB_TZ).strftime("%H:%M")

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "⏰ Изменить время", callback_data=f"tc:{entry_id}"
                ),
                InlineKeyboardButton("✅ OK", callback_data=f"tok:{entry_id}"),
            ]
        ]
    )

    await update.message.reply_text(
        f"Сохранено! ({len(entries)} за сегодня)\nВремя: {now_str}",
        reply_markup=keyboard,
    )


async def handle_time_change(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()  # type: ignore[union-attr]
    entry_id = query.data.split(":")[1]  # type: ignore[union-attr]

    rows = []
    for start in range(6, 24, 6):
        row = [
            InlineKeyboardButton(
                f"{h:02d}", callback_data=f"th:{entry_id}:{h}"
            )
            for h in range(start, min(start + 6, 24))
        ]
        rows.append(row)

    await query.edit_message_text(  # type: ignore[union-attr]
        "Выбери час:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def handle_hour_select(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()  # type: ignore[union-attr]
    _, entry_id, hour = query.data.split(":")  # type: ignore[union-attr]

    minutes = [0, 15, 30, 45]
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"{int(hour):02d}:{m:02d}",
                    callback_data=f"tm:{entry_id}:{hour}:{m}",
                )
                for m in minutes
            ]
        ]
    )

    await query.edit_message_text(  # type: ignore[union-attr]
        "Выбери минуты:",
        reply_markup=keyboard,
    )


async def handle_minute_select(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()  # type: ignore[union-attr]
    _, entry_id, hour, minute = query.data.split(":")  # type: ignore[union-attr]

    storage: Storage = context.bot_data["storage"]
    storage.update_entry_time(int(entry_id), int(hour), int(minute))

    new_time = f"{int(hour):02d}:{int(minute):02d}"
    await query.edit_message_text(f"✅ Время изменено на {new_time}")  # type: ignore[union-attr]


async def handle_time_ok(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()  # type: ignore[union-attr]
    await query.edit_message_reply_markup(reply_markup=None)  # type: ignore[union-attr]


async def handle_text_buttons(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    text = update.message.text  # type: ignore[union-attr]
    if text == "📋 Итог дня":
        await summary_command(update, context)
    elif text == "📊 Счётчик":
        await count_command(update, context)
    elif text == "🗑 Очистить":
        await cancel_command(update, context)


async def count_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    storage: Storage = context.bot_data["storage"]
    user_id = update.effective_user.id  # type: ignore[union-attr]
    entries = storage.get_today_entries(user_id)

    if not entries:
        await update.message.reply_text("Сегодня записей нет. Отправь мне фото!")
    else:
        await update.message.reply_text(f"Сегодня записей: {len(entries)}")


async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    storage: Storage = context.bot_data["storage"]
    telegraph: TelegraphClient = context.bot_data["telegraph"]
    user_id = update.effective_user.id  # type: ignore[union-attr]

    entries = storage.get_today_entries(user_id)

    if not entries:
        await update.message.reply_text(
            "Сегодня записей нет. Сначала отправь фото!"
        )
        return

    progress = await update.message.reply_text(
        f"Создаю статью из {len(entries)} записей... Загружаю фото..."
    )

    try:
        image_entries: list[tuple[str, str | None, str]] = []

        for entry in entries:
            file = await context.bot.get_file(entry.photo_file_id)
            photo_bytes = await file.download_as_bytearray()

            image_url = await telegraph.upload_image(
                bytes(photo_bytes),
                filename=f"food_{entry.id}.jpg",
            )
            time_str = entry.created_at.strftime("%H:%M")
            image_entries.append((image_url, entry.caption, time_str))

        placeholder_url = await telegraph.get_placeholder_url()
        content = build_article_content(image_entries, placeholder_url)

        today = datetime.now(EKB_TZ)
        title = f"Дневник питания — {format_date_ru(today)}"
        url = await telegraph.create_page(title, content)

        await progress.delete()
        await update.message.reply_text(
            f"Твой дневник питания за сегодня:\n{url}",
            link_preview_options=LinkPreviewOptions(
                url=url,
                prefer_large_media=True,
                show_above_text=True,
            ),
        )

    except Exception:
        logger.exception("Failed to create summary")
        await progress.edit_text(
            "Что-то пошло не так при создании статьи. Попробуй позже."
        )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    storage: Storage = context.bot_data["storage"]
    user_id = update.effective_user.id  # type: ignore[union-attr]
    count = storage.clear_today_entries(user_id)

    if count == 0:
        await update.message.reply_text("Нечего удалять — записей за сегодня нет.")
    else:
        await update.message.reply_text(f"Удалено записей: {count}")


async def post_init(application: Application) -> None:  # type: ignore[type-arg]
    config: Config = application.bot_data["config"]

    storage = Storage(config.db_path)
    storage.init()
    application.bot_data["storage"] = storage

    telegraph = TelegraphClient(
        imgbb_api_key=config.imgbb_api_key,
        author_name=config.telegraph_author_name,
        author_url=config.telegraph_author_url,
    )
    application.bot_data["telegraph"] = telegraph

    logger.info("Bot initialized. Storage: %s", config.db_path)


async def post_shutdown(application: Application) -> None:  # type: ignore[type-arg]
    storage: Storage | None = application.bot_data.get("storage")
    if storage:
        storage.close()

    telegraph: TelegraphClient | None = application.bot_data.get("telegraph")
    if telegraph:
        await telegraph.close()

    logger.info("Bot shut down cleanly.")


def main() -> None:
    config = Config.from_env()

    app = (
        Application.builder()
        .token(config.bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.bot_data["config"] = config

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("summary", summary_command))
    app.add_handler(CommandHandler("count", count_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_buttons)
    )
    app.add_handler(CallbackQueryHandler(handle_time_change, pattern=r"^tc:"))
    app.add_handler(CallbackQueryHandler(handle_hour_select, pattern=r"^th:"))
    app.add_handler(CallbackQueryHandler(handle_minute_select, pattern=r"^tm:"))
    app.add_handler(CallbackQueryHandler(handle_time_ok, pattern=r"^tok:"))

    logger.info("Starting bot...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

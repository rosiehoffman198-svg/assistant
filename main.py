import asyncio
import functools
import json
import logging
import os
import re
import tempfile

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import InputRichMessage, Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from groq import Groq

from config import (
    TELEGRAM_TOKEN, GROQ_API_KEY, MY_TELEGRAM_ID, HISTORY_LIMIT,
    FAST_MODEL, WHISPER_MODEL, TELEGRAM_MAX_LEN, RICH_MAX_LEN, now_local,
)
from database import init_db, close_pool
from tools import (
    get_last_messages, save_message, clear_history,
    get_tasks, get_reminders, get_profile, set_profile,
    load_pinned_facts, search_notes, complete_task,
    get_due_reminders, mark_reminder_sent, clear_pinned_facts,
    PROFILE_KEYS,
)
from llm import call_llm, maybe_generate_summary

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

bot       = Bot(token=TELEGRAM_TOKEN)
dp        = Dispatcher()
scheduler = AsyncIOScheduler()
groq      = Groq(api_key=GROQ_API_KEY)

# Strong refs to fire-and-forget tasks — the loop only holds weak ones.
_background: set[asyncio.Task] = set()


def spawn(coro):
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)
    return task


# ─── Guards ───────────────────────────────────────────────────────────────────

def owner_only(func):
    """Decorator: only MY_TELEGRAM_ID can use the bot."""
    @functools.wraps(func)  # сохраняет сигнатуру — aiogram не будет пробрасывать лишние kwargs
    async def wrapper(message: Message, **kwargs):
        if message.from_user.id != MY_TELEGRAM_ID:
            return
        return await func(message)
    return wrapper


def guarded(func):
    """Any unhandled error must reach the user — silence used to be the default
    because DB calls sat outside the handlers' try blocks."""
    @functools.wraps(func)
    async def wrapper(message: Message, **kwargs):
        try:
            return await func(message)
        except Exception as e:
            logger.exception(f"Handler {func.__name__} failed: {e}")
            try:
                await message.answer("❌ Что-то сломалось. Попробуй ещё раз.")
            except Exception:
                logger.exception("Could not deliver the error message")
    return wrapper


def handler(func):
    return owner_only(guarded(func))


# ─── Outgoing messages ────────────────────────────────────────────────────────

def split_text(text: str, limit: int = TELEGRAM_MAX_LEN) -> list[str]:
    """Telegram hard-caps messages at 4096 chars; /tasks and /profile could
    exceed it, and the resulting 400 silently killed the handler."""
    chunks, current = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if not current:
            current = line
        elif len(current) + 1 + len(line) <= limit:
            current += "\n" + line
        else:
            chunks.append(current)
            current = line
    if current:
        chunks.append(current)
    return chunks or ["…"]


async def reply_plain(message: Message, text: str):
    """Plain sendMessage, split to fit 4096. No parse_mode: user text and LLM
    output are untrusted markup and an unbalanced "_" used to abort the handler."""
    for chunk in split_text(text or "…"):
        await message.answer(chunk)


async def reply(message: Message, text: str):
    """Rich Message first (Bot API 10.1 — one message instead of several), plain
    split as fallback.

    The fallback is not optional: the 32k limit is unverified, and markdown
    coming from the LLM can be malformed enough for Telegram to reject it.
    Either way the user still gets the text.
    """
    text = text or "…"
    if len(text) <= RICH_MAX_LEN:
        try:
            await bot.send_rich_message(
                chat_id=message.chat.id,
                rich_message=InputRichMessage(markdown=text),
            )
            return
        except Exception as e:
            logger.warning(f"Rich message rejected, falling back to plain: {e}")
    await reply_plain(message, text)


# ─── Health check (нужен для Web Service на хостинге) ─────────────────────────

async def health(request):
    return web.Response(text="OK")


async def run_web_server():
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Health check server started on port {port}")
    return runner


# ─── Commands ─────────────────────────────────────────────────────────────────

@dp.message(Command("start"))
@handler
async def cmd_start(message: Message):
    await reply(
        message,
        "👋 Привет! Я твой личный ассистент.\n\n"
        "Просто пиши или отправляй голосовые — я сам разберусь.\n\n"
        "Команды:\n"
        "/tasks — список задач\n"
        "/reminders — напоминания\n"
        "/search [запрос] — поиск по заметкам\n"
        "/profile — профиль\n"
        "/setup — настроить профиль\n"
        "/reset — очистить историю диалога\n"
        "/forget — удалить закреплённые факты\n"
        "/done [id] — выполнить задачу"
    )


@dp.message(Command("tasks"))
@handler
async def cmd_tasks(message: Message):
    await reply(message, await asyncio.to_thread(get_tasks))


@dp.message(Command("reminders"))
@handler
async def cmd_reminders(message: Message):
    await reply(message, await asyncio.to_thread(get_reminders))


@dp.message(Command("search"))
@handler
async def cmd_search(message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await reply(message, "Укажи запрос: /search [текст]")
        return
    await reply(message, await asyncio.to_thread(search_notes, parts[1]))


@dp.message(Command("profile"))
@handler
async def cmd_profile(message: Message):
    profile = await asyncio.to_thread(get_profile)
    pinned  = await asyncio.to_thread(load_pinned_facts)
    if not profile and not pinned:
        await reply(message, "Профиль пустой.\nИспользуй /setup чтобы настроить.")
        return
    lines = ["👤 Профиль:\n"]
    for k, v in profile.items():
        lines.append(f"• {k}: {v}")
    if pinned:
        lines.append("\n📌 Запомненные факты:\n" + pinned)
    await reply(message, "\n".join(lines))


@dp.message(Command("setup"))
@handler
async def cmd_setup(message: Message):
    await reply(
        message,
        "Расскажи о себе — я всё запомню.\n\n"
        "Например:\n"
        "«Меня зовут Макс. Цели: запустить SaaS и выйти на $5k MRR. "
        "Предпочитаю краткие ответы без воды. Работаю по утрам.»"
    )


@dp.message(Command("reset"))
@handler
async def cmd_reset(message: Message):
    await reply(message, await asyncio.to_thread(clear_history))


@dp.message(Command("forget"))
@handler
async def cmd_forget(message: Message):
    removed = await asyncio.to_thread(clear_pinned_facts)
    await reply(message, f"🗑 Удалено закреплённых фактов: {removed}.")


@dp.message(Command("done"))
@handler
async def cmd_done(message: Message):
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await reply(message, "Укажи ID задачи: /done [id]")
        return
    await reply(message, await asyncio.to_thread(complete_task, int(parts[1])))


# ─── Voice messages ───────────────────────────────────────────────────────────

def transcribe(path: str) -> str:
    with open(path, "rb") as audio:
        transcription = groq.audio.transcriptions.create(
            file=("voice.ogg", audio, "audio/ogg"),
            model=WHISPER_MODEL,
            language="ru",
        )
    return transcription.text.strip()


@dp.message(F.voice)
@handler
async def handle_voice(message: Message):
    await bot.send_chat_action(message.chat.id, "typing")

    file_info   = await bot.get_file(message.voice.file_id)
    voice_bytes = await bot.download_file(file_info.file_path)

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp.write(voice_bytes.read())
        tmp_path = tmp.name

    try:
        text = await asyncio.to_thread(transcribe, tmp_path)
    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        await reply(message, "❌ Не смог распознать голосовое.")
        return
    finally:
        # Was after the network call with no finally — every failure leaked a file.
        try:
            os.unlink(tmp_path)
        except OSError:
            logger.warning(f"Could not remove temp file {tmp_path}")

    if not text:
        await reply(message, "🎤 Не удалось распознать речь.")
        return

    # Transcribed speech is untrusted text — echo it verbatim, never as markup.
    await reply_plain(message, f"🎤 «{text}»")
    await process_message(message, text)


# ─── Text messages ────────────────────────────────────────────────────────────

@dp.message(F.text & ~F.text.startswith("/"))
@handler
async def handle_text(message: Message):
    profile = await asyncio.to_thread(get_profile, True)
    has_profile = any(k in profile for k in PROFILE_KEYS)
    # The marker stops this from firing a full LLM call on every single message
    # forever when extraction finds nothing.
    if not has_profile and "_extract_tried" not in profile:
        await extract_profile(message.text)
    await process_message(message, message.text)


@dp.message(F.text.startswith("/"))
@handler
async def unknown_command(message: Message):
    await reply(message, "Не знаю такой команды. /start — список доступных.")


# ─── Core processing ──────────────────────────────────────────────────────────

async def process_message(message: Message, text: str):
    await asyncio.to_thread(save_message, "user", text)

    # Background summary, off the event loop and off the response path.
    spawn(asyncio.to_thread(maybe_generate_summary))

    history = await asyncio.to_thread(get_last_messages, HISTORY_LIMIT)
    await bot.send_chat_action(message.chat.id, "typing")

    # Every Groq/psycopg call in here is synchronous — running it inline froze
    # the loop, the health endpoint and the scheduler for the whole round-trip.
    response = await asyncio.to_thread(call_llm, history)

    await reply(message, response)
    # Persist only after delivery, so history never contains a message the user
    # never saw.
    await asyncio.to_thread(save_message, "assistant", response)


# ─── Profile auto-extract ─────────────────────────────────────────────────────

def _extract_profile_sync(text: str) -> dict:
    prompt = (
        "Извлеки из текста: имя пользователя, его цели, стиль общения.\n"
        'Ответь строго в JSON без markdown: {"name": "...", "goals": "...", "style": "..."}\n'
        f"Текст: {text}"
    )
    resp = groq.chat.completions.create(
        model=FAST_MODEL,  # служебная задача — быстрая модель, не 70B
        messages=[{"role": "user", "content": prompt}],
        max_tokens=200,
        temperature=0,
    )
    raw = resp.choices[0].message.content or ""
    # str.strip("```json") strips a character SET, not a suffix, and mangled
    # anything starting with j/s/o/n. Take the JSON object itself instead.
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        return {}
    data = json.loads(match.group(0))
    return data if isinstance(data, dict) else {}


async def extract_profile(text: str):
    try:
        data = await asyncio.to_thread(_extract_profile_sync, text)
    except Exception as e:
        logger.warning(f"Profile extract failed: {e}")
        return

    saved = 0
    for key, value in data.items():
        if not value or value in ("null", "не указано", ""):
            continue
        try:
            # set_profile whitelists keys and stringifies scalars, so one bad
            # value no longer aborts the rest of the profile.
            if await asyncio.to_thread(set_profile, key, value):
                saved += 1
        except Exception as e:
            logger.warning(f"Profile: could not save {key!r}: {e}")

    if not saved:
        await asyncio.to_thread(set_profile, "_extract_tried", "1")


# ─── Reminder scheduler ───────────────────────────────────────────────────────

async def check_reminders():
    try:
        rows = await asyncio.to_thread(get_due_reminders, now_local())
    except Exception as e:
        logger.error(f"Could not load due reminders: {e}")
        return

    for row in rows:
        try:
            await bot.send_message(MY_TELEGRAM_ID, f"⏰ {row['title']}")
            await asyncio.to_thread(mark_reminder_sent, row["id"])
            logger.info(f"Reminder sent: {row['title']}")
        except Exception as e:
            logger.error(f"Reminder send error: {e}")


# ─── Entry point ──────────────────────────────────────────────────────────────

async def main():
    await asyncio.to_thread(init_db)
    logger.info("Database initialised")

    scheduler.add_job(check_reminders, "interval", minutes=1)
    scheduler.start()
    logger.info("Scheduler started")

    runner = await run_web_server()

    try:
        logger.info("Bot polling started")
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await runner.cleanup()
        await bot.session.close()
        await asyncio.to_thread(close_pool)
        logger.info("Shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())

import asyncio
import functools
import logging
import os
import tempfile
from datetime import datetime

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from groq import Groq

from config import TELEGRAM_TOKEN, GROQ_API_KEY, MY_TELEGRAM_ID, HISTORY_LIMIT, MODELS
from database import init_db, get_conn
from tools import (
    get_last_messages, save_message, clear_history,
    get_tasks, get_reminders, get_profile, set_profile,
    load_pinned_facts, search_notes, complete_task,
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


# ─── Auth guard ───────────────────────────────────────────────────────────────

def owner_only(func):
    """Decorator: only MY_TELEGRAM_ID can use the bot."""
    @functools.wraps(func)  # сохраняет сигнатуру — aiogram не будет пробрасывать лишние kwargs
    async def wrapper(message: Message, **kwargs):
        if message.from_user.id != MY_TELEGRAM_ID:
            return
        return await func(message)
    return wrapper


# ─── Health check (нужен для Render Web Service) ──────────────────────────────

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


# ─── Commands ─────────────────────────────────────────────────────────────────

@dp.message(Command("start"))
@owner_only
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я твой личный ассистент.\n\n"
        "Просто пиши или отправляй голосовые — я сам разберусь.\n\n"
        "Команды:\n"
        "/tasks — все задачи\n"
        "/tasks low — задачи с низкой энергией\n"
        "/tasks high — задачи с высокой энергией\n"
        "/projects — проекты\n"
        "/reminders — напоминания\n"
        "/search [запрос] — поиск по заметкам\n"
        "/profile — профиль\n"
        "/setup — настроить профиль\n"
        "/reset — очистить историю диалога\n"
        "/done [id] — выполнить задачу"
    )


@dp.message(Command("tasks"))
@owner_only
async def cmd_tasks(message: Message):
    parts = message.text.split(maxsplit=1)
    arg   = parts[1].lower().strip() if len(parts) > 1 else None
    if arg in ("low", "low-energy", "лёгкие"):
        await message.answer(get_tasks(energy="low"))
    elif arg in ("high", "high-energy", "сложные"):
        await message.answer(get_tasks(energy="high"))
    elif arg in ("done", "выполненные"):
        await message.answer(get_tasks(show_completed=True))
    else:
        await message.answer(get_tasks())


@dp.message(Command("projects"))
@owner_only
async def cmd_projects(message: Message):
    await message.answer(get_projects())

@dp.message(Command("reminders"))
@owner_only
async def cmd_reminders(message: Message):
    await message.answer(get_reminders())


@dp.message(Command("search"))
@owner_only
async def cmd_search(message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Укажи запрос: /search [текст]")
        return
    await message.answer(search_notes(parts[1]))


@dp.message(Command("profile"))
@owner_only
async def cmd_profile(message: Message):
    profile = get_profile()
    pinned  = load_pinned_facts()
    if not profile and not pinned:
        await message.answer("Профиль пустой.\nИспользуй /setup чтобы настроить.")
        return
    lines = ["👤 Профиль:\n"]
    for k, v in profile.items():
        lines.append(f"• {k}: {v}")
    if pinned:
        lines.append("\n📌 Запомненные факты:\n" + pinned)
    await message.answer("\n".join(lines))


@dp.message(Command("setup"))
@owner_only
async def cmd_setup(message: Message):
    await message.answer(
        "Расскажи о себе — я всё запомню.\n\n"
        "Например:\n"
        "«Меня зовут Макс. Цели: запустить SaaS и выйти на $5k MRR. "
        "Предпочитаю краткие ответы без воды. Работаю по утрам.»"
    )


@dp.message(Command("reset"))
@owner_only
async def cmd_reset(message: Message):
    clear_history()
    await message.answer("🗑 История диалога очищена.")


@dp.message(Command("done"))
@owner_only
async def cmd_done(message: Message):
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Укажи ID задачи: /done [id]")
        return
    await message.answer(complete_task(int(parts[1])))


# ─── Voice messages ───────────────────────────────────────────────────────────

@dp.message(F.voice)
@owner_only
async def handle_voice(message: Message):
    await bot.send_chat_action(message.chat.id, "typing")
    try:
        file_info   = await bot.get_file(message.voice.file_id)
        voice_bytes = await bot.download_file(file_info.file_path)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp.write(voice_bytes.read())
            tmp_path = tmp.name

        with open(tmp_path, "rb") as audio:
            transcription = groq.audio.transcriptions.create(
                file=("voice.ogg", audio, "audio/ogg"),
                model="whisper-large-v3",
                language="ru",
            )
        os.unlink(tmp_path)

        text = transcription.text.strip()
        if not text:
            await message.answer("🎤 Не удалось распознать речь.")
            return

        await message.answer(f"🎤 _{text}_", parse_mode="Markdown")
        await process_message(message, text)

    except Exception as e:
        logger.error(f"Voice error: {e}")
        await message.answer("❌ Ошибка при обработке голосового сообщения.")


# ─── Text messages ────────────────────────────────────────────────────────────

@dp.message(F.text & ~F.text.startswith("/"))
@owner_only
async def handle_text(message: Message):
    profile = get_profile()
    if not profile:
        await maybe_extract_profile(message.text)
    await process_message(message, message.text)


# ─── Core processing ──────────────────────────────────────────────────────────

async def process_message(message: Message, text: str):
    save_message("user", text)

    # Generate summary in background every SUMMARY_INTERVAL messages.
    # Runs in a thread-pool so it never delays the response.
    asyncio.create_task(asyncio.to_thread(maybe_generate_summary))

    history = get_last_messages(n=HISTORY_LIMIT)
    await bot.send_chat_action(message.chat.id, "typing")
    try:
        response = call_llm(history)
        save_message("assistant", response)
        await message.answer(response)
    except Exception as e:
        logger.error(f"LLM error: {e}")
        await message.answer("❌ Ошибка. Попробуй ещё раз.")


# ─── Profile auto-extract ─────────────────────────────────────────────────────

async def maybe_extract_profile(text: str):
    import json
    extract_prompt = (
        "Извлеки из текста: имя пользователя, его цели, стиль общения.\n"
        "Ответь строго в JSON без markdown: {\"name\": \"...\", \"goals\": \"...\", \"style\": \"...\"}\n"
        f"Текст: {text}"
    )
    try:
        resp = groq.chat.completions.create(
            model=MODELS[0],  # use fastest model for lightweight extraction
            messages=[{"role": "user", "content": extract_prompt}],
            max_tokens=200,
            temperature=0,
        )
        raw = resp.choices[0].message.content or "{}"
        raw = raw.strip().strip("```json").strip("```").strip()
        data = json.loads(raw)
        for k, v in data.items():
            if v and v not in ("null", "не указано", ""):
                set_profile(k, v)
    except Exception as e:
        logger.warning(f"Profile extract failed: {e}")


# ─── Reminder scheduler ───────────────────────────────────────────────────────

async def check_reminders():
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM reminders WHERE sent = 0 AND remind_at <= ?", (now,))
    rows = c.fetchall()
    for row in rows:
        try:
            await bot.send_message(MY_TELEGRAM_ID, f"⏰ {row['title']}")
            c.execute("UPDATE reminders SET sent = 1 WHERE id = ?", (row["id"],))
        except Exception as e:
            logger.error(f"Reminder send error: {e}")
    conn.commit()
    conn.close()


# ─── Entry point ──────────────────────────────────────────────────────────────

async def main():
    init_db()
    logger.info("Database initialised")

    scheduler.add_job(check_reminders, "interval", minutes=1)
    scheduler.start()
    logger.info("Scheduler started")

    await run_web_server()

    logger.info("Bot polling started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

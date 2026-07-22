# План Stage 1 — «Второй мозг» (ред. 2, с учётом всех правок)

## Зафиксированные решения
- **Платформа:** расширяем существующего Telegram-бота (`D:\AI ASSISTANT\Z code`). PostgreSQL остаётся.
- **Миграции:** только аддитивные (`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`), данные не трогаем.
- **Реестр для каждого модуля (одинаков, чтобы фаза была самодостаточной):** `database.py` (таблица) → `tools.py` (CRUD + вывод) → `llm.py` (TOOL_DEFINITIONS/TOOL_MAP/промпт) → `main.py` (slash-команды) → `ast.parse` всех .py + smoke импорт.

## 🔑 Сквозные архитектурные решения (по твоим правкам №1, №2, №9)

**① user_id во все новые таблицы (№1)** — через `ContextVar`, чтобы LLM его не касался:
- В новом `ctx.py` (или в `config.py`): `CURRENT_USER_ID: ContextVar[int]`.
- `process_message()` в `main.py` выставляет `CURRENT_USER_ID.set(message.from_user.id)` **до** `call_llm`.
- Функции в `tools.py` (`create_goal`, `add_transaction`, `log_health`, `add_inbox`, …) читают `CURRENT_USER_ID.get()` **внутри себя** и подставляют в INSERT/WHERE. В LLM-facing сигнатуре поля `user_id` **нет** — `_prepare_args` его всё равно отбросит, а модели нечего галлюцинировать.
- Т.к. `asyncio.to_thread` копирует контекст → `user_id` доступен и в потоке `call_llm`. Сейчас всегда = `MY_TELEGRAM_ID`, но через полгода просто заработает для многих.
- ⚠️ **Отдельная мини-задача (опционально, обсудим):** добавить `user_id` и в **существующие** таблицы (`projects`, `tasks`, `notes`, `reminders`, `pinned_facts`) через `ADD COLUMN IF NOT EXISTS user_id` + backfill дефолтом `MY_TELEGRAM_ID`, и фильтр по нему в старых функциях. Это бóльшая правка рабочего кода — вынесу в **Phase 1.0** и спрошу подтверждение перед тем, как трогать существующие запросы.

**② Статусы через CHECK + константы (№2)** — не «голый TEXT»:
- На уровне БД: `status TEXT DEFAULT 'active' CHECK (status IN ('active','paused','done'))`.
- На уровне Python: модуль `constants.py` с `class GoalStatus: ACTIVE='active'; DONE='done'; PAUSED='paused'` и т.п. — используются и в SQL, и в `tools.py`. Никаких `Active`/`ACTIVE`/`finished`.

**③ audit created_at + updated_at везде (№9)** — в каждую новую таблицу:
- `created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP`
- `updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP`
- Триггер `BEFORE UPDATE SET updated_at = NOW()` (одна функция `touch_updated_at()` + `CREATE TRIGGER` через `DO $$` в `init_db`, все IF NOT EXISTS) — чтобы `updated_at` обновлялось автоматически при любом UPDATE, без правки каждого UPDATE в коде.

---

## PHASE 0 — Починка критического блокера (обязательно первой)
`llm.py:31-60` повреждён (висячий `"parameters":{...}` вне словаря → SyntaxError, бот не стартует).
- Восстановить обёртку `create_task` (`{"type":"function","function":{"name":"create_task",...,"parameters":{...}}}` — параметры уже написаны ниже, только обернуть).
- Убрать **дублирующий** `search_notes` (`llm.py:31` и `llm.py:151`) — оставить один с опциональным `query`.
- Проверка: `ast.parse` по 4 файлам + `import llm` без ошибки.
- **Критерий:** `TOOL_DEFINITIONS` содержит ровно по одному определению на ключ `TOOL_MAP`.

## PHASE 1.0 — (опционально) user_id в существующих таблицах
Добавить `user_id` + фильтр в `projects/tasks/notes/reminders/pinned_facts`, backfill дефолтом. **Спрошу подтверждение** перед тем, как трогать рабочий код — это регрессионный риск.

## PHASE 1 — Модули данных (первая волна)

### 1.1 Universal Inbox — как страховочная сетка, а не «главная папка» (№3, №10)
**Новая философия:** пользователь пишет естественным языком; если классификация **очевидна** («потратил 230 000 на бензин», «вес 71», «создай задачу…») — LLM создаёт запись сразу в нужном модуле. В Inbox падает **только неоднозначное** («есть идея бизнеса», «надо обдумать…»). Inbox — fallback, а не основной путь. Это не «AI-категоризация» из запрещённого списка ТЗ — это обычный intent-routing, который бот уже делает через tool-calling; просто добавляем `add_inbox` как резервный инструмент.
- **Таблица `inbox`** (с user_id №1, audit №9):
  `id, user_id INT, type TEXT CHECK(type IN ('text','voice','image','document','forward')), content TEXT, metadata JSONB DEFAULT '{}', status TEXT DEFAULT 'inbox' CHECK(status IN ('inbox','task','note','expense','health','done')), created_at, updated_at`.
- **metadata JSONB (№3):** `{"telegram_file_id":"...","duration":35,"mime":"audio/ogg"}` или `{"photo":"..."}` — спасёт позже.
- **`tools.py`:** `add_inbox(content, type='text', metadata=None)`, `get_inbox(status='inbox')`, `resolve_inbox(item_id, action, ...)` (явное перемещение в task/note/…), `get_inbox_count()`.
- **Промпт (правило №10):** «Если уверен, к чему относится запись — вызови нужный инструмент напрямую (create_task/add_transaction/log_health/save_note). Если не уверен — add_inbox. Никогда не спрашивай пользователя "это задача или идея?" без необходимости».

### 1.2 Goals 🎯 (полноценный модуль, №4: priority + audit)
- **Таблица `goals`:** `id, user_id, title, description, deadline TEXT, status TEXT DEFAULT 'active' CHECK(... in active/done/paused), priority TEXT DEFAULT 'medium' CHECK(... in high/medium/low), kpi TEXT, progress INT DEFAULT 0 CHECK(0≤progress≤100), created_at, updated_at`.
- **Связка `goal_projects`:** `goal_id, project_id` (многие-ко-многим: «Запустить MVP» ↔ Backend/Bot/Landing).
- **`tools.py`:** `create_goal`, `get_goals(status_filter)`, `update_goal` (статус/прогресс/kpi/priority), `link_goal_project`.
- **В `build_system_prompt`:** блок «🎯 Активные цели» (отсортированы по priority, как `get_active_projects_summary`).
- **Команда:** `/goals`.

### 1.3 Financial Journal 💰 (№5: account/wallet)
- **Таблица `transactions`:** `id, user_id, type TEXT CHECK(... in income/expense), amount NUMERIC(12,2) CHECK(amount≥0), currency TEXT DEFAULT 'UZS', category TEXT, account TEXT DEFAULT 'cash' CHECK(... in cash/card/humo/visa/crypto), comment TEXT, tx_date DATE DEFAULT CURRENT_DATE, created_at, updated_at`.
- **`tools.py`:** `add_transaction(type, amount, category, comment, account, currency)`, `get_transactions(days=7, type, category, account)`, `get_spent_today()`.
- **`profile.currency`** (дефолт `UZS`) — добавить в `PROFILE_KEYS`.
- **Команда:** `/money`.

### 1.4 Health Journal 💪 (№6: energy/stress)
- **Таблица `health_log`:** `id, user_id, weight NUMERIC, sleep_hours NUMERIC, workout TEXT, mood TEXT CHECK(... in good/normal/bad) , energy TEXT CHECK(... in high/medium/low), stress TEXT CHECK(... in high/medium/low), habits TEXT, note TEXT, log_date DATE DEFAULT CURRENT_DATE, created_at, updated_at`.
- **`tools.py`:** `log_health(weight, sleep_hours, workout, mood, energy, stress, habits, note)`, `get_health(days=7)`.
- **Команда:** `/health`.

**Критерий Phase 1:** 4 таблицы создаются, инструменты видны LLM, `/goals` `/money` `/health` `/inbox` отвечают, user_id фильтрует выборку.

## PHASE 1.5 — Search Everywhere 🔍 (перенесено раньше — №8)
`ILIKE`/`tsvector` по **всем** таблицам — несколько часов работы, польза каждый день.
- **`tools.py`:** `search_everywhere(query)` — по заметкам/Inbox (GIN `to_tsvector('russian',...)` уже есть), задачам/проектам/целям (`ILIKE` по title/name/description), истории сообщений, транзакциям (comment/category). Группировка по секциям, ≤3–5 на секцию (контроль токенов).
- `search_notes` остаётся узким; `search_everywhere` — зонтичный.
- **Команда:** `/find [запрос]`; `/search` расширяется до глобального.
- **С учётом user_id:** поиск только по своим записям (`WHERE user_id = CURRENT_USER_ID.get()`).

## PHASE 2 — Опыт: Dashboard, Daily/Weekly Review
### 2.1 Dashboard как центральная команда (№7: /start → сразу польза)
- **`tools.py`:** `get_dashboard()` — агрегирует: активные цели, задачи сегодня/просрочено, расходы за день, последняя тренировка, счётчик Inbox, ближайшие напоминания.
- **Команда `/start` переработана:** сразу показывает дашборд (`📅 Сегодня / 🎯 Цель / 📥 Inbox / 📌 Просрочено / 💰 Расходы / 🏋️ Тренировка / 🔔 Напоминания`), а не просто список команд.
- **Команды:** `/today`, `/dash`.

### 2.2 Daily Review 🌙
`✔ Выполнено (N), 🎯 главный результат, 📌 осталось, 🔥 фокус завтра`. ≤1 мин чтения.
- **`tools.py`:** `build_daily_review()`.
- **APScheduler:** `cron hour=21` (константа `DAILY_REVIEW_HOUR` в `config.py`).
- **Команда:** `/review`.

### 2.3 Weekly Review 📅
Обзор за неделю (задачи/проекты/цели/финансы/привычки), без аналитики.
- **APScheduler:** `cron day_of_week=sun, hour=20`.
- **Команда:** `/week`.

## PHASE 3 — Calendar + напоминания
### 3.1 Calendar 📆
`get_schedule(period)`, period ∈ today/tomorrow/week/overdue — на основе `tasks.deadline` + `reminders.remind_at`, канонический формат уже есть.
### 3.2 Расширение напоминаний ⏰
«через N часов/дней», повторяющиеся (`repeat TEXT`: daily/weekly/monthly — после отправки переносим `remind_at` вперёд, а не `sent=1`), `snooze_reminder(id, minutes)`.

## ⭐ Маркер важности (новая идея №11)
Сообщение начинается с `!` → высокий приоритет; `‼️` → высокий + пин в Inbox/pinned_facts.
- Реализация в `process_message`/`handle_text`: пре-парсинг префикса; для задач — `priority='high'`, `importance='high'`; для Inbox — флаг в metadata `{"priority":"high"}` и визуально 🔴.
- Учитывается в `build_system_prompt` (правило: «`!`/`‼️` = всегда высокий приоритет»).

## Что НЕ делаем на Stage 1
Анализ поведения, прогнозы, закономерности, AI-советник, автопланирование, авто-категоризация (как ML), проактивные советы «я заметил…» — Stage 2/3. (Примечание: intent-routing «очевидное → сразу в модуль» №10 — это не запрещённая авто-категоризация, а стандартный tool-calling; включаем.)

## Управление контекстом
- Каждая Phase — отдельная сессия; паттерны и архитектурные решения зафиксированы здесь.
- Порядок строго: **0 → 1.0(опц.) → 1 → 1.5 → 2 → 3**.
- После каждой фазы: `ast.parse` всех .py + критерии выше.

## С чего начнём
После согласования: **Phase 0** (починка `llm.py`) → **1.0** вопрос о user_id в существующих таблицах → **1.1 Universal Inbox** (с новой философией №10) → **1.2 Goals**.

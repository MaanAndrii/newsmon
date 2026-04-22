# Перехід з polling на push-архітектуру для Telegram моніторингу

## 1) Поточна проблема

Зараз система періодично опитує Telegram API за розкладом (`monitor.interval_seconds`, `monitor.schedule`) і забирає історію з кожного джерела батчами (`fetch_depth`).
Такий підхід:
- створює затримку між появою поста і його обробкою;
- генерує зайві API-запити у «тихі» періоди;
- ускладнює масштабування при зростанні кількості джерел.

## 2) Цільова модель

Перейти до **подійної (push) моделі**, де не ви опитуєте Telegram, а Telegram-клієнт/інтеграція штовхає подію `new_message` в ingest-пайплайн одразу після появи повідомлення.

> Важливий нюанс: для публічних каналів у Telegram немає «чистого webhook» напряму з Telegram серверів для MTProto user-клієнта. Практична push-модель будується через **довгоживучий Telethon-клієнт (updates stream)**, який тримає з’єднання і отримує апдейти в реальному часі.

## 3) Високорівнева схема

1. **Telegram Updates Ingestor (новий сервіс)**
   - Окремий процес/воркер, що піднімає Telethon client зі збереженою сесією.
   - Підписується на `events.NewMessage` для списку активних джерел.
   - На кожен апдейт формує нормалізовану подію і кладе її у внутрішню чергу.

2. **Ingest Queue (обов’язково)**
   - Рекомендовано Redis Streams / RabbitMQ / NATS / Kafka (для початку достатньо Redis Streams).
   - Дає backpressure, retry, відновлення після падінь.

3. **Message Processor (може бути поточний код monitor, розбитий на етапи)**
   - Ідемпотентний upsert у `messages`.
   - Dedup (контент-хеш + обмеження на source/message id).
   - AI-класифікація.
   - Алерти (Telegram bot / email / web push).

4. **Reconciliation Worker (періодичний, але рідкий)**
   - 1 раз на 15–60 хв перевіряє «дірки» (пропущені message_id) і робить дозабір.
   - Це не основний збір, а safety-net.

5. **Control Plane API / UI**
   - Керування підписками на джерела.
   - Health endpoints: останній апдейт, lag черги, reconnect count.

## 4) Детальний потік подій

1. Канал публікує новий пост.
2. Telethon updates stream приймає подію майже миттєво.
3. Ingestor формує payload:
   - `source_id`, `chat_id`, `tg_message_id`, `published_at`, `text`, `media_type`, `raw_json`, `telegram_url`.
4. Подія записується у queue з `event_id`.
5. Processor читає подію, робить `upsert` в БД (ідемпотентно).
6. Якщо новий запис — запускає AI + alerts pipeline.
7. SSE/WebSocket канал віддає оновлення dashboard майже real-time.

## 5) Мінімальні зміни по компонентах

### Backend
- Виділити з `services/monitor.py` логіку збереження/дедуп/AI в окремий модуль `ingest_pipeline`.
- Додати новий модуль `services/telegram_updates.py`:
  - lifecycle Telethon client;
  - handler на `NewMessage`;
  - публікація в queue;
  - reconnect з exponential backoff.
- Додати `services/reconcile.py` для дозбору пропусків.

### DB
- Додати таблицю `ingest_events` (або journal) з полями:
  - `event_id` (унікальний), `source_id`, `tg_message_id`, `received_at`, `status`, `retries`, `error`.
- У `messages` гарантувати унікальний ключ `(source_id, tg_message_id)`.
- За потреби додати `last_seen_tg_message_id` для кожного source.

### Конфіг
- Нові налаштування:
  - `monitor.mode = polling|push`;
  - `telegram.push.enabled`;
  - `telegram.push.reconnect_backoff_*`;
  - `reconcile.interval_seconds`.
- Поточні `interval_seconds/schedule` залишити тільки для reconciliation.

## 6) Надійність і відмовостійкість

- **At-least-once доставка**: черга + idempotent consumer.
- **Idempotency**: upsert по `(source_id, tg_message_id)` + content hash dedup.
- **Reconnect strategy**:
  - jitter + exponential backoff;
  - heartbeat метрика «час від останнього апдейту».
- **Dead-letter queue** для подій, що не обробились після N retry.
- **Graceful restart** ingestor без втрати стану.

## 7) Безпека

- Сесію Telethon (`StringSession`) тримати шифровано (KMS/secret store).
- Розділити секрети: user API creds окремо від bot token.
- Логувати тільки технічні ідентифікатори без чутливого контенту, якщо це політика комплаєнсу.

## 8) План міграції без простою

### Етап 0 — Підготовка
- Винести ingest pipeline в окремий сервісний шар (без зміни поведінки).
- Додати queue і worker, але ще не підключати до live updates.

### Етап 1 — Dual-write / Shadow mode
- Запустити push-ingestor у тіньовому режимі.
- Polling залишається master, push пише в окремий журнал/таблицю.
- Порівняти покриття повідомлень та latency.

### Етап 2 — Push primary
- Увімкнути `monitor.mode=push` для частини джерел (canary).
- Для решти ще polling.
- Моніторити SLO: lag, drop rate, duplicate rate.

### Етап 3 — Повне перемикання
- Перевести всі джерела на push.
- Polling лишити тільки як reconciliation.
- Через стабілізацію прибрати старий cron loop.

## 9) Метрики / SLO

Критично додати метрики:
- `telegram_updates_received_total`
- `ingest_queue_lag_seconds`
- `event_processing_latency_p95`
- `processor_failures_total`
- `reconnect_attempts_total`
- `missed_messages_recovered_total`

Базові цілі:
- p95 end-to-end latency < 5–10 сек;
- message loss = 0 (за рахунок reconciliation + retry);
- duplicate inserts = 0 на рівні бізнес-даних.

## 10) Що прибираємо з поточної логіки

- Основний цикл «кожні N секунд пройтись по всіх source».
- Adaptive/poll scheduling як core-механізм збору.
- Високі fetch_depth для регулярного збору (лишаються тільки для reconciliation).

## 11) Ризики та як закрити

- **Ліміти/флуд-контроль Telegram** → зменшення зайвих запитів у push, але треба обережний reconnect.
- **Втрата апдейтів під час розриву з’єднання** → catch-up worker по message_id gap.
- **Дублікати** → сувора ідемпотентність + unique constraint.
- **Складність операційки** → окремий health dashboard для ingestor/queue/processor.

## 12) Рекомендований MVP (2–3 ітерації)

1. Реалізувати `telegram_updates` + Redis Stream + простий processor.
2. Перенести поточний insert/dedup/AI/alerts у єдиний `process_event()`.
3. Увімкнути dual-run і порівняння з polling 7 днів.
4. Перемкнути 20% джерел на push, потім 100%.
5. Polling залишити як reconcile job 1 раз/30 хв.

---

Цей підхід дає майже real-time реакцію, нижче API-навантаження і кращу масштабованість, при цьому зберігає надійність через queue + idempotency + reconciliation.

# zen — бесплатный Gemini-класс API из opencode zen

Прямой клиент бесплатных моделей [opencode zen](https://opencode.ai/zen)
(`deepseek-v4-flash-free`, `nemotron-3-ultra-free`, `laguna-s-2.1-free`, `big-pickle` и др.)
плюс OpenAI-совместимый шлюз. Один файл, ноль зависимостей (чистый urllib), Python 3.

## Установка (Termux)

```sh
pkg install python -y
# скопировать zen.py на телефон, затем:
python zen.py serve --port 8787
```

Windows: `python zen.py serve --port 8787` (или `zen.bat serve`).

## Команды

| команда | что делает |
|---|---|
| `python zen.py` / `python zen.py chat` | интерактивный чат с авто-компактом контекста |
| `python zen.py chat "промпт"` | разовый запрос |
| `python zen.py chat "промпт" --stream` | стрим с reasoning (`[think]` в stderr) |
| `python zen.py models` | список моделей zen |
| `python zen.py serve --port 8787` | OpenAI-совместимый шлюз |

В интерактиве: `/model <name>` — смена модели, `/new` — очистить историю, `/quit` — выход.

## Шлюз

```
POST /v1/chat/completions   (OpenAI формат, стрим и не-стрим)
GET  /v1/models
```

Подключается любой OpenAI-клиент: `base_url=http://<host>:8787/v1`, `api_key=любая`.
Пример: SillyTavern, Cline, LangChain, свой код.

## Авто-компакт контекста (виртуальное расширение окна)

Когда история диалога превышает порог (по умолчанию 150000 символов ≈ 50k токенов),
шлюз/чат сам сжимает старые сообщения в резюме через ту же модель и продолжает
диалог — контекст фактически не кончается, работает для любой модели.

- `ZEN_MAX_CONTEXT=300000` — поднять порог
- `serve --max-context 0` — выключить компакт

## Лимиты zen и обход

- Лимит **дневной, по IP** (сброс в полночь UTC). У новых IP первые 7 дней — двойная норма.
- Смена сети/VPN = новый IP = свежий дневной бакет.
- Пул прокси для автомата: `ZEN_PROXIES="http://ip1:port,http://ip2:port"` — случайный прокси на каждый запрос.

## Переменные окружения

| переменная | по умолчанию | смысл |
|---|---|---|
| `ZEN_BASE` | `https://opencode.ai/zen/v1` | базовый URL zen |
| `ZEN_MAX_CONTEXT` | `150000` | порог авто-компакта (символы) |
| `ZEN_PROXIES` | — | список прокси через запятую |
| `ZEN_TOKEN` | — | пароль шлюза (Bearer / x-api-key) |
| `ZEN_PORT` | `8787` | порт шлюза |
| `ZEN_TIMEOUT` | `120` | таймаут запросов (сек) |

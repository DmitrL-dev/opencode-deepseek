# Tool-режим моста (`TOOLCALL_LEGACY=0`)

Единый tool-rework контракт для **всех** провайдеров, включая DeepSeek
(`TOOLCALL_INVALID_MODE`, demote, multi-block, pointed repair).

Рядом: [toolcall-format-repair-legacy.md](toolcall-format-repair-legacy.md)
(`LEGACY=1` / main-era).

Источники: `server/api.py`, `server/tool_protocol.py`, `server/openai_format.py`, `server/tool_notice.py`, `README.md`, `.env.example`.

---

## Политика провайдеров (актуально)

| `TOOLCALL_LEGACY` | Контракт |
|-------------------|----------|
| `1` (default) | main-era для **всех** (`api.py` + `tool_protocol.py`) |
| `0` | единый tool-rework для **всех** (`api_new.py` + `tool_protocol_new.py`) |

Решение: при `LEGACY=0` нет отдельного парсера DeepSeek.

---

## `TOOLCALL_INVALID_MODE` (`error` \| `text`, default `error`)

После `ToolCallError` (и после неудачной одной коррекции), для **любого** провайдера
(DeepSeek и browser). Термины: **провайдер** = закладка с чатом ИИ; **мост** = наше
приложение; **агент** = приложение, куда пишет человек.

### Схема вызовов (актуальная)

```text
человек → агент → мост → провайдер (закладка)
                ←       ←

Провайдер вернул ответ
  → мост: validate (+ опц. TOOLCALL_FORMAT_REPAIR=1 → один correction в ту же беседу)
  → OK: агенту tool_calls и/или content
  → ToolCallError:
        ├─ INVALID_MODE=error (default)
        │     → агенту HTTP/SSE invalid_tool_response
        │     → риск обрыва продолжения истории у агента
        │
        └─ INVALID_MODE=text  (нужны: tool_choice auto, stop, conversation_id)
              → агенту HTTP 200, чат провайдера жив:
                    content:
                      <сырой ответ провайдера>

                      Bridge notice: BRIDGE_TOOL_NOT_EXECUTED: … NOT executed: …
                    message.refusal: <тот же текст notice>
                    top-level bridge_notice:
                      {
                        "executed": false,
                        "code": "BRIDGE_TOOL_NOT_EXECUTED",
                        "kind": "schema|format|unknown_tool|repeat|…",
                        "tool": "<имя или null>",
                        "message": "<полный notice>",
                        "as_tool_error": {
                          "name": "<имя или tool>",
                          "content": "NOT executed: …"
                        }
                      }
                    исполняемые tool_calls НЕ выдумываются
              → следующий ход человека в агенте:
                    мост → провайдеру в ту же беседу:
                      Tool result (<имя>): <notice>

                      <текст follow-up пользователя>
```

### Краткая таблица

| Режим | Исход (если auto + stop + conversation_id) |
|-------|--------------------------------------------|
| `error` | `invalid_tool_response` агенту (fail-closed; дефолт) |
| `text` | content + `BRIDGE_TOOL_NOT_EXECUTED` + `bridge_notice` + `refusal`; следующий ход в провайдер как `Tool result (имя): …` |

Иначе (required / нет id / не stop) — всегда raise, даже при `text`.  
Совет browser (demote → `` ```text ``) в параметр **не** входит. Битый JSON с
известным name — входит (как `ToolCallError`).

---

## Главное отличие парсеров (по `TOOLCALL_LEGACY`)

| | `LEGACY=1` (`tool_protocol`) | `LEGACY=0` (`tool_protocol_new`, все провайдеры) |
|--|----------------------------|-----------------------------------------------|
| Блоки | Один протокол-блок (допуски DeepSeek) | Несколько верхнеуровневых `` ```tool_calls `` где угодно |
| Битый JSON / без имени tool | Обычно `ToolCallError` | Demote → `` ```text ``; иначе `ToolCallError` |
| Unknown tool + проза | Ошибка пакета | Demote → `` ```text `` (совет, не вызов) |
| Unknown / schema / битый JSON с известным name | Ошибка пакета | Ошибка всего сообщения (± repair / INVALID_MODE) |
| После неудачного repair / без repair | Всегда error | `TOOLCALL_INVALID_MODE` |
| Повтор executed call | — | `text` → notice; `error` → raise |

---

## 1) Когда ответ идёт в tool-обработку (не сырая трансляция)

### Вход в конвейер

Как и раньше — по **запросу**, не по эвристике текста:

- есть `req.tools` → `api_new.tool_chat_or_notice` → `tool_chat_with_retry` → (опц. demote → text) иначе `validate_tool_reply` → `tool_protocol_new.validate_reply`;
- ответ буферизуется; агенту — уже `tool_calls` / `content` / notice / ошибка;
- без `tools` → `_plain_conversation_reply`.

Дальше развилка по провайдеру внутри `validate_reply`:

```text
provider == deepseek?
  да  → deepseek parse (один блок, preamble, calls-alias, DSML closers, …)
  нет и browser → _validate_browser_reply (find_tool_blocks)
  иначе → deepseek-подобный parse без deepseek-допусков
```

### Browser: что считается вызовом

Только **верхнеуровневый** fenced-блок `` ```tool_calls `` (`find_tool_blocks`: построчный учёт fence, вложенный/цитируемый fence не считается).

- несколько таких блоков → все валидные вызовы по порядку; текст вокруг с вырезанными блоками → `content`;
- проза, `` ```json ``, массивы без fence, цитаты → текст;
- **до validate** (`demote_mistagged_tool_reply`, stop + auto): нет call-level `name` или unknown `name` + проза → `` ```tool_calls `` → `` ```text ``, успешный `content` (не repair / не INVALID_MODE);
- любой оставшийся битый/no_name/unknown/schema/format блок → `ToolCallError` на всё сообщение (silent `continue` на syntax/no_name убран);
- `tool_choice: none` → разбор блоков никогда не превращает ответ в ошибку.

### DeepSeek: что считается вызовом

Канон — один блок:

````text
```tool_calls
[{"name": "...", "arguments": { ... }}]
```
````

При штатном `stop`: preamble, алиас `` ```calls ``, узкие локальные починки оболочки (без угадывания аргументов).  
«Похожая» разметка без канона (`has_tool_call_markup`) → ошибка, не текст.  
Пустой ответ → ошибка.

---

## 2) Ошибка → при `TOOLCALL_FORMAT_REPAIR=1` уходит промпт на переделку

### Eligibility (все сразу)

В `_tool_chat_with_retry`:

1. `TOOLCALL_FORMAT_REPAIR=1`;
2. провайдер **DeepSeek** **или** browser-провайдер;
3. `tool_choice != "none"`;
4. `finish_reason == "stop"`;
5. есть `conversation_id`;
6. выброшен `ToolCallError`.

Один paced-запрос в ту же беседу. Рекурсии нет. Исходный пакет не исполняется.

**Важно для browser:** совет (нет `name` / unknown+проза) срезается **demote** до
validate → repair не запускается. Оставшиеся `syntax`/`no_name` в validate —
`ToolCallError` → eligible repair / INVALID_MODE.

### Промпт DeepSeek

Базовый `_FORMAT_REPAIR_PROMPT` (как раньше):

```text
No tools were executed from your previous reply: it was empty or its tool-call format was invalid. Choose the next response using the task and tool schemas already provided. If a tool is necessary, return ONLY one fenced tool_calls block containing a complete valid JSON array. Each entry must have name and arguments; ALL input fields belong inside the arguments object. Escape string values correctly. No commentary before or after the block, and no XML/DSML tags. If you intended only an explanation or the work is complete, give plain text without a tool_calls block. Do not claim an unexecuted action succeeded.
```

При JSON-ошибке может добавиться `repair_hint` (позиция/категория, без значений аргументов).

### Промпт (`api_new.browser_repair_prompt`) — pointed, без полного dump схем каждый раз

| `exc.kind` | Что уходит модели |
|------------|-------------------|
| `unknown_tool` | «`X` is not an available tool» + полный `tool_catalog(tools)` (имена + форма аргументов, `?` = optional) + `_BROWSER_REPAIR_TAIL` |
| `schema` / `format` + есть `guidance` | `guidance` (форма **этого** инструмента, без значений) + tail |
| иначе | короткий «empty or invalid» + tail |

Хвост `_BROWSER_REPAIR_TAIL`:

```text
Send a call as a fenced tool_calls block: a JSON array of {"name": ..., "arguments": {...}} entries with exact tool names; all input fields belong inside arguments. If no action is needed, answer in plain text. Do not claim an unexecuted action succeeded.
```

Если коррекция успешна — агент видит только итоговый результат, **без** notice.

---

## 3) Ошибка → даже при `TOOLCALL_FORMAT_REPAIR=1` промпт не шлётся

### Сразу без correction-запроса

| Признак | Поведение |
|--------|-----------|
| `TOOLCALL_FORMAT_REPAIR=0` | Repair выключен |
| Не DeepSeek и не browser | Не eligible |
| `tool_choice == "none"` | Не eligible |
| `finish_reason != "stop"` (например `length`) | Не format repair; возможен отдельный `TOOLCALL_MAX_CONTINUATIONS` |
| Нет `conversation_id` | Не eligible |
| Provider reject / login / cancel | Другой путь |
| Browser: блок `syntax` / `no_name` | Вообще не ошибка → текст |
| Уже была одна коррекция, второй ответ снова плохой | Дальше см. §4 (DeepSeek vs browser) |

### После неудачного repair / без repair: `TOOLCALL_INVALID_MODE`

Не зависит от DeepSeek vs browser — см. схему в разделе `TOOLCALL_INVALID_MODE` выше.

- **`error` (default)** — `invalid_tool_response` агенту.
- **`text`** + guards — content notice с `BRIDGE_TOOL_NOT_EXECUTED`, `bridge_notice`,
  `refusal`; чат провайдера жив; следующий ход → `Tool result (имя): …`.

### Повтор вызова (executed-path, сейчас browser)

Повтор с теми же args не исполняется: при `text` — notice `kind=repeat`; при `error` — raise.

---

## 4) Полная схема развилок (`LEGACY=0`)

Активна при `TOOLCALL_LEGACY=0` (`api_new` + `tool_protocol_new`). При
`TOOLCALL_LEGACY=1` (default) — main-era в `api.py` / `tool_protocol.py`;
см. [toolcall-format-repair-legacy.md](toolcall-format-repair-legacy.md). Флаг временный.

```text
req.tools?
  нет  → plain reply
  да   → api_new.tool_chat_or_notice
           │
           └─ tool_chat_with_retry
                │
                ├─ chat провайдера
                │
                ├─ [любой провайдер, stop, tool_choice auto] demote_mistagged_tool_reply?
                │     да (совет, не вызов):
                │       · нет call-level name (JSON целый/битый), или
                │       · unknown name + проза вокруг fence
                │     → ```tool_calls → ```text
                │     → агенту успешный content
                │       (без validate / repair / INVALID_MODE)
                │
                └─ иначе → validate_tool_reply
                     │
                     ├─ length + openai_format.looks_truncated (незакрытый top-level
                     │     ```tool_calls где угодно)
                     │     → TOOLCALL_MAX_CONTINUATIONS (НЕ format repair)
                     │
                     ├─ tool_protocol_new.validate_reply (все провайдеры)
                     │     ├─ все валидные блоки → tool_calls + content вокруг
                     │     │     (в SSE — упорядоченные части text / tool_call)
                     │     └─ syntax / no_name / unknown_tool / schema / format
                     │           → ToolCallError (advice уже срезан demote выше)
                     │     (tool_choice=none → блоки как текст, без ошибки)
                     │
                     └─ ToolCallError
                           ├─ eligible + FORMAT_REPAIR=1
                           │     → browser_repair_prompt (catalog / shape)
                           │           → один запрос → validate снова
                           │                 ├─ OK → агенту итог (без notice)
                           │                 └─ снова ошибка ↓
                           └─ не eligible / второй сбой
                                 ├─ INVALID_MODE=error или нет guards
                                 │     → invalid_tool_response агенту
                                 └─ INVALID_MODE=text + auto + stop + conversation_id
                                       → агенту: content + BRIDGE_TOOL_NOT_EXECUTED
                                         + bridge_notice + refusal
                                       → провайдерский тред жив
                                       → следующий user-ход → Tool result (имя): …

Дополнительно (browser executed-path):
  повтор name+args → text: notice repeat (+ bridge_notice) | error: raise

Рядом:
  ProviderRejected / login / cancel
  context rollover (DeepSeek, opt-in) — без repair/continuation
  TOOLCALL_DIAGNOSTICS / TOOLCALL_DEBUG
  TOOL_INSTRUCTIONS_BROWSER=medium — длиннее reminder на продолжениях (не repair)
```

### Порядок текста и вызовов в SSE (`LEGACY=0`)

`validate_reply(..., with_parts=True)` сохраняет исходный порядок частей:
`text → tool_call → text → tool_call → text`. `api_new` кладёт этот список в
`Reply.parts`, а `sse_frames` отправляет каждую часть отдельным кадром:
`delta.content` для текста и `delta.tool_calls` для вызова. `index` считается
только по вызовам, поэтому несколько текстовых частей не сдвигают нумерацию.
Финальный кадр получает `finish_reason: tool_calls`, если в списке есть вызовы.

Обычный ответ (`stream: false`) не меняется: один `content`, куски текста
склеены через пустую строку, и один массив `tool_calls`. Клиент всё равно
собирает SSE в такое же плоское сообщение; порядок частей виден только в
потоке кадров, но не в следующем запросе к провайдеру.

### Краткая схема repair + invalid mode

```text
ToolCallError после штатного stop?
  нет  → (browser: возможно просто текст / tool_calls; или continuation; или reject)
  да   → eligible (DeepSeek|browser, FORMAT_REPAIR=1, tool_choice≠none, conversation_id)?
           да → один correction → validate
                 ├─ OK → агенту
                 └─ снова ошибка → INVALID_MODE
           нет → INVALID_MODE сразу
                 error → invalid_tool_response агенту
                 text + guards → content + bridge_notice + refusal
                                 → next user → Tool result (имя): … в провайдер
```

---

## 5) Виды `ToolCallError.kind` (важно для browser)

| kind | Смысл | Browser validate | Repair prompt |
|------|--------|------------------|---------------|
| `syntax` | Битый JSON блока | → текст | не доходит |
| `no_name` | Нет имени tool / пустой образец | → текст | не доходит |
| `unknown_tool` | Имя не из списка | ошибка | catalog всех tools |
| `format` | Оболочка/args не object и т.п. | ошибка | shape этого tool (`guidance`) |
| `schema` | JSON Schema | ошибка | shape этого tool |
| `repeat` | Повтор только что исполненного | notice (API-слой) | не format-repair |

---

## 6) Переменные окружения

| Переменная | Default | Роль при `LEGACY=0` |
|------------|---------|---------------------|
| `TOOLCALL_LEGACY` | `1` | Должен быть `0` для этого пути |
| `TOOLCALL_FORMAT_REPAIR` | `0` | Одна pointed-коррекция для **любого** провайдера |
| `TOOLCALL_INVALID_MODE` | `error` | `error` / `text` — исход невалидности для **любого** провайдера |
| `TOOLCALL_MAX_CONTINUATIONS` | `0` | Continuation при `length` (незакрытый top-level fence где угодно) |
| `TOOL_INSTRUCTIONS_BROWSER` | `light` | `medium` — формы аргументов на продолжениях беседы |
| `TOOLCALL_DIAGNOSTICS` / `TOOLCALL_DEBUG` | off | Диагностика, не меняют дерево решений |

---

## Связь с соседними файлами

| Файл | Смысл |
|------|--------|
| [`toolcall-format-repair-legacy.md`](toolcall-format-repair-legacy.md) | `LEGACY=1` / main-era (`api` + `tool_protocol`) |
| [`toolcall-format-repair.md`](toolcall-format-repair.md) | Этот файл: единый `LEGACY=0` (`api_new` + `tool_protocol_new`) |

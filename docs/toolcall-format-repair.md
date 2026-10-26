# Tool-режим моста

Единственный путь обработки вызовов инструментов: `server/api.py` +
`server/tool_protocol.py` для **всех** провайдеров. Прежний альтернативный путь
`TOOLCALL_LEGACY=0` (`api_new.py` + `tool_protocol_new.py`) удалён.

Источники: `server/api.py`, `server/tool_protocol.py`, `server/openai_format.py`, `.env.example`.

---

## Параметры

| Переменная | Default | Роль |
|------------|---------|---------------------|
| `TOOLCALL_FORMAT_REPAIR` | `0` | Одна коррекция формата; **только DeepSeek**, main-era промпт |
| `TOOLCALL_MAX_CONTINUATIONS` | `0` | Continuation при `length`, если ответ **начинается** открытым fence |

---

## 1) Вход в tool-обработку

- Есть `req.tools` → буфер → `tool_protocol.validate_reply` (не сырая трансляция потока).
- Один канон-блок `` ```tool_calls `` на весь ответ (preamble / `calls` / узкие
  closer-fix при `stop` — допуски historically DeepSeek, для всех провайдеров).
- Несколько блоков / mid-message / demote — **нет**.

---

## 2) Format repair (`TOOLCALL_FORMAT_REPAIR=1`)

Eligibility: штатный `stop`, есть `conversation_id`, `tool_choice != none`,
провайдер **DeepSeek**. Промпт: `_FORMAT_REPAIR_PROMPT` ± `repair_hint`
(JSON line/col). Один запрос; исходный пакет не исполняется.

---

## 3) Исход невалидности

Всегда `invalid_tool_response` агенту.

Исполняемые `tool_calls` при отказе **не** выдумываются.

---

## 4) Схема

```text
req.tools?
  нет  → plain
  да   → tool_protocol.validate (+ optional FORMAT_REPAIR, только DeepSeek)
           ├─ OK tool_calls / OK content
           └─ ToolCallError → invalid_tool_response
```

---

## Что гарантирует парсер

- Контракт одного блока / markup→ошибка / пустой→ошибка
- `_FORMAT_REPAIR_PROMPT` и eligibility repair (DeepSeek)
- Локальные closer-fix без угадывания аргументов
- Continuation только через `TOOLCALL_MAX_CONTINUATIONS` (fence с начала ответа)

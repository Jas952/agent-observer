# Контракт наблюдаемых событий

POST `/api/event`, JSON, локальный HTTP endpoint. Заголовок `X-Agent-Observer-Token` (совместимый `X-CAD-Token` также поддерживается). Токен создаёт сервер с правами 0600; его нельзя сохранять в коде, промптах, URL, Git или журнале. WebSocket UI использует существующий протокол `cad-xray` с локальным токеном. Поддерживаются только loopback-адреса.

## Жизненный цикл

Каждый запрос получает уникальный `run_id`. Начало отправляется первым; все вызовы и финал используют этот же ID. Повторное использование завершённого ID и события чужого запроса запрещены. Каждая пара вызова имеет свой `call_id`.

```json
{"type":"PROMPT_START","run_id":"unique-request-id","project":"My project","prompt":"Actual request","project_spec":"Actual requirements","attachments":["relative/path.png"],"comments":[{"text":"Actual refinement"}]}
```

```json
{"type":"TOOL_CALL_START","run_id":"unique-request-id","call_id":"unique-call-id","tool":"read","input":"{\"path\":\"docs/requirements.md\"}"}
```

```json
{"type":"TOOL_CALL_END","run_id":"unique-request-id","call_id":"unique-call-id","tool":"read","success":true,"result":"Read 1200 characters","duration_ms":12}
```

```json
{"type":"KNOWLEDGE_ACCESS","run_id":"unique-request-id","path":"docs/requirements.md","success":true,"estimated_tokens":340,"reason":"File actually loaded"}
```

```json
{"type":"THINKING","run_id":"unique-request-id","phase":"Checking the requested change"}
```

THINKING — историческое имя события публичного прогресса. Не передавайте скрытые рассуждения модели. `REQUEST_MANIFEST` можно использовать для подготовленного контекста, но он не заменяет события чтения.

```json
{"type":"THINKING","run_id":"unique-request-id","usage_kind":"token_usage","model_call_id":"unique-model-call-id","input_tokens":1234,"output_tokens":100}
```

Usage отправляется один раз на завершённый ответ модели. Оценку не выдавать за данные провайдера; неизвестное поле не заполнять нулём. Ноль допустим, если провайдер действительно его сообщил.

```json
{"type":"GENERATION_END","run_id":"unique-request-id","success":true,"status":"completed","result":"Actual result summary"}
```

Для ошибки: `success:false`, `status:"failed"`. Завершение запроса не означает успех всех вложенных вызовов. Для ожидания уточнений допустим `status:"waiting_for_input"`.

## Python

Добавьте `<проект>/.agent-observer/src` в PYTHONPATH или в механизм импорта проекта. Импорт клиента: `from agent_observer.observer_client import ObserverClient`. Пример: [instrumented_agent.py](../examples/instrumented_agent.py).

`ObserverClient` ограничивает ожидание доставки и не ломает рабочий запрос при недоступности графа. `emit()` возвращает bool; `last_error` содержит только класс ошибки. Это не гарантия доставки: при недоступном сервере события не будут показаны. Контекст `run.tool()` должен охватывать настоящую операцию, а `run.document()` вызывается после фактического чтения.

## Агент без приложения / другой язык

Отправляйте тот же JSON из реальных callbacks среды инструментов. Для локальной ручной отправки факта из файла:

```bash
python observer.py emit --config "/path/to/project/.agent-observer/config/observer.project.json" --file event.json
```

CLI сам берёт локальный токен, не выводит его и не выполняет содержимое события. Одного промпта недостаточно, чтобы увидеть действия агента, если его среда не предоставляет callbacks или агент не отправляет события. Без такого подключения показывайте только те факты, которые доступны, и явно укажите ограничение.

## Дополнительные команды Codex

Граф различает восемь постоянно видимых стандартных действий и дополнительные типы инструментов, скрытые до реального вызова. Распознаются, в частности: `write_stdin`, `update_plan`, `web_search`, `list_mcp_resources`, `list_mcp_resource_templates`, `read_mcp_resource`, `request_user_input`, `tool_search`, операции делегирования (`spawn_agent`, `send_input`/`send_message`, `wait_agent`, `list_agents`, `resume_agent`, `close_agent`), композиция вызовов и именованные инструменты MCP. Учитываются квалифицированные имена вроде `functions.write_stdin`. `exec_command` и `shell_command` относятся к стандартному shell-блоку; `view_image` — к стандартному просмотру изображений.

Наличие каждого инструмента зависит от среды и подключённых расширений. Этот список является словарём распознавания, а не заявлением, что все инструменты включены в любом Codex. Неизвестная команда, явно помеченная источником как command, тоже может добавиться под собственным именем при событии вызова. События из текста THINKING и названия доступных инструментов не считаются вызовом. При просмотре другой истории состав дополнительных карточек строится по её событиям. История и инспектор сохраняют исходное имя, аргументы и результат.

Источники, просмотрены 24 октября 2026:

- [Codex Prompting Guide: shell, apply_patch, update_plan и расширяемые инструменты](https://developers.openai.com/cookbook/examples/gpt-5/codex_prompting_guide).
- [OpenAI Using tools: web search, MCP, tool search и композиция вызовов](https://developers.openai.com/api/docs/guides/tools).
- Имена доступных оркестрационных инструментов текущей среды; они не гарантируют ту же доступность в другой установке.

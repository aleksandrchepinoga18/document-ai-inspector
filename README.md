# Document AI Inspector

Демонстрационный MVP для проверки DOCX. Базовая спецификация —
[TECHNICAL_SPEC.md](TECHNICAL_SPEC.md), скопированная без изменений из
`TECHNICAL_SPEC_REVISED.md`.

Реализованы **этапы 1–5**: инфраструктура, безопасное чтение DOCX,
формальные правила по JSON-профилю, точечные правки размера шрифта,
OOXML-инварианты, скачивание, PDF через LibreOffice/PyMuPDF и опциональные
LLM-провайдеры с проверкой цитат, синтетический бенчмарк и MLflow Tracking.
Этап 1 принят пользователем: Compose работает, все 9 исходных тестов прошли.
UI-клиент пока не реализован.
Streamlit остаётся заглушкой этапа 1.

## Требования

- Windows 10/11, Docker Desktop с Linux containers и работающим Docker Engine.
- Git Bash; все команды ниже выполняются из корня этого проекта.
- Доступ к Docker Hub, Debian repositories и PyPI для первой сборки.
- Свободные локальные порты 8000, 8501, 5000.

Python на Windows для этапов 1–4 не нужен: контейнеры используют
Python 3.12.12. Первый образ достаточно большой: в него входят LibreOffice,
Streamlit, MLflow, исходники и тестовые зависимости.
Ollama нужен только для явно выбранного локального LLM-анализа;
формальная проверка и обычные тесты работают без моделей и облачных ключей.

## Запуск и ручная приёмка в Git Bash

Сначала запустите Docker Desktop и дождитесь готовности Engine.

```bash
cd '/c/workplace/AI_ Inspector_doc'
docker version
```

Ожидается информация о Client **и Server**. Если Server недоступен,
сначала устраните проблему Docker Desktop по разделу диагностики ниже.

При первом запуске создайте локальный файл настроек, если его ещё нет:

```bash
test -f .env || cp .env.example .env
docker compose config --quiet
docker compose up -d --build
docker compose ps
curl --fail http://localhost:8000/health
docker compose exec api python -m pytest -v
docker compose exec api python -m pytest --cov=backend --cov-report=term-missing
```

Ожидаемые результаты:

- `config --quiet` завершается без ошибки.
- После завершения запуска `ps` показывает `api`, `ui`, `mlflow` в состоянии
  `Up (healthy)`. Инициализация MLflow при первом запуске может занять больше
  времени; повторите `docker compose ps`.
- `/health` возвращает HTTP 200 и `{"status":"ok"}`.
- pytest завершается без ошибок, включая исходные 9 тестов и новые проверки
  этапов 2–4; external-тесты по умолчанию исключены.
- Проверка покрытия выводит таблицу; обязательный процент пока не задан.

Откройте в браузере:

- <http://localhost:8501> — заголовок Document AI Inspector и сообщение об этапе 1.
- <http://localhost:5000> — интерфейс MLflow. Метрик моделей пока нет.
- <http://localhost:8000/docs> — OpenAPI с маршрутом `/health`.

Проверка установленного LibreOffice:

```bash
docker compose exec api libreoffice --version
```

Ожидается номер версии LibreOffice без ошибки выполнения.

Просмотр логов и остановка:

```bash
docker compose logs -f api
```

`Ctrl+C` прекращает просмотр логов. Для остановки сервисов:

```bash
docker compose down
```

Именованный volume MLflow сохраняется после обычного `down`.
Не используйте `down -v`, если данные MLflow нужно сохранить.

## Конфигурация и хранение

`.env` читается Docker Compose; в контейнер API передаются только явно
перечисленные параметры. `pydantic-settings` также поддерживает чтение `.env`
при локальном запуске Python. Секреты в репозиторий не добавляются.

| Настройка | Значение по умолчанию | Назначение |
| --- | --- | --- |
| `APP_NAME` | `Document AI Inspector` | Название API |
| `PROCESSING_TIMEOUT_SECONDS` | `120` | Резерв для обработки документов, 1–600 секунд |
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434/v1` | Адрес Ollama на Windows-хосте из контейнера |
| `OLLAMA_MODEL` | пусто | Имя установленной локальной модели; обязательно при выборе Ollama |
| `MAX_UPLOAD_BYTES` | `20971520` | Не более 20 MiB на один DOCX |
| `MAX_ZIP_ENTRIES` | `1000` | Лимит элементов ZIP |
| `MAX_UNCOMPRESSED_BYTES` | `104857600` | Не более 100 MiB после распаковки |
| `MAX_ZIP_RATIO` | `200` | Максимальное отношение размера к сжатому размеру каждой части |
| `RENDER_TIMEOUT_SECONDS` | `60` | Лимит конвертации и проверки PDF, внутри общего processing timeout |
| `MAX_PDF_BYTES` | `52428800` | Максимальный размер PDF: 50 MiB |
| `MAX_PDF_PAGES` | `300` | Максимальное число страниц PDF |

Без `use_llm=true` запросы к моделям не выполняются. Yandex дополнительно
требует `allow_cloud=true` и `synthetic_data=true` в конкретном запросе.
Внутри сети Compose адрес API — `http://api:8000`, MLflow —
`http://mlflow:5000`. Эти адреса не предназначены для браузера Windows.
API запускается независимо от доступности MLflow.

Compose задаёт API `DATA_DIR=/app/results`. В отдельном volume
`documents_data` сохраняются UUID-каталоги с `original.docx`, снимком
профиля `profile.json`, `report.json` и успешными `corrected.docx`/`preview.pdf`.
Исходник записывается эксклюзивно и проверяется по SHA-256; имя загрузки
не используется как путь. Для отклонённого после сохранения документа
остаются исходник и `failure.json`, готового отчёта нет. Автоматической
очистки нет: размер хранилища нужно контролировать вручную.
Обычный `docker compose down` сохраняет оба volume.
При локальном запуске Python `DATA_DIR` по умолчанию равен `results`.

Все опубликованные порты привязаны к `127.0.0.1`. MLflow запускается от
непривилегированного пользователя `inspector` с UID/GID 10001; каталог
`/mlflow` в образе принадлежит ему. Именованный volume монтируется в этот
каталог; в нём находятся SQLite `/mlflow/mlflow.db` и `/mlflow/artifacts`.
MLflow обслуживает артефакты через HTTP, поэтому будущим клиентам не нужен
доступ к файловой системе сервера.

Проверка записи в volume от пользователя сервиса:

```bash
docker compose exec mlflow python -c "import tempfile; f = tempfile.TemporaryFile(dir='/mlflow'); f.write(b'ok'); f.seek(0); print(f.read().decode()); f.close()"
```

Ожидается `ok`; используется отдельный временный файл без перезаписи
существующих файлов. Для проверки сохранения БД после пересоздания контейнеров:

```bash
docker compose exec mlflow python -c "from pathlib import Path; p = Path('/mlflow/mlflow.db'); print('SQLite exists:', p.is_file(), 'bytes:', p.stat().st_size)"
docker compose down
docker compose up -d
docker compose ps
docker compose exec mlflow python -c "from pathlib import Path; p = Path('/mlflow/mlflow.db'); print('SQLite exists:', p.is_file(), 'bytes:', p.stat().st_size)"
```

Ожидается существующая непустая БД до и после перезапуска. Это проверка
инфраструктуры хранения; логирование бенчмарка будет реализовано на этапе 5.

`.dockerignore` разрешает только необходимые файлы сборки, а Dockerfile
копирует исходники явным списком. `.env`, DOCX, PDF, локальные окружения и
результаты не включаются в образ. Данные и конфиденциальные документы на
в образ не включаются. Генератор создаёт только синтетические DOCX.

## Этап 2: DOCX и формальные правила

`backend/documents/security.py` проверяет расширение/MIME, структуру ZIP,
обязательные OOXML-части, размер, количество частей, степень сжатия, CRC,
XML и связи. ZIP не извлекается на диск; DTD, XML-сущности, макросы,
выход за пределы пакета и внешние связи отклоняются. Помимо DOCX MIME
допускается `application/octet-stream`, но содержимое проверяется одинаково.
Весь multipart-запрос ограничен размером DOCX плюс 64 KiB на обрамление;
проверка работает и без Content-Length.

Разбор и правила выполняются в отдельном процессе с жёстким timeout
`PROCESSING_TIMEOUT_SECONDS`. Слишком большой документ по числу абзацев,
таблиц и runs также отклоняется: максимум 20 000 элементов.

`backend/documents/parser.py` читает основной текст, заголовки, обычные
таблицы и runs через python-docx/lxml. ID — `word/document.xml:` и XPath;
mapping ID → исходный OOXML-узел доступен в `ParsedDocument.node_map`.
В отчёте сохраняются ID, свойства и SHA-256 версии файла. ID воспроизводимы
для одних и тех же байтов документа, но не гарантируются между редакциями.

Профиль `backend/documents/profiles/default.json`, версия `1.0.0`, задаёт
семейство/размер шрифта, первую строку, выравнивание, стили заголовков,
обязательные разделы «Введение» и «Заключение», подпись непосредственно
перед таблицей вида «Таблица 1 — …». Это демонстрационный профиль,
не заявление о соответствии ГОСТ. Профиль можно менять в репозитории;
после изменения пересоберите образ и увеличьте его версию. API принимает
имя профиля, а не произвольный путь или загруженный конфиг.

Прямое форматирование, символьные/абзацные базовые стили и docDefaults
разрешаются по иерархии; поддерживается Latin/Cyrillic theme-font fallback.
Неопределимое свойство получает `status=undetermined`, `severity=info` и
`actual=null`; оно не объявляется установленной ошибкой оформления.
В отчёте отдельно перечисляются неподдерживаемые объекты: поля, формулы,
сноски, исправления, сложные/вложенные таблицы, рисунки, текстовые блоки,
списки и условное форматирование таблиц. Затронутые контейнеры исключаются
из обычных форматных правил. Колонтитулы/сноски/комментарии не проверяются.
Внешние гиперссылки сейчас тоже отклоняются — это намеренное ограничение.
Полная модель визуального оформления Word не реализована.

Смысловое представление пока использует текст с исходными ID из
python-docx. Docling и LLM на этом этапе не подключаются.
Без `auto_fix=true` файл не исправляется. `auto_fix_allowed=true` ставится
только для разрешённой правки размера шрифта; список разрешений описан ниже.

### Создание трёх примеров через Git Bash

После сборки и запуска Compose выполните:

```bash
docker compose exec -T api python -m samples.generate --output-dir results/stage2-samples
mkdir -p results/stage2-samples
MSYS_NO_PATHCONV=1 docker compose cp api:/app/results/stage2-samples/. ./results/stage2-samples
```

Появятся `valid.docx`, `format_errors.docx`, `structure_errors.docx` и
`expected.json`. Генератор отказывается перезаписывать существующие файлы.
Для повторной генерации выберите новый каталог в обеих командах.
`MSYS_NO_PATHCONV=1` предотвращает преобразование контейнерного пути Git Bash.

### Проверка документа и получение отчёта

```bash
response=$(curl --fail-with-body --silent --show-error \
  -F 'file=@results/stage2-samples/format_errors.docx;type=application/vnd.openxmlformats-officedocument.wordprocessingml.document' \
  -F 'profile=default' -F 'provider=none' -F 'use_llm=false' -F 'auto_fix=false' \
  http://localhost:8000/documents/check)
printf '%s\n' "$response"
JOB_ID=$(printf '%s' "$response" | docker compose exec -T api python -c 'import json,sys; print(json.load(sys.stdin)["job_id"])')
curl --fail-with-body "http://localhost:8000/documents/$JOB_ID/report"
```

POST возвращает HTTP **201**, UUID и полный отчёт; GET возвращает HTTP 200
с тем же отчётом. При ошибке POST не продолжайте извлечение UUID.
Ожидаемые поля (UUID, SHA-256 и время зависят от запуска):

```json
{
  "status": "completed",
  "profile": "default",
  "profile_version": "1.0.0",
  "parser": "python-docx",
  "use_llm": false,
  "auto_fix": false,
  "metrics": {"finding_count": 4, "undetermined_count": 0, "unsupported_count": 0}
}
```

В `findings` будут ровно четыре правила: `alignment` (center → justify),
`first_line_indent_mm` (0 → 12.5), `font_name` (Arial → Times New Roman),
`font_size_pt` (10 → 14). Каждое содержит `id`, `element_id`, `locations`,
цитату `evidence`, `actual`, `expected`, `source=rule`. У размера шрифта
`auto_fix_allowed=true`, у трёх остальных замечаний — `false`.
Например, замечание по шрифту указывает на run внутри второго абзаца:

```json
{
  "rule_id": "font_name",
  "element_id": "word/document.xml:/w:document/w:body/w:p[2]/w:r",
  "actual": "Arial",
  "expected": "Times New Roman",
  "source": "rule",
  "auto_fix_allowed": false,
  "status": "open"
}
```

Проверка остальных примеров:

```bash
curl --fail-with-body -F 'file=@results/stage2-samples/valid.docx;type=application/vnd.openxmlformats-officedocument.wordprocessingml.document' \
  -F 'use_llm=false' http://localhost:8000/documents/check
curl --fail-with-body -F 'file=@results/stage2-samples/structure_errors.docx;type=application/vnd.openxmlformats-officedocument.wordprocessingml.document' \
  -F 'use_llm=false' http://localhost:8000/documents/check
```

Для `valid.docx` ожидается пустой `findings`; для `structure_errors.docx` —
два замечания: `required_section` для «Заключение» и `table_caption`.
`status=completed` означает завершение проверки, а не отсутствие замечаний.

Проверка неизменности оригинала для `$JOB_ID` первого запроса:

```bash
docker compose exec -T -e JOB_ID="$JOB_ID" api python -c 'import hashlib,json,os; from pathlib import Path; d=Path("results")/os.environ["JOB_ID"]; r=json.loads((d/"report.json").read_text(encoding="utf-8")); print("SHA-256 matches:", hashlib.sha256((d/"original.docx").read_bytes()).hexdigest()==r["original_sha256"])'
```

Ожидается `SHA-256 matches: True`. Проверку можно повторить после
`docker compose restart api`; GET отчёта с тем же UUID должен работать.

Отрицательный запрос (переданный JSON не является DOCX):

```bash
curl -i -F 'file=@results/stage2-samples/expected.json;filename=broken.docx;type=application/vnd.openxmlformats-officedocument.wordprocessingml.document' \
  http://localhost:8000/documents/check
```

Ожидается HTTP 422 и `detail.code=invalid_docx`. Неизвестный профиль — 422,
неподдерживаемое расширение/MIME — 415, размер — 413, timeout — 504,
отсутствующий отчёт — 404. При `use_llm=false` задайте `provider=none`.
При `use_llm=true` явно выберите `ollama`, `vllm` или `yandex`.
`auto_fix=true` и `render_pdf=true` поддерживаются на этапе 3.

## Этап 3: безопасные исправления и PDF

Автоматическая правка разрешена только для `font_size_pt`: текстовый run
с обычным символьным стилем внутри абзаца `Normal` основного текста.
Заголовки, таблицы, списки, символьные стили, надстрочный/подстрочный текст,
неопределимые свойства и абзацы со сложными объектами не исправляются.
Семейство шрифта, выравнивание, отступы, текст, числа, таблицы и формулы
не изменяются автоматически.

`backend/documents/editor.py` меняет только `w:rPr/w:sz` в
`word/document.xml` и записывает новую ZIP-копию. Содержимое остальных
частей сохраняется побайтово; `python-docx.save()` для результата не
используется. Каждая правка содержит ID замечания, XPath, свойство,
значения до/после и статус.

`backend/documents/validator.py` заново определяет допустимые правки по
оригиналу и профилю. Проверяются состав пакета, текст/числа, количество
структурных элементов, таблицы, формулы, связи, медиа и все защищённые
части. После отмены только разрешённых изменений канонический XML должен
совпасть целиком. Побайтового совпадения всего ZIP не требуется.

При отказе проверки `correction.status=rejected`, `validation=failed`,
причины находятся в `correction.errors`. `download_url=null`; запрос
обычного `/download` возвращает 409. Исходник доступен по
`original_download_url` (`/download?original=true`); небезопасная копия
не публикуется. Если запрошен PDF, при отказе правки допускается только
предпросмотр исходника с `preview.source=original`.

`render_pdf=true` явно включает PDF; по умолчанию конвертация выключена.
LibreOffice запускается headless в отдельном временном каталоге с отдельным
профилем и timeout. В Linux при timeout завершается только его собственная
группа процессов. Макросы запрещены; документы с полями (в том числе PAGE),
OLE/embedded-объектами, altChunk и SVG отклоняются для PDF, чтобы не
выполнять DDE/INCLUDETEXT и другие активные инструкции. Это ограничение
конвертации не блокирует проверку DOCX и получение отчёта.
Обычные PNG и OMML допускаются; исправлять их содержимое редактору запрещено.

PyMuPDF проверяет открываемость PDF, отсутствие восстановления/шифрования,
число/размер страниц и декодирование каждой страницы с маленьким растром.
Ошибка LibreOffice/PDF сохраняется в `preview.error_code` и `message`,
`preview_url=null`. Исправленный DOCX при пройденных инвариантах остаётся
доступным. Скачивание DOCX/PDF дополнительно проверяет SHA-256 сохранённого
файла; повреждённый артефакт не выдаётся.

Поддерживаемая среда конвертации — Linux-контейнер API. Нативный запуск
LibreOffice на Windows не проверен. Используются параметры из
[официальной справки LibreOffice](https://help.libreoffice.org/latest/en-US/text/shared/guide/start_parameters.html)
и проверки из [API PyMuPDF](https://pymupdf.readthedocs.io/en/latest/document.html).
Успешный PDF и число страниц **не доказывают отсутствие дефектов вёрстки**.
Times New Roman не включён в образ: установленные Liberation/DejaVu могут
использоваться как замены, поэтому внешний вид нужно проверить вручную.
Для формул в образ явно добавлены LibreOffice Math и шрифт OpenSymbol.

### Ручная приёмка этапа 3 через Git Bash

```bash
docker compose up -d --build
docker compose ps
curl --fail http://localhost:8000/health
docker compose exec api python -m pytest -v
docker compose exec api python -m pytest --cov=backend --cov-report=term-missing
docker compose exec api libreoffice --version
```

Обычные pytest-тесты не запускают настоящий LibreOffice: конвертация
замокана, проверки PDF выполняются настоящим PyMuPDF. Дополнительный
opt-in тест реальной конвертации (без сети и LLM):

```bash
docker compose exec api python -m pytest -v -m external tests/integration/test_document_export.py::test_real_libreoffice_pdf_export
```

Создайте новый каталог синтетических примеров, включая таблицу, OMML и PNG:

```bash
RUN_DIR="results/stage3-$(date +%Y%m%d-%H%M%S)-$RANDOM"
docker compose exec -T api python -m samples.generate --include-protected --output-dir "$RUN_DIR"
mkdir -p "$RUN_DIR"
MSYS_NO_PATHCONV=1 docker compose cp "api:/app/$RUN_DIR/." "./$RUN_DIR"
```

Генератор создаёт прежние три файла и `protected_objects.docx`.
Он не перезаписывает существующие файлы. Отправьте защищённый пример:

```bash
curl --fail-with-body --silent --show-error \
  -F "file=@$RUN_DIR/protected_objects.docx;type=application/vnd.openxmlformats-officedocument.wordprocessingml.document" \
  -F 'profile=default' -F 'provider=none' -F 'use_llm=false' \
  -F 'auto_fix=true' -F 'render_pdf=true' \
  --output "$RUN_DIR/report.json" http://localhost:8000/documents/check
cat "$RUN_DIR/report.json"
JOB_ID=$(docker compose exec -T api python -c 'import json,sys; print(json.load(sys.stdin)["job_id"])' < "$RUN_DIR/report.json")
curl --fail-with-body --output "$RUN_DIR/corrected.docx" "http://localhost:8000/documents/$JOB_ID/download"
curl --fail-with-body --output "$RUN_DIR/original.docx" "http://localhost:8000/documents/$JOB_ID/download?original=true"
curl --fail-with-body --output "$RUN_DIR/preview.pdf" "http://localhost:8000/documents/$JOB_ID/preview"
cmp "$RUN_DIR/protected_objects.docx" "$RUN_DIR/original.docx"
```

При ошибке команды остановитесь и изучите ответ/логи перед следующим шагом.
Ожидается POST 201, скачивания 200 и молчаливый успешный `cmp` (код 0).
В отчёте — `correction.status=applied`, `validation=passed`, одна правка
10 → 14 pt, `fixed_count=1`, `open_count=3`. Остальные три замечания
потребуют ручной работы; в `findings` всего четыре замечания, одно со
`status=fixed`. `unsupported_features` содержит `drawing` и `formula`:
эти объекты сохранены без изменения. При успешной конвертации
`preview.status=available`, `source=corrected`, `pages>=1`.

Откройте исходный и исправленный DOCX и PDF: проверьте таблицу,
формулу `x + 2 = 42`, синий квадрат, числа `123.45` и `17`.
При недоступном PDF сначала изучите `preview.error_code` в отчёте и:

```bash
docker compose logs --tail=100 api
docker compose exec api libreoffice --version
docker compose exec api python -c 'import pymupdf; print(pymupdf.__version__)'
```

## Этап 4: LLM-провайдеры и смысловые замечания

`backend/llm/providers.py` содержит `LLMProvider.analyze()` и адаптеры
Ollama, vLLM, Yandex. Они используют совместимый Chat Completions API через
[OpenAI Python SDK](https://developers.openai.com/api/reference/python).
Модель и base URL берутся из окружения, модель по умолчанию не выбирается.
Для Ollama используется [официальный совместимый endpoint](https://docs.ollama.com/api/openai-compatibility),
для vLLM — [совместимый сервер](https://docs.vllm.ai/en/latest/serving/online_serving/).
GPU-развёртывание vLLM не выполняется и не требуется для обычных тестов.
Реальная работа GPU-сервера не проверялась.

Yandex подключается к `https://ai.api.cloud.yandex.net/v1` через API-ключ
и URI модели из каталога, как в
[официальном примере Yandex AI Studio](https://yandex.cloud/ru/docs/tutorials/serverless/create-ai-agent-function).
В `YANDEX_MODEL` допустим placeholder `{folder_id}`; он заменяется значением
`YANDEX_FOLDER_ID`. Идентификатор каталога должен совпадать с каталогом URI.
Это настройка совместимого API, дополнительные агенты/сервисы из примера
Yandex здесь не разворачиваются.

Смысловой анализ работает по оригиналу в отдельном read-only процессе
**после** формальных правил и безопасных правок. Ошибка/таймаут LLM
не отменяет формальный отчёт, исправленный DOCX или проверку инвариантов.
В prompt передаются текст, `element_id` и `section_id` поддерживаемых
абзацев основного документа; runs не дублируются. Сложные/неоднозначные
контейнеры исключаются. Требуются фрагменты минимум двух разделов.
Docling на этом этапе не подключён; используется извлечение python-docx
с сохранением исходных ID. Большие документы не усекаются автоматически:
если превышен лимит фрагментов, LLM пропускается с явной ошибкой,
а формальные результаты сохраняются.

Prompt версионирован (`cross-section-contradictions-v1`). Ответ должен
содержать только JSON по Pydantic-схеме `SemanticResponse`, например:

```json
{
  "issues": [{
    "error_type": "contradiction",
    "severity": "warning",
    "references": [
      {"element_id": "<ID из введения>", "quote": "Срок исполнения договора составляет 10 календарных дней."},
      {"element_id": "<ID из заключения>", "quote": "Срок исполнения договора составляет 30 календарных дней."}
    ],
    "explanation": "Для одного договора указаны разные сроки исполнения.",
    "suggestion": "Проверьте срок исполнения вручную."
  }]
}
```

Сервер проверяет существование ID именно среди отправленных фрагментов,
дословное вхождение каждой цитаты, разные разделы, отсутствие дубликатов
ссылок и соответствие схеме. Выдуманный ID, цитата, markdown-обёртка,
обрезанный ответ или поле с командой автоисправления отклоняют ответ целиком.
Повторные одинаковые замечания объединяются. Сырой ответ модели не сохраняется.

Принятые замечания попадают в общий `findings` с `source=llm`,
`category=semantic`, `rule_id=semantic_contradiction`, цитатами и
`auto_fix_allowed=false`. Даже `auto_fix=true` не разрешает им менять файл.
Проверенные цитаты подтверждают связь с текстом, но не доказывают, что
утверждения действительно противоречат друг другу: нужна ручная оценка.
`issues=[]` означает отсутствие найденных моделью замечаний и не является
доказательством корректности документа. Оценка качества/benchmark — этап 5.

### Настройки LLM

Добавьте нужные новые переменные из `.env.example` в существующий `.env`,
сохранив свои настройки. Ключ OpenAI для этих адаптеров не требуется.

| Переменные | Назначение |
| --- | --- |
| `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, `OLLAMA_API_KEY` | Адрес, имя локальной модели, опциональная авторизация |
| `VLLM_BASE_URL`, `VLLM_MODEL`, `VLLM_API_KEY` | Адрес существующего сервера, модель, опциональная авторизация |
| `YANDEX_BASE_URL`, `YANDEX_FOLDER_ID`, `YANDEX_MODEL`, `YANDEX_API_KEY` | Совместимый endpoint, каталог, полный URI модели, ключ |
| `LLM_TIMEOUT_SECONDS=30` | Timeout одной попытки |
| `LLM_TOTAL_TIMEOUT_SECONDS=45` | Жёсткий предел LLM-процесса внутри общего processing timeout |
| `LLM_MAX_RETRIES=0` | Дополнительные попытки при timeout/соединении/5xx; 401/403/429 не повторяются |
| `LLM_TEMPERATURE=0` | Температура генерации |
| `LLM_MAX_TOKENS=1024` | Предел выхода модели |
| `LLM_MAX_INPUT_CHARS=20000` | Предел сериализованных фрагментов; фиксированная инструкция/схема добавляются отдельно |
| `LLM_MAX_FRAGMENTS=200` | Предел числа фрагментов |
| `LLM_MAX_RESPONSE_CHARS=50000` | Предел принимаемого текста ответа |
| `LLM_JSON_MODE=true` | Отправлять `response_format={"type":"json_object"}` |

Если сервер/модель не принимает `response_format`, задайте
`LLM_JSON_MODE=false`: локальная проверка JSON, схемы и цитат остаётся.
SDK retries отключены; повторения контролирует приложение. Квоты/авторизация
не повторяются автоматически. `Retry-After` при повторяемой ошибке учитывается;
если ожидание выходит за deadline, новой попытки нет. При принудительном
завершении worker число уже отправленных попыток неизвестно: `attempts=null`.
Для медленного холодного старта увеличьте
LLM timeout и при необходимости общий `PROCESSING_TIMEOUT_SECONDS` в `.env`.

Ollama/vLLM допускают локальные/внутренние адреса (loopback, private IP,
Docker service names, `.internal`); публичные адреса блокируются. Ollama
модели с cloud-суффиксом также блокируются. Администратор должен выбрать
действительно локальную модель и доверенный сервер: приложение не проверяет
внутреннюю маршрутизацию/алиасы сервера. Redirect и HTTP proxy из окружения
для SDK-клиента отключены. Автоматического переключения в облако нет.

Yandex допускается только с `use_llm=true`, `provider=yandex`,
`allow_cloud=true`, `synthetic_data=true` в одном запросе. Последний флаг —
подтверждение пользователя, а не автоматическое распознавание чувствительных
данных. Отправляйте только сгенерированные демонстрационные документы.
Ключи не включаются в prompt, stdout, report, профиль или JSON-запрос worker;
LLM-worker получает выбранный ключ через окружение. Ответ с отражённым
служебным ключом отклоняется. Не публикуйте `.env` и полный вывод
`docker compose config`: используйте `config --quiet`.

После изменения `.env` примените настройки через `docker compose up -d api`;
одного `restart` недостаточно для обновления окружения контейнера.

### Самостоятельная проверка Ollama через Git Bash

1. Запустите Ollama на Windows и выберите установленную локальную модель:

```bash
curl --fail http://localhost:11434/api/tags
```

2. В `.env` укажите её точное имя в `OLLAMA_MODEL` и оставьте контейнерный
адрес `OLLAMA_BASE_URL=http://host.docker.internal:11434/v1`.
Затем выполните:

```bash
docker compose up -d --build
docker compose exec api python -m pytest -v
RUN_DIR="results/stage4-$(date +%Y%m%d-%H%M%S)-$RANDOM"
docker compose exec -T api python -m samples.generate --include-semantic --output-dir "$RUN_DIR"
mkdir -p "$RUN_DIR"
MSYS_NO_PATHCONV=1 docker compose cp "api:/app/$RUN_DIR/." "./$RUN_DIR"
curl --fail-with-body -sS \
  -F "file=@$RUN_DIR/semantic_conflict.docx;type=application/octet-stream" \
  -F 'provider=ollama' -F 'use_llm=true' -F 'auto_fix=false' \
  --output "$RUN_DIR/ollama-report.json" http://localhost:8000/documents/check
cat "$RUN_DIR/ollama-report.json"
```

`semantic_conflict.docx` содержит 10 и 30 дней в двух разделах;
`semantic_consistent.docx` — 10 дней в обоих. Оба не имеют формальных ошибок.
В `expected.json` описаны только формальные правила, не метрики LLM.
Ожидается HTTP 201 и `llm.status=completed`, `json_valid=true`,
`references_valid=true`. Если модель нашла противоречие, `findings` содержит
семантическое замечание с двумя реальными ID и цитатами. Количество замечаний
не гарантируется: это реальный ответ модели, не фиктивный результат.
Повторите запрос с `semantic_consistent.docx` для отрицательного примера.

Отдельный opt-in тест реальной Ollama, также с синтетическими данными:

```bash
docker compose exec -e RUN_OLLAMA_TESTS=1 api python -m pytest -v -m external tests/integration/test_llm_external.py::test_real_ollama
```

### Отдельная проверка Yandex после настройки ключа

Настройте в `.env` `YANDEX_FOLDER_ID`, `YANDEX_API_KEY`, `YANDEX_MODEL`.
URI модели возьмите из своего каталога, например шаблон
`gpt://{folder_id}/<выбранная-модель>/<версия>`. Используется API-ключ
сервисного аккаунта с доступом к выбранной модели; в официальном примере
указаны роль `ai.languageModels.user` и scope `yc.ai.languageModels.execute`.
Base URL из `.env.example` — `https://ai.api.cloud.yandex.net/v1`.
Ниже команды **реально вызывают платный API**, выполняйте их только вручную
с уже созданным синтетическим `semantic_conflict.docx`:

```bash
docker compose up -d api
curl --fail-with-body -sS \
  -F "file=@$RUN_DIR/semantic_conflict.docx;type=application/octet-stream" \
  -F 'provider=yandex' -F 'use_llm=true' -F 'allow_cloud=true' \
  -F 'synthetic_data=true' -F 'auto_fix=false' \
  --output "$RUN_DIR/yandex-report.json" http://localhost:8000/documents/check
cat "$RUN_DIR/yandex-report.json"
```

Либо отдельный реальный тест (тоже платный):

```bash
docker compose exec -e RUN_YANDEX_TESTS=1 -e ALLOW_PAID_TESTS=1 api python -m pytest -v -m external tests/integration/test_llm_external.py::test_real_yandex
```

Достаточно выбрать один из этих способов проверки. Без всех подтверждений
Yandex-запрос не отправляется. Обычный pytest исключает `external`; даже
при ручном `-m external` реальные LLM-тесты дополнительно требуют своих
флагов запуска. Наличие ключа в `.env` само по себе не вызывает модель.

### Ошибки и fallback

При недоступной модели HTTP 201 всё ещё означает готовый формальный отчёт;
смотрите **`llm.status` и `llm.error_code`**, а не только HTTP-код:

| Код | Значение |
| --- | --- |
| `llm_not_configured` | Нет обязательной модели/base URL/ключа/каталога |
| `llm_auth_failed` | Провайдер вернул 401/403 |
| `llm_quota_or_rate_limit` | Провайдер вернул 429; автоматического повторения нет |
| `llm_unavailable` | Ошибка соединения или 5xx |
| `llm_model_or_endpoint_not_found` | Провайдер вернул 404 |
| `llm_request_rejected` | Несовместимые параметры или другой отказ HTTP |
| `llm_timeout` | Истёк timeout попытки или всего LLM-процесса |
| `invalid_llm_json` | JSON/схема ответа невалидны |
| `invalid_llm_reference`, `invalid_llm_evidence` | Нет подтверждения ID/цитат/разных разделов |
| `incomplete_llm_response` | Обрезанный ответ, отказ или запрос инструмента |
| `llm_input_too_large` | Превышен размер/число фрагментов, вызов не выполнялся |
| `insufficient_sections` | `status=skipped`, не хватает поддерживаемых разделов |

Без подтверждения облака API возвращает 403 `cloud_consent_required` до
проверки документа; неизвестный/непоследовательный выбор провайдера — 422.
Прочие ограничения URL/cloud-моделей и внутренние ошибки также явно
отражаются в `llm.error_code` без текста SDK-исключения или ключей.

Если Ollama доступна на Windows, но не из контейнера, проверьте адрес
`host.docker.internal`, настройки bind Ollama и Windows Firewall. Затем:

```bash
docker compose logs --tail=100 api
docker compose exec api python -c 'import os,urllib.request; print(urllib.request.urlopen(os.environ["OLLAMA_BASE_URL"].rstrip("/")+"/models", timeout=5).read().decode())'
```

Для vLLM задайте его адрес/модель в `.env` и используйте тот же синтетический
POST с `provider=vllm`. Это опциональная проверка существующего сервера,
а не команда развёртывания GPU.

## Воспроизводимость зависимостей

`requirements.in` содержит прямые зависимости. `requirements.txt` содержит
все разрешённые транзитивные версии, платформенные маркеры и SHA-256.
В образе установка выполняется через `pip --require-hashes`.
SQLAlchemy закреплена на 2.0.44: MLflow 3.5.1 использует API пула,
удалённый в SQLAlchemy 2.1. Офлайн smoke-тест проверяет создание и повторное
открытие SQLite-хранилища, сохранение параметра тестового run и чтение
синтетического артефакта по восстановленному URI. Тест использует только
временный каталог pytest, не обращается к серверу и Docker volume.
Базовый образ закреплён как `python:3.12.12-slim-bookworm`.
Пакеты Debian устанавливаются из bookworm repositories, поэтому их сборки
могут меняться при обновлении репозитория; полная побайтовая повторяемость
образа не заявляется.

При намеренном обновлении зависимостей файл можно пересоздать с Python 3.12
и `uv==0.9.5`:

```bash
uv pip compile requirements.in --python-version 3.12 --universal --generate-hashes --output-file requirements.txt --cache-dir .uv-cache
```

## Если сервисы не запускаются

Выполняйте диагностику последовательно:

```bash
docker version
docker context show
docker compose config --quiet
docker compose ps -a
docker compose logs --tail=100 api ui mlflow
```

- Ошибка подключения к `dockerDesktopLinuxEngine`/`docker_engine`: запустите
  Docker Desktop, дождитесь готовности и проверьте режим Linux containers.
- Ошибка скачивания образа, apt или pip: проверьте доступ в интернет и
  настройки proxy Docker Desktop; повторите сборку с подробным выводом:

```bash
docker compose --progress plain build
```

- `port is already allocated`: освободите соответствующий порт и повторите запуск.
- `ui` ожидает зависимость: сначала проверьте логи и healthcheck API.
- MLflow сообщает `Permission denied` или SQLite `unable to open database`:
  проверьте пользователя и каталог, не удаляя volume:

```bash
docker compose run --rm --no-deps mlflow id
docker compose run --rm --no-deps mlflow ls -ld /mlflow /mlflow/artifacts
```

  Ожидается UID 10001 и возможность записи в `/mlflow` от этого пользователя.
  При ошибке передайте вывод команд для адресного исправления прав.
- При ошибке `/health`:

```bash
curl -i http://localhost:8000/health
docker compose logs --tail=100 api
```

Передайте использованную команду и её вывод, предварительно убрав секреты.
Следующий этап начинается только после вашего подтверждения приёмки.

## Статус проверки этапа 2

Этап 1 после восстановления принят пользователем: Docker Compose работает,
все девять исходных pytest-тестов прошли. Это подтверждение предыдущего
этапа; новые изменения требуют отдельной проверки.

На этапе 2 локально проверены синтаксис Python, импорты/OpenAPI,
согласованность Compose/Dockerfile и отсутствие изменений всех 101 прежних
закреплённых версий. Добавлены и установлены с проверкой SHA-256 три
зависимости: python-docx 1.2.0, lxml 6.1.3, python-multipart 0.0.33.
Прямой вызов валидатора, парсера и rules engine на трёх синтетических
документах дал ожидаемые 0, 4 и 2 замечания без неподдерживаемых объектов.
Также локально проверен полный checker с отдельным worker-процессом,
записью/чтением отчёта и побайтовой неизменностью оригинала во временном
каталоге Windows. `uv pip check` подтвердил совместимость 103 установленных
пакетов; `git diff --check` не выявил ошибок пробелов.
Локальный HTTP-обработчик проверен через TestClient: multipart POST → 201,
четыре реальных замечания и GET → 200; повреждённый DOCX → 422;
слишком большой streaming multipart без Content-Length → 413.
Эти проверки не подтверждают работу Linux-контейнеров.

Добавлены unit/integration pytest-тесты для наследования стилей,
неопределимых свойств, схем, стабильных ID, таблиц/заголовков, негативных ZIP,
XML и внешних связей, multipart API, постоянного отчёта, неизменности
оригинала, streaming upload и timeout. Тесты автономны и не вызывают LLM.

Docker Compose и pytest на этапе 2 **не запускались**: их запускает
пользователь через Git Bash. Новое покрытие, сборка Linux-образа, права
volume `documents_data` и работа HTTP API в контейнере пока не подтверждены.
Команды самостоятельной приёмки приведены выше; при ошибке передайте лог.
Эти результаты относятся к этапу 2 до начала этапа 3.

## Статус проверки этапа 3 — 10.11.2026

Добавлены editor, независимый validator, renderer, маршруты скачивания,
журнал правок и тесты для отказа при нарушении инвариантов. Исходные
версии зависимостей сохранены, добавлен PyMuPDF 1.26.7 для PDF-проверок.
Все данные в существующих volume сохранены.

Локально проверены синтаксис/импорты, исправление 10 → 14 pt в отдельном
worker, успешная проверка инвариантов, сохранение остальных трёх замечаний,
скачивание копии и побайтовая неизменность оригинала. Защищённый пример
с OMML, PNG и таблицей также прошёл инварианты с одной разрешённой правкой.
Локальный сценарий с подменённой конвертацией проверил настоящий PyMuPDF
и выдачу PDF по HTTP; это не запуск LibreOffice. Инъекция изменения
защищённого стиля привела к отказу инвариантов, HTTP 409 для исправленной
копии и доступности исходника. Повреждение сохранённого результата также
блокируется с HTTP 409.
LibreOffice на хосте не найден: проверен корректный отказ PDF при сохранении
доступности DOCX/отчёта. Настоящая конвертация в этой среде не подтверждена.

Docker Compose и pytest по договорённости запускает пользователь через
Git Bash; на этапе 3 они не запускались. Проверка Linux-образа, реального
PDF и визуальной вёрстки остаётся частью ручной приёмки выше.
Эти результаты относятся к этапу 3 до начала этапа 4.

## Статус проверки этапа 4 — 10.11.2026

Реализованы адаптеры, read-only LLM-worker с жёстким deadline, версионированный
prompt, типизированные противоречия и проверка цитат, fallback и per-request
подтверждение облака. Добавлены два синтетических примера, автономные
unit/integration tests и opt-in external tests. UI, benchmark и MLflow
логирование моделей не добавлялись: это последующие этапы.

Локально SDK проверен для всех трёх провайдеров только через
`httpx.MockTransport`: валидное замечание принято; ошибки 401/429/503
преобразованы в безопасные коды без скрытых повторений. Проверен настоящий
локальный worker при пустой модели: без сетевого запроса сохранены четыре
формальных замечания, успешная форматная правка, скачивание DOCX и исходник.
Yandex без подтверждений возвращает 403 до LLM-фазы.
Локальный multipart API с SDK/HTTP mocks проверен для всех трёх провайдеров:
201, одно замечание с подтверждёнными цитатами, GET отчёта и побайтовое
совпадение скачанного DOCX с оригиналом даже при `auto_fix=true`.

Реальных запросов Ollama/Yandex/vLLM, Docker Compose и pytest на этапе 4
не выполнялось. Эти проверки выполняет пользователь по командам выше.
Работа реальных моделей, права/квоты Yandex и GPU-инференс не подтверждены.
Этапы 5–6 не начинались.

## Этап 5: синтетический benchmark и MLflow

Реализованы 24 синтетических DOCX-кейса с авторской разметкой: 12 с противоречием,
8 без противоречия и 4 неоднозначных. CLI использует существующий LLM-worker
этапа 4, проверку схемы и цитат; rules engine в оценку не входит.
Правила сопоставления, знаменатели и состав артефактов описаны в
[evaluation/README.md](evaluation/README.md).
Следующий этап UI не начат.

В Git Bash пересоберите образ с этапом 5 и запустите автономные тесты:

```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose exec api python -m pytest -v
docker compose exec api python -m pytest -v tests/unit/test_evaluation_metrics.py tests/unit/test_benchmark.py tests/integration/test_benchmark_mlflow.py
```

Ожидается успешный pytest без запросов к моделям. Новый integration test использует
реальный MLflow client, временные SQLite и файловое хранилище, без Tracking Server.
Проверка текущего сервера и его volume выполняется отдельно следующей командой:

```bash
curl --fail http://localhost:5000/health
docker compose exec api python -m evaluation.benchmark --dry-run
```

Ожидается строка `MOCK (no real model)` и код 0. В `http://localhost:5000` появится
эксперимент `semantic-benchmark-mock`: 24 кейса, TP=0, FP=0, FN=12, JSON-valid=1,
references-valid=1. Это пустой mock baseline, его F1 не оценивает реальную LLM.
Без Tracking Server можно выполнить:

```bash
docker compose exec api python -m evaluation.benchmark --dry-run --no-mlflow
```

Для реальной локальной Ollama сначала установите/запустите выбранную локальную
модель и задайте её точное имя в `OLLAMA_MODEL` файла `.env`. Адрес для контейнера:
`OLLAMA_BASE_URL=http://host.docker.internal:11434/v1`. Затем:

```bash
docker compose up -d --build
docker compose exec api python -m evaluation.benchmark --provider ollama
```

Ожидается `REAL synthetic benchmark`, локальные JSON/CSV и новый run эксперимента
`semantic-benchmark`. Числа TP/FP/FN зависят от фактических ответов модели.
Один запуск обрабатывает все 24 кейса, по одному вызову на кейс при
`LLM_MAX_RETRIES=0`; повторения и задержки ограничены настройками этапа 4.
Для длинных ответов при необходимости увеличьте `LLM_MAX_TOKENS` одинаково для
сравниваемых моделей, затем пересоздайте контейнер API.

Yandex запускается только по вашему явному выбору; это **платные запросы**.
Настройте `.env`: `YANDEX_BASE_URL`, `YANDEX_FOLDER_ID`, `YANDEX_MODEL`,
`YANDEX_API_KEY` по разделу этапа 4. Не передавайте ключи аргументами CLI.

```bash
docker compose up -d --build
docker compose exec api python -m evaluation.benchmark --provider yandex --allow-paid
```

Без `--allow-paid` CLI откажет до сетевого запроса. Ожидается отдельный реальный
run того же эксперимента с теми же версиями набора, prompt и matching.
Наличие ключей само по себе не запускает запросы. Для настроенного локального
vLLM доступен `--provider vllm`; GPU-развёртывание не выполнялось.

Путь результатов печатается CLI; он находится в существующем volume
`documents_data` под `/app/results/benchmarks`. Для просмотра summary:

```bash
docker compose exec api python -c 'import json,pathlib; p=max(pathlib.Path("results/benchmarks").glob("*/summary.json"),key=lambda p:p.stat().st_mtime); print(p); print(json.dumps(json.loads(p.read_text(encoding="utf-8")),ensure_ascii=False,indent=2))'
```

В MLflow проверьте параметры model/provider, dataset/prompt hashes и версии,
TP/FP/FN, precision/recall/F1, JSON/reference rates, latency mean/p95, ошибки
и четыре JSON/CSV артефакта. Обычный API документов не зависит от MLflow.

При коде 1 результаты сохранены: проверьте `cases.json` и `tracking.json` в
напечатанном каталоге. При проблеме tracking:

```bash
docker compose ps
curl --fail http://localhost:5000/health
docker compose logs --tail=100 mlflow
docker compose exec api python -c 'import urllib.request; print(urllib.request.urlopen("http://mlflow:5000/health",timeout=5).read().decode())'
```

При ошибках Ollama проверьте её доступность на хосте, точное имя модели и
`OLLAMA_BASE_URL`; при Yandex — код ошибки авторизации/квоты в cases.json.
Код 2 означает отказ CLI или ошибку конфигурации/подготовки.
Не удаляйте volume для исправления соединения.

### Фактическая проверка этапа 5 — 10.11.2026

В локальной Windows-среде Python 3.12 выполнены новые тесты этапа 5:
**16 passed**. Проверены one-to-one matching, FP отрицательных кейсов, FN при
сбоях, отдельный учёт неоднозначных случаев, стабильность DOCX и цитат,
защита CLI от неявных/платных вызовов, mock и сохранение локальных результатов
при сбое tracking. Реальный MLflow client записал метрики и четыре артефакта
во временные SQLite/локальное хранилище; повторное чтение и скачивание прошли.

Отдельный CLI `--dry-run --no-mlflow` выполнен: 24 кейса, TP=0, FP=0, FN=12,
JSON-valid/reference-valid=1, failed_case_count=0. Это проверка mock, а не моделей.
`git diff --check` прошёл.

Полный pytest, выполненный до добавления последнего теста benchmark, дал
**165 passed, 1 failed, 3 deselected**. Сбой существующего теста
`test_zip_paths_are_never_extracted[dir\\bad.xml]` связан с созданием ZIP на
Windows: `zipfile` нормализует обратный слеш до проверки, поэтому ожидаемый
`unsafe_zip_path` заменяется на `invalid_xml`. Этот тест и код безопасности
не изменялись в этапе 5; успешность полного набора в Linux нужно подтвердить
командой Docker выше.

Docker Compose, Tracking Server по HTTP, постоянный Docker volume и реальные
Ollama/Yandex/vLLM в этом этапе не запускались. Их ручная проверка остаётся
за пользователем по приведённым командам; реальных метрик моделей пока нет.

### Разбор сохранённого запуска Ollama этапа 5

Пользователь предоставил run `real-7d083fee55ad4e90ae91595cfb0a91fc`.
[Отчёт об ошибках и команды проверки](evaluation/reports/ollama-stage5-analysis.md)
содержит анализ пяти отказов grounding, трёх смысловых FP и исправление matcher
для цитат без конечной точки. Ground truth, baseline prompt и исходный run
сохранены. Новые запуски используют версию matcher v2 и подробные счётчики
ошибок валидации; [отдельное предложение prompt v2](evaluation/prompts/contradictions-v2-proposal.md)
не активировано. Офлайн-пересчёт прежних ответов даёт 9/3/3 вместо 8/4/4;
это исправление оценки, не результат нового запуска LLM. Этап 6 не начат.

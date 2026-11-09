# Document AI Inspector

Демонстрационный MVP для проверки DOCX. Базовая спецификация —
[TECHNICAL_SPEC.md](TECHNICAL_SPEC.md), скопированная без изменений из
`TECHNICAL_SPEC_REVISED.md`.

Реализованы **этапы 1–2**: инфраструктура, безопасное чтение DOCX,
формальные правила по JSON-профилю, API проверки и постоянные отчёты.
Этап 1 принят пользователем: Compose работает, все 9 исходных тестов прошли.
Редактор, PDF, LLM, бенчмарк и UI-клиент не реализованы.
Streamlit остаётся заглушкой этапа 1.

## Требования

- Windows 10/11, Docker Desktop с Linux containers и работающим Docker Engine.
- Git Bash; все команды ниже выполняются из корня этого проекта.
- Доступ к Docker Hub, Debian repositories и PyPI для первой сборки.
- Свободные локальные порты 8000, 8501, 5000.

Python на Windows и Ollama для этапов 1–2 не нужны: контейнеры используют
Python 3.12.12. Первый образ достаточно большой: в него входят LibreOffice,
Streamlit, MLflow, исходники и тестовые зависимости.

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
  этапа 2; external-тесты по умолчанию исключены.
- Проверка покрытия выводит таблицу; обязательный процент пока не задан.

Откройте в браузере:

- <http://localhost:8501> — заголовок Document AI Inspector и сообщение об этапе 1.
- <http://localhost:5000> — интерфейс MLflow. Метрик моделей пока нет.
- <http://localhost:8000/docs> — OpenAPI с маршрутом `/health`.

Проверка установленного LibreOffice (конвертация пока не реализована):

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
| `OLLAMA_MODEL` | пусто | Модель выбирается позднее |
| `MAX_UPLOAD_BYTES` | `20971520` | Не более 20 MiB на один DOCX |
| `MAX_ZIP_ENTRIES` | `1000` | Лимит элементов ZIP |
| `MAX_UNCOMPRESSED_BYTES` | `104857600` | Не более 100 MiB после распаковки |
| `MAX_ZIP_RATIO` | `200` | Максимальное отношение размера к сжатому размеру каждой части |

Этапы 1–2 не выполняют запросы к Ollama или платным API.
Внутри сети Compose адрес API — `http://api:8000`, MLflow —
`http://mlflow:5000`. Эти адреса не предназначены для браузера Windows.
API запускается независимо от доступности MLflow.

Compose задаёт API `DATA_DIR=/app/results`. В отдельном volume
`documents_data` сохраняются UUID-каталоги с `original.docx` и `report.json`.
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
Ни один файл не исправляется; все `auto_fix_allowed=false`.

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
цитату `evidence`, `actual`, `expected`, `source=rule` и `auto_fix_allowed=false`.
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
отсутствующий отчёт — 404. `use_llm=true`, `auto_fix=true` и
`provider`, отличный от `none`, отклоняются с 422 `unsupported_mode`.
Маршрутов скачивания и PDF пока нет.

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
Этапы 3–6 не начинались.

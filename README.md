# Document AI Inspector

Демонстрационный MVP для проверки DOCX. Базовая спецификация —
[TECHNICAL_SPEC.md](TECHNICAL_SPEC.md), скопированная без изменений из
`TECHNICAL_SPEC_REVISED.md`.

Реализован **только этап 1**: каркас пакетов, FastAPI `/health`, заглушка
Streamlit, настройки окружения, Docker Compose, локальный MLflow и smoke-тесты.
Обработка DOCX, исправления, PDF, LLM и бенчмарк пока не реализованы.
Заглушка интерфейса не выдаёт результатов проверки документов.

## Требования

- Windows 10/11, Docker Desktop с Linux containers и работающим Docker Engine.
- Git Bash; все команды ниже выполняются из корня этого проекта.
- Доступ к Docker Hub, Debian repositories и PyPI для первой сборки.
- Свободные локальные порты 8000, 8501, 5000.

Python на Windows и Ollama для этапа 1 не нужны: контейнеры используют
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
- pytest завершается с `9 passed`; external-тесты по умолчанию исключены.
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

Этап 1 не выполняет запросы к Ollama или платным API.
Внутри сети Compose адрес API — `http://api:8000`, MLflow —
`http://mlflow:5000`. Эти адреса не предназначены для браузера Windows.
API запускается независимо от доступности MLflow.

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
этом этапе не загружаются.

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

## Восстановление после аварийного выключения — 09.11.2026

До восстановления уже существовали все основные компоненты команды №1:
каркас `backend`, `frontend`, `evaluation`, `samples`, `tests`,
`infrastructure/docker`; FastAPI с точным контрактом `/health`;
заглушка Streamlit; `pydantic-settings`; Dockerfile с LibreOffice и тестовыми
зависимостями; Compose с сервисами `api`, `ui`, `mlflow`, healthcheck и
постоянным volume; `.env.example`, `.gitignore`, `.dockerignore`,
закреплённые зависимости, `pytest.ini`, пять smoke-тестов и этот README.

При анализе файлов не обнаружено отсутствующих обязательных компонентов
этапа 1. Частично покрыты тестами загрузка настроек и сохранность MLflow;
подтверждение сборки и запуска контейнеров оставалось незавершённым.

При восстановлении сохранены существующие исходники, Dockerfile, Compose
и версии зависимостей. Дополнены проверки границ timeout, чтения UTF-8
`.env`, приоритета окружения, применения имени API и сохранности run/артефакта
MLflow. Исправлены инструкции проверки и убрано утверждение об успешном
прохождении старых тестов как о результате текущего восстановления.
Пользовательские файлы, локальные окружения и Docker volumes сохранены.

Проверки текущего восстановления: синтаксис Python, согласованность
Compose/Dockerfile, закреплённых зависимостей и установленного Python 3.12.12.
Это статические проверки; они не подтверждают сборку Linux-образа.

По договорённости Docker Compose и pytest запускает пользователь через
Git Bash. В ходе восстановления они **не запускались**; `9 passed` выше —
ожидаемый результат. Запуск сервисов, установка зависимостей внутри образа,
LibreOffice, права и сохранность Docker volume требуют ручной проверки.
Новых результатов покрытия пока нет. Наличие старых `.coverage` и
`.pytest_cache` не подтверждает прохождение обновлённых тестов.

В каталоге проекта не обнаружен `.git`; Git-история и diff относительно
коммита недоступны. Инициализация Git не выполнялась.
Этапы 2–6 не реализовывались; следующий этап начинается после вашей приёмки.

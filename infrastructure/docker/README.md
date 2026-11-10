# Docker

Все три сервиса используют общий корневой `Dockerfile` и образ stage6.
Конфигурация запуска находится в корневом `docker-compose.yml`.
LibreOffice установлен в образе; этап 3 использует его для опционального
PDF (`render_pdf=true`) с отдельным временным профилем и timeout.
SQLite и артефакты MLflow хранятся в именованном volume `mlflow_data`.
Оригиналы DOCX, отчёты и сгенерированные примеры этапа 2 хранятся в отдельном
volume `documents_data`, смонтированном в API как `/app/results`.
Там же сохраняются проверенные исправленные DOCX, журнал в отчёте и PDF.
Оба volume сохраняются после обычного `docker compose down`.

UI обращается к `http://api:8000`, на Windows API опубликован по
`http://localhost:18000`; UI — порт 8501, MLflow — порт 5000.
Интерфейс не получает ключи LLM. Его HTTP timeout 630 секунд покрывает
максимальный backend timeout 600 секунд. Полная приёмка и диагностика:
[этап 6](../../docs/FINAL_ACCEPTANCE.md).

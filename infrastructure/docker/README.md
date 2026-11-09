# Docker

На этапах 1–2 все три сервиса используют общий корневой `Dockerfile`.
Конфигурация запуска находится в корневом `docker-compose.yml`.
LibreOffice установлен в образе; обработка документов появится на этапе 3.
SQLite и артефакты MLflow хранятся в именованном volume `mlflow_data`.
Оригиналы DOCX, отчёты и сгенерированные примеры этапа 2 хранятся в отдельном
volume `documents_data`, смонтированном в API как `/app/results`.
Оба volume сохраняются после обычного `docker compose down`.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# UID/GID непривилегированного пользователя. Вынесены в build-args, потому что
# /data приходит бинд-маунтом с хоста (см. docker-compose.yml): образ не может
# сменить владельца смонтированного каталога, поэтому UID в контейнере должен
# совпадать с владельцем ./data на хосте. По умолчанию 1000 — типичный первый
# пользователь Linux; при несовпадении собирайте с APP_UID=$(id -u).
ARG APP_UID=1000
ARG APP_GID=1000

WORKDIR /app

COPY backend/requirements.txt ./backend/requirements.txt
COPY doc-parser ./doc-parser
RUN pip install --no-cache-dir -e ./doc-parser && \
    pip install --no-cache-dir -r backend/requirements.txt

COPY backend/app ./app
COPY backend/prompts ./prompts
# Миграции и скрипты предзаполнения обязательны при деплое
# (docs/PRODUCTION_DEPLOYMENT.md §7) и потому должны быть в образе: иначе внутри
# контейнера нечем выполнить ни `alembic upgrade head`, ни сид справочников.
# Раскладка не случайна: alembic.ini лежит рядом с app/, его script_location
# (%(here)s/alembic) и prepend_sys_path=. дают рабочий alembic из WORKDIR /app;
# скрипты сами кладут в sys.path родителя scripts/ — тот же /app.
COPY backend/alembic.ini ./alembic.ini
COPY backend/alembic ./alembic
COPY backend/scripts ./scripts

# Непривилегированный пользователь: uvicorn не нуждается в root (порт 8000 > 1024).
# USER здесь НЕ ставится: /data приходит бинд-маунтом, владельца которого задаёт
# хост, и выровнять его может только root. Это делает entrypoint — и тут же
# сбрасывает привилегии через setpriv, поэтому приложение всё равно работает
# под app. Оператор может пропустить этот шаг, задав user: в compose.
COPY backend/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN groupadd --gid "${APP_GID}" app && \
    useradd --uid "${APP_UID}" --gid "${APP_GID}" --home-dir /app --shell /usr/sbin/nologin app && \
    mkdir -p /data && \
    chown -R app:app /app /data && \
    chmod +x /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

EXPOSE 8000

# 0.0.0.0 нужен для межконтейнерной связи (frontend -> backend:8000); сам
# контейнер наружу не публикуется (см. docker-compose.yml без ports у backend).
CMD ["sh", "-c", "uvicorn app.main:app --host ${UVICORN_HOST:-0.0.0.0} --port 8000 --workers ${UVICORN_WORKERS:-1}"]

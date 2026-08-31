FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY backend/requirements.txt ./backend/requirements.txt
COPY doc-parser ./doc-parser
RUN pip install --no-cache-dir -e ./doc-parser && \
    pip install --no-cache-dir -r backend/requirements.txt

COPY backend/app ./app
COPY backend/prompts ./prompts

EXPOSE 8000

# 0.0.0.0 нужен для межконтейнерной связи (frontend -> backend:8000); сам
# контейнер наружу не публикуется (см. docker-compose.yml без ports у backend).
CMD ["sh", "-c", "uvicorn app.main:app --host ${UVICORN_HOST:-0.0.0.0} --port 8000"]

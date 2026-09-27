#!/usr/bin/env bash
# Остановить локальный стек AI Knowledge, поднятый scripts/start-all.sh
# (macOS/Linux). Аналог scripts/stop-all.ps1 для Windows.
#
# Останавливает только то, что запустил start-all.sh: процессы по PID-файлам,
# Docker-контейнер Qdrant и кластер PostgreSQL, запущенный через pg_ctl.
# Переиспользованные сервисы (brew services / systemd PostgreSQL, Ollama.app)
# не трогаются — в отличие от stop-all.ps1, который гасит всё по портам.
# Backend и frontend, потерявшие PID-файл, дополнительно ищутся по портам
# 18000/16300 — но только если командная строка совпадает с процессом проекта.
#
# Использование: ./scripts/stop-all.sh

set -u

# shellcheck source=scripts/stack-common.sh
source "$(dirname "${BASH_SOURCE[0]}")/stack-common.sh"

echo "=== Остановка по PID-файлам ==="
okf_stop_pidfile next next/dist/bin/next
okf_stop_pidfile backend app.main:app
okf_stop_pidfile ollama ollama
okf_stop_pidfile qdrant qdrant

if [ -f "$OKF_RUN_DIR/qdrant.container" ]; then
    rm -f "$OKF_RUN_DIR/qdrant.container"
    if docker rm -f "$OKF_QDRANT_CONTAINER" >/dev/null 2>&1; then
        echo "  stopped qdrant (docker $OKF_QDRANT_CONTAINER)"
    fi
fi

if [ -f "$OKF_RUN_DIR/postgres.datadir" ]; then
    data="$(head -n 1 "$OKF_RUN_DIR/postgres.datadir")"
    rm -f "$OKF_RUN_DIR/postgres.datadir"
    major="$(cat "$data/PG_VERSION" 2>/dev/null)"
    # -m fast: откат активных транзакций и чистый shutdown, без SIGKILL.
    if pg_ctl="$(okf_pg_tool pg_ctl "$major")" && [ -f "$data/postmaster.pid" ] &&
        "$pg_ctl" -D "$data" -m fast -w stop >/dev/null 2>&1; then
        echo "  stopped postgres ($data)"
    fi
fi

echo "=== Остановка по портам (backend/frontend без PID-файла) ==="
if command -v lsof >/dev/null 2>&1; then
    for spec in "$OKF_BACKEND_PORT:app.main:app" "$OKF_FRONTEND_PORT:next"; do
        port="${spec%%:*}"
        pattern="${spec#*:}"
        for pid in $(lsof -nP -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null); do
            okf_pid_matches "$pid" "$pattern" || continue
            okf_kill_group "$pid"
            echo "  stopped port $port (PID $pid)"
        done
    done
else
    echo "  lsof не найден — проверка портов пропущена."
fi

echo "=== Готово ==="

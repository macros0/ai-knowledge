# shellcheck shell=bash
# Общая часть scripts/start-all.sh и scripts/stop-all.sh (macOS/Linux).
# Подключается через `source`, сама не запускается. Совместима с bash 3.2
# (системный bash macOS): без ассоциативных массивов, mapfile и ${var,,}.

OKF_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# PID-файлы, маркеры и логи. Не $TMPDIR: macOS удаляет из него файлы, которые
# давно не открывались, и stop-all.sh потерял бы PID долгоживущего стека.
OKF_RUN_DIR="${OKF_RUN_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/okf-knowledge}"
# Данные сервисов, которые скрипт запускает сам (хранилище Qdrant).
OKF_DATA_HOME="${OKF_DATA_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/okf-knowledge}"

# Те же host-порты, что у start-all.ps1: на них смотрят фолбэки
# frontend/next.config.js, frontend/src/lib/backendFetch.js и SSO redirect URI.
OKF_BACKEND_PORT=18000
OKF_FRONTEND_PORT=16300

# Версия бинаря/образа должна читать storage-формат (±1 минор). Та же версия,
# что в docker-compose.yml и start-qdrant.ps1.
OKF_QDRANT_VERSION=1.19.0
OKF_QDRANT_CONTAINER=okf-qdrant-dev

# Значение настройки так, как его видит backend (pydantic-settings):
# переменная окружения важнее корневого .env, дальше — дефолт config.py.
okf_setting() {
    local key="$1" default="$2" value=""
    value="${!key-}"
    if [ -z "$value" ] && [ -f "$OKF_ROOT/.env" ]; then
        value="$(sed -n "s/^[[:space:]]*$key[[:space:]]*=[[:space:]]*//p" "$OKF_ROOT/.env" | tail -n 1 | tr -d '\r')"
        case "$value" in
            \"*\") value="${value#\"}"; value="${value%\"}" ;;
            \'*\') value="${value#\'}"; value="${value%\'}" ;;
            *) value="$(printf '%s' "$value" | sed 's/[[:space:]]#.*$//; s/[[:space:]]*$//')" ;;
        esac
    fi
    printf '%s\n' "${value:-$default}"
}

# Хост и порт из URL вида scheme://[user:pass@]host[:port][/path].
okf_url_authority() {
    local rest="${1#*://}"
    rest="${rest%%/*}"
    printf '%s\n' "${rest##*@}"
}

okf_url_host() {
    local authority
    authority="$(okf_url_authority "$1")"
    printf '%s\n' "${authority%%:*}"
}

okf_url_port() {
    local authority
    authority="$(okf_url_authority "$1")"
    case "$authority" in
        *:*) printf '%s\n' "${authority##*:}" ;;
        *) printf '%s\n' "$2" ;;
    esac
}

okf_is_loopback() {
    case "$1" in
        127.0.0.1|localhost|::1) return 0 ;;
        *) return 1 ;;
    esac
}

# Слушает ли кто-нибудь порт на loopback (без lsof: он есть не везде).
okf_port_busy() {
    (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
}

# Кто слушает порт — для понятной ошибки. Пусто, если lsof недоступен.
okf_port_owner() {
    command -v lsof >/dev/null 2>&1 || return 0
    local pid
    for pid in $(lsof -nP -t -iTCP:"$1" -sTCP:LISTEN 2>/dev/null); do
        printf 'PID %s: %s\n' "$pid" "$(ps -p "$pid" -o command= 2>/dev/null | cut -c1-120)"
    done
}

okf_pid_matches() {
    ps -p "$1" -o command= 2>/dev/null | grep -q -- "$2"
}

# Процессы стартуют лидерами своих групп (set -m в start-all.sh), поэтому
# группа гасится целиком — вместе с дочерними процессами next dev и uvicorn.
okf_kill_group() {
    local pid="$1" i
    kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
    for i in $(seq 1 50); do
        kill -0 -- "-$pid" 2>/dev/null || kill -0 "$pid" 2>/dev/null || return 0
        sleep 0.2
    done
    kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null
    return 0
}

# Остановить свой процесс по PID-файлу. PATTERN защищает от чужого процесса,
# получившего тот же PID после перезагрузки.
okf_stop_pidfile() {
    local name="$1" pattern="$2" pidfile="$OKF_RUN_DIR/$1.pid" pid
    [ -f "$pidfile" ] || return 1
    pid="$(head -n 1 "$pidfile")"
    rm -f "$pidfile"
    case "$pid" in ''|*[!0-9]*) return 1 ;; esac
    okf_pid_matches "$pid" "$pattern" || return 1
    okf_kill_group "$pid"
    echo "  stopped $name (PID $pid)"
}

# PostgreSQL-утилита: keg-only формулы Homebrew и каталоги Debian/Ubuntu, затем PATH.
# Второй аргумент — мажорная версия кластера: pg_ctl другой версии его не запустит.
okf_pg_tool() {
    local tool="$1" major="${2-}" prefix="" dir v
    if command -v brew >/dev/null 2>&1; then
        prefix="$(brew --prefix)"
    fi
    for v in ${major:-17 16 15 14}; do
        for dir in "$prefix/opt/postgresql@$v/bin" "/usr/lib/postgresql/$v/bin"; do
            if [ -x "$dir/$tool" ]; then
                printf '%s\n' "$dir/$tool"
                return 0
            fi
        done
    done
    command -v "$tool"
}

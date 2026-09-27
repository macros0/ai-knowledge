#!/usr/bin/env bash
# Поднять весь локальный стек AI Knowledge на macOS/Linux одной командой:
# Qdrant, Ollama, PostgreSQL, backend (:18000), frontend (:16300).
# Аналог scripts/start-all.ps1 для Windows.
#
# Адреса Qdrant, Ollama и PostgreSQL берутся оттуда же, откуда их читает
# backend: переменные окружения → корневой .env → дефолты config.py, поэтому
# скрипт и backend не могут разойтись в портах. Каждый шаг ждёт свой
# health-эндпоинт, а не спит вслепую.
#
# Идемпотентен: здоровый сервис переиспользуется (brew/systemd PostgreSQL,
# Ollama.app), свой устаревший процесс по PID-файлу перезапускается, чужой
# процесс на нужном порту не убивается — шаг падает с понятной ошибкой.
#
# Настройки окружения (все необязательны):
#   QDRANT_BIN   путь к бинарю qdrant (иначе PATH, затем Docker-контейнер)
#   OKF_PG_DATA  каталог данных PostgreSQL, если сервер не запущен
#   OKF_PYTHON   python backend (по умолчанию backend/.venv/bin/python)
#   OKF_RUN_DIR / OKF_DATA_HOME  PID-файлы и логи / данные Qdrant
#
# Использование: ./scripts/start-all.sh   (остановка: ./scripts/stop-all.sh)

set -u
# Каждый фоновый процесс становится лидером своей группы: Ctrl-C в терминале
# его не задевает, а stop-all.sh гасит группу целиком.
set -m

# shellcheck source=scripts/stack-common.sh
source "$(dirname "${BASH_SOURCE[0]}")/stack-common.sh"

RESULTS=()
LAUNCHED_PID=""

record() {
    RESULTS+=("$1|$2")
}

healthy() {
    curl -fsSL --noproxy 127.0.0.1,localhost -o /dev/null --max-time 4 "$1" 2>/dev/null
}

launch() {
    local name="$1" log="$2" workdir="$3"
    shift 3
    (cd "$workdir" && exec nohup "$@" >"$OKF_RUN_DIR/$log-out.log" 2>"$OKF_RUN_DIR/$log-err.log" </dev/null) &
    LAUNCHED_PID=$!
    echo "$LAUNCHED_PID" >"$OKF_RUN_DIR/$name.pid"
}

show_log_tail() {
    local file="$OKF_RUN_DIR/$1-err.log"
    [ -s "$file" ] || file="$OKF_RUN_DIR/$1-out.log"
    [ -s "$file" ] || return 0
    echo "  Последние строки $file:"
    tail -n 15 "$file" | sed 's/^/    /'
}

# wait_health NAME URL LOG [PID] — ждать 200 от URL; быстрый выход, если
# запущенный процесс уже умер.
wait_health() {
    local name="$1" url="$2" log="$3" pid="${4-}" i
    for i in $(seq 1 40); do
        sleep 1
        if healthy "$url"; then
            echo "  [OK] $name  ->  $url"
            return 0
        fi
        if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
            echo "  [FAIL] $name: процесс завершился при старте"
            show_log_tail "$log"
            return 1
        fi
    done
    echo "  [FAIL] $name не ответил за 40 c: $url"
    show_log_tail "$log"
    return 1
}

# Освободить порт от собственного устаревшего процесса; чужой — не трогать.
claim_port() {
    local name="$1" pattern="$2" port="$3" i
    okf_stop_pidfile "$name" "$pattern"
    for i in $(seq 1 25); do
        okf_port_busy "$port" || return 0
        sleep 0.2
    done
    echo "  [FAIL] Порт $port занят другим процессом, который не отвечает как $name:"
    okf_port_owner "$port" | sed 's/^/    /'
    return 1
}

# run_service DISPLAY NAME PATTERN PORT URL LOG WORKDIR CMD...
run_service() {
    local display="$1" name="$2" pattern="$3" port="$4" url="$5" log="$6" workdir="$7"
    shift 7
    if healthy "$url"; then
        echo "  [OK] $display уже работает; существующий процесс переиспользован."
        return 0
    fi
    claim_port "$name" "$pattern" "$port" || return 1
    launch "$name" "$log" "$workdir" "$@"
    wait_health "$display" "$url" "$log" "$LAUNCHED_PID"
}

start_qdrant() {
    local url host port grpc_port health bin
    url="$(okf_setting QDRANT_URL http://127.0.0.1:6333)"
    host="$(okf_url_host "$url")"
    port="$(okf_url_port "$url" 6333)"
    if ! okf_is_loopback "$host"; then
        health="${url%/}/collections"
        if healthy "$health"; then
            echo "  [OK] Внешний Qdrant отвечает: $health"
            return 0
        fi
        echo "  [FAIL] Внешний Qdrant ($url) не отвечает; скрипт запускает только локальный."
        return 1
    fi
    health="http://127.0.0.1:$port/collections"
    if healthy "$health"; then
        echo "  [OK] Qdrant уже работает; существующий процесс переиспользован."
        return 0
    fi
    grpc_port="$(okf_setting QDRANT_GRPC_PORT $((port + 1)))"
    mkdir -p "$OKF_DATA_HOME/qdrant/storage"

    bin="${QDRANT_BIN:-}"
    [ -n "$bin" ] || bin="$(command -v qdrant 2>/dev/null || true)"
    [ -n "$bin" ] || { [ -x "$OKF_DATA_HOME/qdrant/qdrant" ] && bin="$OKF_DATA_HOME/qdrant/qdrant"; }

    if [ -n "$bin" ]; then
        [ -x "$bin" ] || { echo "  [FAIL] QDRANT_BIN не исполняемый: $bin"; return 1; }
        claim_port qdrant qdrant "$port" || return 1
        echo "  Запуск Qdrant: $bin (данные: $OKF_DATA_HOME/qdrant/storage)"
        # QDRANT__SERVICE__HOST: только loopback — без него Qdrant слушает 0.0.0.0
        # без авторизации (SECURITY.md, как в start-qdrant.ps1).
        launch qdrant qdrant "$OKF_DATA_HOME/qdrant" env \
            QDRANT__SERVICE__HOST=127.0.0.1 \
            QDRANT__SERVICE__HTTP_PORT="$port" \
            QDRANT__SERVICE__GRPC_PORT="$grpc_port" \
            "$bin"
        wait_health Qdrant "$health" qdrant "$LAUNCHED_PID"
        return
    fi

    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
        docker rm -f "$OKF_QDRANT_CONTAINER" >/dev/null 2>&1
        if okf_port_busy "$port"; then
            echo "  [FAIL] Порт $port занят другим процессом, который не отвечает как Qdrant:"
            okf_port_owner "$port" | sed 's/^/    /'
            return 1
        fi
        echo "  Бинарь qdrant не найден — запуск Docker-образа qdrant/qdrant:v$OKF_QDRANT_VERSION"
        # Публикация только на 127.0.0.1: тот же профиль изоляции, что у бинаря.
        if ! docker run -d --name "$OKF_QDRANT_CONTAINER" \
            -p "127.0.0.1:$port:6333" -p "127.0.0.1:$grpc_port:6334" \
            -v "$OKF_DATA_HOME/qdrant/storage:/qdrant/storage" \
            "qdrant/qdrant:v$OKF_QDRANT_VERSION" >/dev/null; then
            echo "  [FAIL] docker run не удался"
            return 1
        fi
        touch "$OKF_RUN_DIR/qdrant.container"
        wait_health Qdrant "$health" qdrant || {
            echo "  Логи контейнера: docker logs $OKF_QDRANT_CONTAINER"
            return 1
        }
        return 0
    fi

    echo "  [FAIL] Qdrant не найден: нет бинаря (QDRANT_BIN или qdrant в PATH) и не запущен Docker."
    echo "         Бинарь v$OKF_QDRANT_VERSION: https://github.com/qdrant/qdrant/releases/tag/v$OKF_QDRANT_VERSION"
    echo "         (macOS: *-apple-darwin, после распаковки: xattr -d com.apple.quarantine qdrant)"
    return 1
}

# Порт Ollama, если он нужен: эмбеддинги или LLM через ollama/* на loopback.
ollama_base() {
    local provider model base
    provider="$(okf_setting EMBEDDING_PROVIDER http)"
    model="$(okf_setting EMBEDDING_MODEL ollama/bge-m3)"
    base="$(okf_setting EMBEDDING_API_BASE http://127.0.0.1:11434)"
    case "$model" in
        ollama/*)
            if [ "$provider" != fake ] && okf_is_loopback "$(okf_url_host "$base")"; then
                printf '%s\n' "$base"
                return 0
            fi
            ;;
    esac
    model="$(okf_setting LLM_MODEL ollama/qwen2.5:14b)"
    base="$(okf_setting LLM_BASE_URL http://127.0.0.1:11434)"
    case "$model" in
        ollama/*) okf_is_loopback "$(okf_url_host "$base")" && printf '%s\n' "$base" && return 0 ;;
    esac
    return 1
}

# Предупредить о модели ollama/*, которую backend будет звать на локальном Ollama,
# но которая не загружена (ошибка иначе всплывёт только при первой загрузке документа).
warn_missing_ollama_model() {
    local tags="$1" key="$2" model="$3" base="$4"
    case "$model" in ollama/*) model="${model#ollama/}" ;; *) return 0 ;; esac
    okf_is_loopback "$(okf_url_host "$base")" || return 0
    if ! printf '%s' "$tags" | grep -q "\"name\":\"$model"; then
        echo "  WARNING: модель $model не загружена в Ollama ($key). Выполните: ollama pull $model"
    fi
}

warn_missing_ollama_models() {
    local tags="$1"
    if [ "$(okf_setting EMBEDDING_PROVIDER http)" != fake ]; then
        warn_missing_ollama_model "$tags" EMBEDDING_MODEL \
            "$(okf_setting EMBEDDING_MODEL ollama/bge-m3)" \
            "$(okf_setting EMBEDDING_API_BASE http://127.0.0.1:11434)"
    fi
    warn_missing_ollama_model "$tags" LLM_MODEL \
        "$(okf_setting LLM_MODEL ollama/qwen2.5:14b)" \
        "$(okf_setting LLM_BASE_URL http://127.0.0.1:11434)"
}

start_ollama() {
    local base port url bin
    if ! base="$(ollama_base)"; then
        echo "  Пропуск: эмбеддинги и LLM не используют локальный Ollama (.env)."
        return 2
    fi
    port="$(okf_url_port "$base" 11434)"
    url="http://127.0.0.1:$port/api/tags"
    bin="$(command -v ollama 2>/dev/null || true)"
    [ -n "$bin" ] || { [ -x /Applications/Ollama.app/Contents/Resources/ollama ] && bin=/Applications/Ollama.app/Contents/Resources/ollama; }
    if [ -z "$bin" ] && ! healthy "$url"; then
        echo "  [FAIL] Ollama не найден. Установите: https://ollama.com/download"
        return 1
    fi
    run_service Ollama ollama ollama "$port" "$url" ollama "$OKF_RUN_DIR" \
        env OLLAMA_HOST="127.0.0.1:$port" "$bin" serve || return 1
    warn_missing_ollama_models "$(curl -fsS --noproxy 127.0.0.1 --max-time 4 "$url" 2>/dev/null)"
}

# Каталог данных PostgreSQL для pg_ctl: OKF_PG_DATA, иначе кластеры Homebrew.
pg_data_dir() {
    local prefix dir
    if [ -n "${OKF_PG_DATA:-}" ]; then
        printf '%s\n' "$OKF_PG_DATA"
        return 0
    fi
    command -v brew >/dev/null 2>&1 || return 1
    prefix="$(brew --prefix)"
    for dir in "$prefix/var/postgresql@17" "$prefix/var/postgresql@16" "$prefix/var/postgresql@15" \
        "$prefix/var/postgresql@14" "$prefix/var/postgres"; do
        if [ -f "$dir/PG_VERSION" ]; then
            printf '%s\n' "$dir"
            return 0
        fi
    done
    return 1
}

# Создать БД приложения из DATABASE_URL, если её ещё нет (как start-postgres.ps1).
ensure_database() {
    local url="$1" rest authority hostport path db query creds user password psql exists
    psql="$(okf_pg_tool psql)" || { echo "  WARNING: psql не найден — проверка/создание БД пропущены."; return 0; }
    rest="${url#*://}"
    authority="${rest%%/*}"
    path=""
    [ "$rest" = "$authority" ] || path="${rest#*/}"
    db="${path%%\?*}"
    query=""
    case "$path" in *\?*) query="?${path#*\?}" ;; esac
    [ -n "$db" ] || { echo "  WARNING: в DATABASE_URL нет имени БД — создание пропущено."; return 0; }
    hostport="${authority##*@}"
    user=""
    password=""
    case "$authority" in
        *@*)
            creds="${authority%@*}"
            user="${creds%%:*}"
            case "$creds" in *:*) password="${creds#*:}" ;; esac
            ;;
    esac
    # Пароль уходит через PGPASSWORD, а не в URI: командную строку видит ps.
    # В URL пароль percent-encoded — декодируем (литеральные \ сохраняем).
    password="${password//\\/\\\\}"
    password="$(printf '%b' "${password//%/\\x}")"
    local uri="postgresql://${user:+$user@}$hostport/postgres$query"
    exists="$(printf '%s\n' "SELECT 1 FROM pg_database WHERE datname = :'db';" |
        PGPASSWORD="$password" "$psql" -X -q -tA -v ON_ERROR_STOP=1 -v db="$db" "$uri" 2>&1)"
    if [ "$exists" = 1 ]; then
        echo "  БД $db уже существует."
        return 0
    fi
    if [ -n "$exists" ]; then
        echo "  WARNING: не удалось подключиться как ${user:-<текущий пользователь>}: $exists"
        echo "           Проверьте пользователя и пароль в DATABASE_URL (.env)."
        return 0
    fi
    if printf '%s\n' 'CREATE DATABASE :"db";' |
        PGPASSWORD="$password" "$psql" -X -q -v ON_ERROR_STOP=1 -v db="$db" "$uri" >/dev/null; then
        echo "  БД $db создана."
    else
        echo "  WARNING: не удалось создать БД $db."
    fi
}

start_postgres() {
    local url host port pg_isready data major pg_ctl pg_locale i
    url="$(okf_setting DATABASE_URL "")"
    [ -n "$url" ] || url="$(okf_setting DATABASE_URL_DEV "")"
    case "$url" in
        ""|sqlite*)
            echo "  Пропуск: DATABASE_URL не задан — backend работает на SQLite."
            return 2
            ;;
    esac
    host="$(okf_url_host "$url")"
    port="$(okf_url_port "$url" 5432)"
    if ! pg_isready="$(okf_pg_tool pg_isready)"; then
        echo "  [FAIL] pg_isready не найден. macOS: brew install postgresql@17; Linux: пакет postgresql-client."
        return 1
    fi
    if "$pg_isready" -h "$host" -p "$port" -q 2>/dev/null; then
        echo "  [OK] PostgreSQL уже готов на $host:$port; существующий сервер переиспользован."
        ensure_database "$url"
        return 0
    fi
    if ! okf_is_loopback "$host"; then
        echo "  [FAIL] Внешний PostgreSQL $host:$port не отвечает; скрипт запускает только локальный."
        return 1
    fi
    if ! data="$(pg_data_dir)"; then
        echo "  [FAIL] PostgreSQL на $host:$port не запущен, каталог данных не найден."
        echo "         macOS: brew services start postgresql@17 (или OKF_PG_DATA=<каталог> для pg_ctl)"
        echo "         Linux: sudo systemctl start postgresql"
        return 1
    fi
    major="$(cat "$data/PG_VERSION" 2>/dev/null)"
    if ! pg_ctl="$(okf_pg_tool pg_ctl "$major")"; then
        echo "  [FAIL] pg_ctl для PostgreSQL $major не найден."
        return 1
    fi
    # macOS: без LC_ALL postmaster может упасть с «postmaster became
    # multithreaded during startup» (shell без LANG, запуск из IDE). Службы
    # Homebrew задают ту же переменную в своём plist.
    pg_locale="${LC_ALL:-}"
    if [ -z "$pg_locale" ] && [ "$(uname -s)" = Darwin ]; then
        pg_locale=en_US.UTF-8
    fi
    echo "  Запуск PostgreSQL $major: $pg_ctl -D $data"
    if ! env ${pg_locale:+LC_ALL="$pg_locale"} \
        "$pg_ctl" -D "$data" -l "$OKF_RUN_DIR/postgres.log" -o "-p $port" -w -t 30 start >/dev/null; then
        echo "  [FAIL] pg_ctl start не удался. Лог: $OKF_RUN_DIR/postgres.log"
        return 1
    fi
    # stop-all.sh останавливает только тот кластер, который запустил этот скрипт.
    printf '%s\n' "$data" >"$OKF_RUN_DIR/postgres.datadir"
    for i in $(seq 1 30); do
        if "$pg_isready" -h "$host" -p "$port" -q 2>/dev/null; then
            echo "  [OK] PostgreSQL  ->  pg_isready $host:$port"
            ensure_database "$url"
            return 0
        fi
        sleep 1
    done
    echo "  [FAIL] PostgreSQL не ответил за 30 c. Лог: $OKF_RUN_DIR/postgres.log"
    return 1
}

start_backend() {
    local python="${OKF_PYTHON:-$OKF_ROOT/backend/.venv/bin/python}"
    if [ ! -x "$python" ]; then
        echo "  [FAIL] Не найден $python. Создайте окружение (Python 3.12+):"
        echo "         python3 -m venv backend/.venv"
        echo "         backend/.venv/bin/pip install -e ./doc-parser -r backend/requirements.txt"
        return 1
    fi
    run_service Backend backend app.main:app "$OKF_BACKEND_PORT" \
        "http://127.0.0.1:$OKF_BACKEND_PORT/health" python "$OKF_ROOT/backend" \
        "$python" -m uvicorn app.main:app --host 127.0.0.1 --port "$OKF_BACKEND_PORT"
}

start_frontend() {
    if ! command -v node >/dev/null 2>&1; then
        echo "  [FAIL] node не найден в PATH (нужен Node.js 20+)."
        return 1
    fi
    if [ ! -f "$OKF_ROOT/frontend/node_modules/next/dist/bin/next" ]; then
        echo "  [FAIL] Не установлены зависимости фронтенда: (cd frontend && npm ci)"
        return 1
    fi
    run_service Frontend next next/dist/bin/next "$OKF_FRONTEND_PORT" \
        "http://127.0.0.1:$OKF_FRONTEND_PORT" node "$OKF_ROOT/frontend" \
        node node_modules/next/dist/bin/next dev -p "$OKF_FRONTEND_PORT"
}

step() {
    local display="$1" fn="$2" status
    echo ""
    echo "=== $display ==="
    "$fn"
    status=$?
    case "$status" in
        0) record "$display" ready ;;
        2) record "$display" skipped ;;
        *) record "$display" FAIL ;;
    esac
}

mkdir -p "$OKF_RUN_DIR" "$OKF_DATA_HOME"
if [ ! -f "$OKF_ROOT/.env" ]; then
    echo "WARNING: корневой .env не найден — backend и этот скрипт берут дефолты config.py"
    echo "         (Qdrant :6333, SQLite, Ollama :11434). Шаблон: cp .env.example .env"
fi
if ! command -v curl >/dev/null 2>&1; then
    echo "[FAIL] curl не найден — health-проверки невозможны."
    exit 1
fi

step Qdrant start_qdrant
step Ollama start_ollama
step PostgreSQL start_postgres
step Backend start_backend
step Frontend start_frontend

echo ""
echo "=== Итог ==="
all_ok=1
for entry in "${RESULTS[@]}"; do
    printf '  %-11s %s\n' "${entry%%|*}" "${entry#*|}"
    [ "${entry#*|}" = FAIL ] && all_ok=0
done

echo ""
if [ "$all_ok" = 1 ]; then
    echo "Стек готов. UI: http://localhost:$OKF_FRONTEND_PORT"
    echo "Логи: $OKF_RUN_DIR. Остановить: ./scripts/stop-all.sh"
    exit 0
fi
echo "WARNING: часть сервисов не поднялась. Логи: $OKF_RUN_DIR"
exit 1

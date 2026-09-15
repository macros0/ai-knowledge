#!/usr/bin/env bash
set -euo pipefail

usage() { echo "usage: $0 --env-file FILE --backup-dir DIR --target-project NAME --target-data-dir DIR [--dry-run] [--replace-existing --confirm-replace-existing]" >&2; exit 2; }
env_file= backup_dir= target_project= target_data= dry_run=0 replace=0 confirm=0
while (($#)); do
  case "$1" in
    --env-file) env_file=${2:-}; shift 2 ;;
    --backup-dir) backup_dir=${2:-}; shift 2 ;;
    --target-project) target_project=${2:-}; shift 2 ;;
    --target-data-dir) target_data=${2:-}; shift 2 ;;
    --dry-run) dry_run=1; shift ;;
    --replace-existing) replace=1; shift ;;
    --confirm-replace-existing) confirm=1; shift ;;
    *) usage ;;
  esac
done
[[ -n $env_file && -n $backup_dir && -n $target_project && -n $target_data && -f $env_file ]] || usage
[[ $target_project =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ ]] || { echo 'unsafe target project name' >&2; exit 1; }
dotenv_value() { awk -F= -v key="$1" '$0 !~ /^[[:space:]]*#/ && $1 == key {sub("^[^=]*=", ""); print; exit}' "$env_file"; }
[[ $(dotenv_value STORAGE_MODE) == bundled ]] || { echo 'restore requires STORAGE_MODE=bundled' >&2; exit 1; }
if ((replace && !confirm)); then echo 'replace mode requires --confirm-replace-existing' >&2; exit 1; fi
if ((!replace)) && [[ -e $target_data ]]; then echo "target data directory already exists: $target_data" >&2; exit 1; fi
export OKF_RUNTIME_ENV_FILE="$env_file"
compose() { OKF_DATA_DIR="$target_data" docker compose --project-name "$target_project" --env-file "$env_file" "$@"; }
fallback_lock=
cleanup() { [[ -z $fallback_lock ]] || rmdir "$fallback_lock" 2>/dev/null || true; }
trap cleanup EXIT

if ((dry_run)); then
  compose up -d --no-deps postgres qdrant
  compose exec -T postgres pg_restore --clean --if-exists -U okf -d okf_knowledge
  compose run --rm --no-deps -v "$backup_dir:/backup:ro" backend python scripts/restore_qdrant_snapshot.py --snapshot-file /backup/qdrant/SNAPSHOT
  echo 'dry-run: default restore never removes Docker volumes'
  exit 0
fi
[[ -f "$backup_dir/manifest.json" && -f "$backup_dir/SHA256SUMS" ]] || { echo 'backup manifest or checksums missing' >&2; exit 1; }
(cd "$backup_dir" && sha256sum --check SHA256SUMS)
lock_root=$(cd "$(dirname "$backup_dir")" && pwd)
if command -v flock >/dev/null 2>&1; then
  exec 9>"$lock_root/.backup.lock"
  flock -n 9 || { echo 'another backup, restore, or upgrade is active' >&2; exit 1; }
else
  fallback_lock="$lock_root/.backup.lock.d"
  mkdir "$fallback_lock" 2>/dev/null || {
    echo 'another backup, restore, or upgrade is active' >&2
    exit 1
  }
fi
if ((replace)); then
  active=$(compose ps -aq)
  [[ -z $active ]] || { echo 'replace requires every target-project container to be stopped and removed' >&2; exit 1; }
  resolved_target=$(cd "$(dirname "$target_data")" && pwd)/$(basename "$target_data")
  [[ $resolved_target != / && $resolved_target != "$HOME" ]] || { echo 'unsafe target data directory' >&2; exit 1; }
  for volume in "${target_project}_postgres_data" "${target_project}_qdrant_data"; do
    docker volume inspect "$volume" >/dev/null 2>&1 || continue
    echo "removing confirmed target volume: $volume" >&2
    docker volume rm "$volume"
  done
  [[ ! -e $target_data ]] || rm -rf -- "$target_data"
fi
for volume in "${target_project}_postgres_data" "${target_project}_qdrant_data"; do
  if docker volume inspect "$volume" >/dev/null 2>&1; then
    echo "target volume already exists: $volume" >&2
    exit 1
  fi
done
mkdir "$target_data"
tar --extract --file "$backup_dir/data.tar" --directory "$target_data"
compose up -d --no-deps postgres qdrant
postgres_ready=0
for _ in $(seq 1 60); do
  if compose exec -T postgres pg_isready -U okf -d okf_knowledge >/dev/null 2>&1; then
    postgres_ready=1
    break
  fi
  sleep 1
done
((postgres_ready)) || { echo 'PostgreSQL did not become ready before restore' >&2; exit 1; }
compose cp "$backup_dir/postgres.dump" postgres:/tmp/okf-restore.dump
compose exec -T postgres pg_restore --clean --if-exists -U okf -d okf_knowledge /tmp/okf-restore.dump
snapshot=$(find "$backup_dir/qdrant" -maxdepth 1 -type f ! -name '*.sha256' -print -quit)
[[ -n $snapshot ]] || { echo 'Qdrant snapshot is missing' >&2; exit 1; }
compose run --rm --no-deps -v "$(cd "$backup_dir" && pwd):/backup:ro" backend python scripts/restore_qdrant_snapshot.py --snapshot-file "/backup/qdrant/$(basename "$snapshot")"
compose run --rm --no-deps migrate alembic upgrade head
compose run --rm --no-deps -v "$(cd "$backup_dir" && pwd):/backup:ro" backend python scripts/check_integrity.py --strict --expected-manifest /backup/manifest.json
compose up -d backend
compose up -d frontend
echo "restored into Compose project $target_project"

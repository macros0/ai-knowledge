#!/usr/bin/env bash
set -euo pipefail

usage() { echo "usage: $0 --env-file FILE --backup-dir DIR [--dry-run]" >&2; exit 2; }
env_file= backup_root= dry_run=0
while (($#)); do
  case "$1" in
    --env-file) env_file=${2:-}; shift 2 ;;
    --backup-dir) backup_root=${2:-}; shift 2 ;;
    --dry-run) dry_run=1; shift ;;
    *) usage ;;
  esac
done
[[ -n $env_file && -n $backup_root && -f $env_file ]] || usage
env_file=$(cd "$(dirname "$env_file")" && pwd)/$(basename "$env_file")

dotenv_value() {
  awk -F= -v key="$1" '$0 !~ /^[[:space:]]*#/ && $1 == key {sub("^[^=]*=", ""); print; exit}' "$env_file"
}
[[ $(dotenv_value STORAGE_MODE) == bundled ]] || { echo 'backup requires STORAGE_MODE=bundled' >&2; exit 1; }
data_dir=${OKF_DATA_DIR:-}
if [[ -z $data_dir ]]; then
  data_dir=$(dotenv_value OKF_DATA_DIR)
fi
if [[ -z $data_dir ]]; then
  data_dir=$(dotenv_value DATA_DIR)
fi
data_dir=${data_dir:-./data}
[[ -d $data_dir ]] || { echo "data directory does not exist: $data_dir" >&2; exit 1; }
data_dir=$(cd "$data_dir" && pwd)
export OKF_RUNTIME_ENV_FILE="$env_file"
compose() { docker compose --env-file "$env_file" "$@"; }

archive="$backup_root/backup-$(date -u +%Y%m%dT%H%M%SZ)"
if ((dry_run)); then archive="$backup_root/backup-dry-run"; fi
restart=0
fallback_lock=
cleanup() {
  ((restart)) && compose start backend frontend || true
  [[ -z $fallback_lock ]] || rmdir "$fallback_lock" 2>/dev/null || true
}
trap cleanup EXIT

if ((dry_run)); then
  compose stop frontend backend
  compose exec -T postgres pg_dump -U okf -d okf_knowledge --format=custom
  compose run --rm --no-deps -v "$archive:/backup" backend python scripts/create_qdrant_snapshot.py --output-dir /backup/qdrant
  echo "dry-run: would archive data and write manifest to $archive"
  exit 0
fi

mkdir -p "$backup_root"
[[ ! -e $archive ]] || { echo "backup destination already exists: $archive" >&2; exit 1; }
umask 077
mkdir "$archive"
archive_path=$(cd "$archive" && pwd)
if command -v flock >/dev/null 2>&1; then
  exec 9>"$backup_root/.backup.lock"
  flock -n 9 || { echo 'another backup, restore, or upgrade is active' >&2; exit 1; }
else
  # Git Bash on Windows does not ship flock. mkdir is atomic on the supported
  # local filesystems and preserves the same fail-closed overlap protection.
  fallback_lock="$backup_root/.backup.lock.d"
  mkdir "$fallback_lock" 2>/dev/null || {
    echo 'another backup, restore, or upgrade is active' >&2
    exit 1
  }
fi

compose stop frontend backend
restart=1
compose exec -T postgres pg_dump -U okf -d okf_knowledge --format=custom > "$archive/postgres.dump"
compose exec -T postgres pg_restore --list < "$archive/postgres.dump" > "$archive/postgres.list"
compose run --rm --no-deps -v "$archive_path:/backup" backend python scripts/create_qdrant_snapshot.py --output-dir /backup/qdrant
compose run --rm --no-deps -v "$archive_path:/backup" backend python scripts/backup_totals.py --output /backup/totals.json
if [[ $archive_path == "$data_dir/"* ]]; then
  archive_relative=${archive_path#"$data_dir/"}
  (cd "$data_dir" && tar --create --exclude="./$archive_relative" --file "$archive_path/data.tar" .)
else
  (cd "$data_dir" && tar --create --file "$archive_path/data.tar" .)
fi
compose images --format json > "$archive/compose-images.json"
compose run --rm --no-deps -v "$archive_path:/backup" backend python scripts/write_backup_manifest.py --backup-dir /backup
(cd "$archive" && sha256sum postgres.dump postgres.list data.tar totals.json compose-images.json qdrant/* > SHA256SUMS)
echo "$archive"

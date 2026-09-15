#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
fixture_env="$tmp/bundled.env"
fixture_bin="$tmp/bin"
fake_docker_log="$tmp/docker.log"
mkdir -p "$fixture_bin"
printf '%s\n' \
  'STORAGE_MODE=bundled' \
  'POSTGRES_PASSWORD=test-password' \
  'DATABASE_URL=postgresql+psycopg://okf:test-password@postgres:5432/okf_knowledge' \
  'QDRANT_URL=http://qdrant:6333' > "$fixture_env"

cat > "$fixture_bin/docker" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
args=("$@")
if [[ "${args[0]:-}" == compose ]]; then
  args=("${args[@]:1}")
  if [[ "${args[0]:-}" == --env-file ]]; then args=("${args[@]:2}"); fi
  { printf 'compose'; printf ' %s' "${args[@]}"; printf '\n'; } >> "$FAKE_DOCKER_LOG"
fi
EOF
chmod +x "$fixture_bin/docker"

PATH="$fixture_bin:$PATH" FAKE_DOCKER_LOG="$fake_docker_log" \
  "$root/scripts/production/backup-bundled.sh" --env-file "$fixture_env" --backup-dir "$tmp/backup" --dry-run
grep -qx 'compose stop frontend backend' "$fake_docker_log"
grep -qx 'compose exec -T postgres pg_dump -U okf -d okf_knowledge --format=custom' "$fake_docker_log"
backup_user="$(id -u):$(id -g)"
grep -Fqx "compose run --rm --no-deps --user $backup_user -v $tmp/backup/backup-dry-run:/backup backend python scripts/create_qdrant_snapshot.py --output-dir /backup/qdrant" "$fake_docker_log"

PATH="$fixture_bin:$PATH" FAKE_DOCKER_LOG="$fake_docker_log" \
  "$root/scripts/production/restore-bundled.sh" --env-file "$fixture_env" --backup-dir "$tmp/backup" --target-project recovered --target-data-dir "$tmp/recovered" --dry-run
! grep -q 'volume rm' "$fake_docker_log"

mkdir "$tmp/existing"
if PATH="$fixture_bin:$PATH" FAKE_DOCKER_LOG="$fake_docker_log" \
  "$root/scripts/production/restore-bundled.sh" --env-file "$fixture_env" --backup-dir "$tmp/backup" --target-project recovered --target-data-dir "$tmp/existing" --dry-run; then
  echo 'restore accepted an existing target directory' >&2
  exit 1
fi
if PATH="$fixture_bin:$PATH" FAKE_DOCKER_LOG="$fake_docker_log" \
  "$root/scripts/production/restore-bundled.sh" --env-file "$fixture_env" --backup-dir "$tmp/backup" --target-project recovered --target-data-dir "$tmp/recovered2" --replace-existing --dry-run; then
  echo 'replace mode accepted without literal confirmation' >&2
  exit 1
fi
PATH="$fixture_bin:$PATH" FAKE_DOCKER_LOG="$fake_docker_log" \
  "$root/scripts/production/restore-bundled.sh" --env-file "$fixture_env" --backup-dir "$tmp/backup" --target-project recovered --target-data-dir "$tmp/recovered3" --replace-existing --confirm-replace-existing --dry-run
! grep -q 'volume rm' "$fake_docker_log"

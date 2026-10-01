#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 --env-file FILE --project NAME --diagnostics-dir DIR --since UTC --until UTC --output NEW_ZIP [--compose-override FILE] [--dry-run]" >&2
  exit 2
}

env_file= project= diagnostics_dir= since= until= output= compose_override= dry_run=0
while (($#)); do
  case "$1" in
    --env-file) env_file=${2:-}; shift 2 ;;
    --project) project=${2:-}; shift 2 ;;
    --diagnostics-dir) diagnostics_dir=${2:-}; shift 2 ;;
    --since) since=${2:-}; shift 2 ;;
    --until) until=${2:-}; shift 2 ;;
    --output) output=${2:-}; shift 2 ;;
    --compose-override) compose_override=${2:-}; shift 2 ;;
    --dry-run) dry_run=1; shift ;;
    *) usage ;;
  esac
done
[[ -n $env_file && -f $env_file && -n $project && -n $diagnostics_dir && -d $diagnostics_dir && -n $since && -n $until && -n $output ]] || usage
[[ $project =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}$ ]] || usage
[[ ! -e $output && ! -L $output && -d $(dirname "$output") ]] || usage

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd "$script_dir/../.." && pwd)
env_file=$(cd "$(dirname "$env_file")" && pwd)/$(basename "$env_file")
if [[ -n $compose_override ]]; then
  [[ -f $compose_override ]] || usage
  compose_override=$(cd "$(dirname "$compose_override")" && pwd)/$(basename "$compose_override")
fi
diagnostics_dir=$(cd "$diagnostics_dir" && pwd)
output_dir=$(cd "$(dirname "$output")" && pwd)
output_name=$(basename "$output")
[[ $output_name != . && $output_name != .. && $output_name == *.zip ]] || usage

mode=$(awk -F= '$0 !~ /^[[:space:]]*#/ && $1 == "STORAGE_MODE" {sub("^[^=]*=", ""); print; exit}' "$env_file")
app_uid=$(awk -F= '$0 !~ /^[[:space:]]*#/ && $1 == "APP_UID" {sub("^[^=]*=", ""); print; exit}' "$env_file")
app_gid=$(awk -F= '$0 !~ /^[[:space:]]*#/ && $1 == "APP_GID" {sub("^[^=]*=", ""); print; exit}' "$env_file")
app_uid=${app_uid:-1000}
app_gid=${app_gid:-1000}
[[ $app_uid =~ ^[1-9][0-9]*$ && $app_gid =~ ^[1-9][0-9]*$ ]] || usage
export OKF_RUNTIME_ENV_FILE="$env_file"
compose_files=(-f "$repo_dir/docker-compose.yml")
case "$mode" in
  bundled) compose_files=(--profile bundled "${compose_files[@]}") ;;
  external) compose_files+=(-f "$repo_dir/deploy/production/docker-compose.external.yml") ;;
  *) echo "STORAGE_MODE must be bundled or external" >&2; exit 2 ;;
esac
[[ -z $compose_override ]] || compose_files+=(-f "$compose_override")
compose() { docker compose --env-file "$env_file" -p "$project" "${compose_files[@]}" "$@"; }

if [[ -n $(compose ps --status running --services backend) ]]; then
  echo "Backend is running; use the administrator UI. This script does not stop the service." >&2
  exit 3
fi
if ((dry_run)); then
  echo "Offline export is available; the backend is stopped. No files were created."
  exit 0
fi

# Ask Docker only for six fixed state fields per container in this project.
# Parse and validate them before they are passed to the offline CLI; neither
# full inspect output nor Compose environment enters the archive.
container_state='{'
state_incomplete=0
for service in backend frontend postgres qdrant migrate; do
  container_id=$(compose ps -a -q "$service" 2>/dev/null) || {
    [[ $service == backend || $service == frontend ]] && state_incomplete=1
    continue
  }
  if [[ -z $container_id ]]; then
    [[ $service == backend || $service == frontend ]] && state_incomplete=1
    continue
  fi
  [[ $container_id =~ ^[a-f0-9]{12,64}$ ]] || { echo "Ambiguous container identity" >&2; exit 4; }
  line=$(docker inspect --format '{{.State.Status}}|{{.State.ExitCode}}|{{.State.OOMKilled}}|{{.State.StartedAt}}|{{.State.FinishedAt}}|{{.Image}}' "$container_id") || {
    state_incomplete=1; continue;
  }
  IFS='|' read -r state exit_code oom started finished image extra <<< "$line"
  [[ -z ${extra:-} && $state =~ ^(created|running|restarting|exited|paused|dead)$ && $exit_code =~ ^(0|[1-9][0-9]{0,2})$ ]] || exit 4
  ((exit_code <= 255)) || exit 4
  [[ $oom == true || $oom == false ]] || exit 4
  [[ $started =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+(Z|[+-][0-9]{2}:[0-9]{2})$ ]] || exit 4
  [[ $finished =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+(Z|[+-][0-9]{2}:[0-9]{2})$ ]] || exit 4
  [[ $image =~ ^sha256:[a-f0-9]{64}$ ]] || exit 4
  [[ $container_state == '{' ]] || container_state+=','
  printf -v record '"%s":{"status":"%s","exit_code":%s,"oom_killed":%s,"started_at":"%s","finished_at":"%s","image_id":"%s"}' \
    "$service" "$state" "$exit_code" "$oom" "$started" "$finished" "$image"
  container_state+="$record"
done
container_state+='}'
incomplete_arg=()
((state_incomplete)) && incomplete_arg=(--container-state-incomplete)

# The image entrypoint is replaced, so application startup and DB connections
# are never attempted. The Python CLI takes the nonblocking writer lock.
printf '%s' "$container_state" | compose run -T --rm --no-deps --user "$app_uid:$app_gid" --entrypoint python \
  -v "$diagnostics_dir:/diagnostics:rw" -v "$output_dir:/export:rw" \
  backend -m scripts.collect_diagnostics --root /diagnostics/backend \
  --since "$since" --until "$until" --output "/export/$output_name" --container-state-stdin "${incomplete_arg[@]}"
[[ -s $output ]] || { echo "No diagnostic archive was created" >&2; exit 4; }
echo "$output"

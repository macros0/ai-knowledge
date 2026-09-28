#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/diagnostics/backend" "$tmp/out"
printf 'STORAGE_MODE=bundled\n' > "$tmp/runtime.env"
cat > "$tmp/bin/docker" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$TRACE"
case " $* " in
  *' ps --status running --services backend '*) [[ ${BACKEND_RUNNING:-0} == 1 ]] && printf 'backend\n'; exit 0 ;;
  *' ps -a -q backend '*) printf 'abcdef123456\n'; exit 0 ;;
  *' ps -a -q frontend '*) printf 'fedcba654321\n'; exit 0 ;;
  *' ps -a -q migrate '*) printf '123456abcdef\n'; exit 0 ;;
  *' ps -a -q postgres '*|*' ps -a -q qdrant '*) exit 0 ;;
  *' inspect --format '* )
    [[ ${INSPECT_FAIL_FRONTEND:-0} == 1 && $* == *fedcba654321* ]] && exit 1
    if [[ $* == *123456abcdef* ]]; then
      printf 'exited|1|false|2026-09-27T00:00:00Z|2026-09-27T00:01:00Z|sha256:%064d\n' 0
      exit 0
    fi
    printf 'exited|137|true|2026-09-27T00:00:00Z|2026-09-27T00:01:00Z|sha256:%064d\n' 0
    exit 0 ;;
  *' run -T --rm --no-deps --user 1000:1000 --entrypoint python '*)
    cat > "$STATE_FIXTURE"
    [[ ${SIMULATE_ARCHIVE:-0} == 1 ]] && printf 'ZIP' > "$OUTPUT_FIXTURE"
    exit 0 ;;
esac
exit 1
SH
chmod +x "$tmp/bin/docker"
export PATH="$tmp/bin:$PATH" TRACE="$tmp/trace" OUTPUT_FIXTURE="$tmp/out/support.zip" STATE_FIXTURE="$tmp/state.json"
script="$root/scripts/production/collect-diagnostics.sh"
args=(--env-file "$tmp/runtime.env" --project acceptance --diagnostics-dir "$tmp/diagnostics" \
      --since 2026-09-27T00:00:00Z --until 2026-09-27T00:01:00Z --output "$OUTPUT_FIXTURE")

BACKEND_RUNNING=1 bash "$script" "${args[@]}" > "$tmp/stdout" 2> "$tmp/stderr" && exit 1 || [[ $? == 3 ]]
[[ ! -e $OUTPUT_FIXTURE ]]
BACKEND_RUNNING=0 bash "$script" "${args[@]}" --dry-run > "$tmp/stdout"
[[ ! -e $OUTPUT_FIXTURE ]]
printf 'services: {}\n' > "$tmp/compose-override.yml"
BACKEND_RUNNING=0 bash "$script" "${args[@]}" \
  --compose-override "$tmp/compose-override.yml" --dry-run > "$tmp/stdout"
grep -Fq -- "-f $tmp/compose-override.yml" "$TRACE"
SIMULATE_ARCHIVE=1 bash "$script" "${args[@]}" > "$tmp/stdout"
[[ -s $OUTPUT_FIXTURE ]]
grep -q -- '--entrypoint python' "$TRACE"
grep -q -- '--user 1000:1000' "$TRACE"
grep -q -- '--container-state-stdin' "$TRACE"
grep -q -- 'inspect --format' "$TRACE"
grep -q -- '"oom_killed":true' "$STATE_FIXTURE"
grep -q -- '"migrate":{"status":"exited","exit_code":1,"oom_killed":false' "$STATE_FIXTURE"
! grep -Eq '(^| )stop( |$)|(^| )logs( |$)|inspect --format$' "$TRACE"
grep -q -- '--env-file' "$TRACE"
rm -- "$OUTPUT_FIXTURE"
INSPECT_FAIL_FRONTEND=1 SIMULATE_ARCHIVE=1 bash "$script" "${args[@]}" > "$tmp/stdout"
[[ -s $OUTPUT_FIXTURE ]]
grep -q -- '--container-state-incomplete' "$TRACE"
echo 'offline wrapper fixture passed'

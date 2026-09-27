# Full local Qwen migration

> Execution: inline, using executing-plans and test-driven-development. User authorized the full migration and the discussed optimizations on 2026-09-27, including Proxmox/container changes. Continue without intermediate approvals within that scope.

## Design and constraints

All generation, chat, classification, development detection and translations use the existing Qwen3.6-35B-A3B Q6 server at 10.10.1.24:8080/v1. Preserve provider-neutral defaults for other installations; activate an explicit local_qwen profile in this installation. No cloud fallback. Existing bge-m3 embeddings and stored corpus stay unchanged.

Local profile: disable thinking, temperature .2, top_p .8, top_k 20, min_p 0, repeat_penalty 1, presence_penalty 0, prompt caching. Use task-specific JSON schemas and budgets: generation 4096 capped at 8192, classification/detection 512, chat 1536, translation 2048. Keep final content separate from reasoning; truncation (including empty final content) remains explicit. Validate JSON contracts before normalization.

One shared process-local GPU slot, chat priority between calls, bounded waiting and no release while a timed-out worker is still alive. One uvicorn worker in local deployment. First-token timeout 120 seconds, inter-token idle 30 seconds, chat total 180 seconds; bulk attempt total 300 seconds. Bound retry cascades per generation chunk. Preserve current behavior when profile is not local_qwen.

RAG sources 16000 characters, 5 blocks by default; retain 40 search candidates per branch. Enforce the serialized context budget including metadata for every source kind. Preserve source ancestry, mail scope and citations. Stream progress and answer text to the UI, with the final validated response authoritative. Cancellation closes the underlying LLM stream; failures are localized and never saved as completed turns.

Server: retain weights/build/Vulkan/MTP/Q8; reduce window to 32768, start with one slot and cache RAM 4096 MiB. Read live configuration and free slots first; back up unit/env before replacement, verify model ID and effective slot window after restart. Compare timings and correctness; do not claim speedup from allocated-window reduction alone.

## Tasks

- [x] 1. Tests then implementation: local profiles/schema contracts, reasoning-only truncation, first-token timeout, bounded retries, GPU scheduler priority/cancellation. Files: config.py, llm_client.py, new llm_profiles.py and llm_scheduler.py, LLM callers; tests/test_local_llm.py.
- [x] 2. Tests then implementation: serialized RAG budget and stream transport/UI, cancellation and safe errors. Files: context_builder.py, api/chat.py, frontend api.js and ChatPanel.jsx; backend/frontend focused tests.
- [x] 3. Verification: focused backend/frontend tests, relevant lint, independent final review. Add live synthetic acceptance script/report; include negative, table, code, mail and long-context cases.
- [x] 4. Deployment: preserve backups, update container unit, verify 32K/one slot/local sampling; apply verified files to primary checkout, update local env, restart only backend/frontend as needed, live browser/API acceptance. Record rollback and SECURITY.md trusted-LAN boundary.

## Review focus

- Reasoning-only output with length must retry/split, never be accepted as empty success.
- Timed-out or disconnected requests must not release a still-running GPU call or persist a partial chat answer.
- Classification, translation and concept schemas differ; no single schema for all chat_json callers.
- Mail provenance budget and citation numbering must match final transmitted source blocks.
- Network/deployment failure must leave a recoverable unit/env and report actual readiness.

## Ledger

Initial state: primary main 421e2bd, unrelated mail-filter docs dirty. Worktree created from main at C:/Users/alexey/.codex/worktrees/local-qwen/ai-knowledge. Implementation will be reviewed then applied narrowly to primary without staging unrelated files.
Ruling: execute already authorized design inline; no repeated design/plan confirmation. Changes to remote service and local env are included in the user's explicit full migration request.

Implementation: task contracts, local sampling options, first-token timeout,
single priority scheduler, bounded generation budget, strict serialized RAG
budget and NDJSON UI are implemented. Focused backend checks: 174 passing plus
one test double needing the new task argument; fixed and 17 stream/detection
checks passed. Local LLM tests 9 passed. Frontend 205 tests passed, lint clean,
isolated production build passed. Backend full suite in progress.

Independent reviewer found standard-profile streaming retries could concatenate
two attempts. Reproduced RED (two calls), fixed by disabling retries for visible
streams regardless of profile, GREEN (one call); reviewer confirmed closure.

Ruling: preserve canonical chunk boundaries and OKF_MAX_CHUNK_CHARS=8000.
Reduce query context only; avoiding unmeasured chunk changes preserves existing
source locations. Cost: generation of a large chunk can still be slow.

Deployment: server unit backed up to llama-server.service.pre-qwen20260927,
32K/one slot/4GiB cache verified live. Applied only migration files to primary
with per-file dirty checks; unrelated mail-filter docs preserved.
Original env and changed files saved under tests/tmp/qwen-rollback.
Backend restarted; health and all dependencies report ok.

Final: 2276 backend tests passed, 21 skipped; latest focused changes 83 passed. Frontend 205 passed, lint/build passed. Live synthetic 8/8; real UI fact and cancellation verified. See docs/LOCAL_LLM_ACCEPTANCE_2026-09-27.md for quality limits and evidence.
Ruling: constrain additional named-object focus to complete overview questions; other questions retain existing retrieval. Cost: unmatched phrasings retain a broader context. Both reviewer findings fixed and re-reviewed; no deferred minor findings.

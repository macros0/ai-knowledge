# LLM Proxy Routing Design

## Goal

Ensure every external LLM exchange initiated by OKF goes through the corporate
PII-protection proxy before reaching OpenRouter, while preserving the existing
model names and restoring redacted values in responses.

## Confirmed data flow

```text
OKF backend
  | original prompt + proxy credential
  v
corp-llm-gateway sidecar :8081/v1
  | PII detection and replacement
  v
LiteLLM gateway
  | sanitized prompt + server-side OpenRouter credential
  v
OpenRouter
  | sanitized response
  v
LiteLLM gateway / sidecar
  | restore placeholders
  v
OKF backend
```

The OpenRouter credential remains only on the gateway host. The OKF `.env`
contains the proxy credential, not an OpenRouter credential. The proxy's
restoration step is part of the trusted gateway path and must happen before
the response reaches OKF.

## Scope

### In scope

- Bulk OKF generation, interactive chat, table classification, development
  detection, and translation calls that use the shared `LLMClient`.
- The LLM dependency health check, which must probe the configured proxy
  endpoint rather than contain a direct OpenRouter fallback.
- Local environment documentation and tests proving that the configured
  endpoint is passed to LiteLLM for every completion.
- Removing the direct OpenRouter credential from the local OKF configuration.

### Explicitly out of scope

- Embeddings: they continue to use the local Ollama endpoint configured by
  `EMBEDDING_API_BASE`.
- Deploying, restarting, or administering Proxmox, Docker, Keycloak, or the
  gateway host.
- Changing model identifiers. `LLM_MODEL` and `LLM_CHAT_MODEL` remain the
  existing OpenRouter model names so the gateway can route them unchanged.
- Implementing PII detection or response restoration in OKF; those behaviors
  belong to the gateway described in the deployment document.

## Repository design

1. Keep `LLMClient` as the single completion transport. It already supplies
   `settings.llm_base_url` to LiteLLM, and translation reuses this client.
2. Configure `LLM_BASE_URL` in the local `.env` to the proxy's OpenAI-compatible
   `/v1` endpoint and configure `LLM_API_KEY` with the proxy credential. Do not
   copy the gateway's OpenRouter credential into OKF or into tracked files.
3. Update `.env.example` and runtime comments so the distinction between the
   proxy credential and the gateway-only OpenRouter credential is explicit.
4. Make the health check use the configured LLM base URL as its only target;
   if it is unset, report a configuration error rather than silently probing
   OpenRouter directly.
5. Use neutral dependency wording such as `LLM gateway` in backend/frontend
   health labels where the current text incorrectly names OpenRouter.

## Security and failure behavior

- No source file or tracked example may contain a live proxy or OpenRouter
  credential.
- OKF must never call `https://openrouter.ai` directly after this change.
- A failed proxy health check must identify the configured LLM gateway as
  unavailable; it must not imply that local Ollama embeddings are unavailable.
- Proxy authentication failures remain fatal and must not be retried as if they
  were transient network errors.
- Tests must use fake credentials and fake endpoints only.

## Verification

- Unit test that `LLMClient` passes the configured proxy `api_base` and proxy
  key to `litellm.completion`.
- Unit test that the health check requests `<configured-base>/models` and does
  not fall back to OpenRouter when the base is missing.
- Run the focused LLM, health, translation, and settings tests.
- Run a repository search over runtime sources and configuration examples to
  confirm there is no direct OpenRouter URL or credential path left in OKF.
- If the proxy is reachable, perform a non-sensitive `/v1/models` check and a
  minimal end-to-end request without real personal data. Do not place PII in
  test prompts or logs.

## Non-goals and operational note

The deployment document contains operational commands and credentials for an
already-deployed gateway. This repository change consumes only its endpoint,
credential roles, model-routing contract, and redaction/restoration behavior;
it does not execute those host-management commands. Credentials appearing in
that document should be rotated separately if the document has been exposed
outside the trusted environment.

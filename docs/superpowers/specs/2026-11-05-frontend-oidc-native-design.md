# Native real-OIDC frontend acceptance

Continue approved F2 with the admitted Gateway package and the verified bounded
OIDC wire component. These are implementation choices under the user's standing
autonomous instruction. They do not grant new cluster privileges or qualify
customer Gateway/IdP services. No further approval is needed for the existing
Gateway package; its controller is stopped and all earlier capacity loans closed.

Select a separate literal `owned-keycloak-local-v1` basic recipe. Keep simulation
admission and read-only defaults intact. Reuse the protected parent intent
protocol and its 12 stages, but select a fixed separate native coordinator and
declared source capsule. Do not disguise OIDC inputs as simulation inputs.

The application namespace remains `replica-test-<run_id>` with two frontend,
one combined backend/model and owned scoped TLS PG/Q providers. Backend Settings
stay legacy compact/combined/schema-none/frontend-default-one; Helm alone selects
frontend-split topology. Add a fresh `okf-f2-<run_id>` access namespace containing
Keycloak, Gateway/GatewayParameters and cluster-local identity/knowledge aliases.
The admitted platform controller stays in `okf-f2-gateway-system`. Only those fresh
namespaces receive the discovery label. No new cluster component is installed.

Native admission binds all three namespace/controller/Pod UIDs, actual image IDs,
GatewayClass, Gateway and ListenerSet/HTTPRoute status, TLS/realm/probe Secret and
ConfigMap hashes, exact provider/Service/EndpointSlice identities and physical
claim/node bindings. Derive both HTTPS origins from the access namespace; parent
payloads contain only nonsecret identity/image/UID fields. Passwords are read
inside the VM from the admitted synthetic-probe Secret, never from the parent.
The actual backend must use real Keycloak with secure sessions, verified HTTPS,
the fixed realm/client/full-path mapping, no default role, fake embeddings/stub
model/local artifacts, verify-full scoped PostgreSQL and a fresh empty corpus.

Prove both frontend paths through Gateway using two owned helper Services and
more-specific test HTTPRoutes matching a reserved header. Their fixed slot
selectors initially select the two exact frontend UIDs. Also exercise the
ordinary frontend Service route without the header. Neither backend nor internal
API is exposed as a Gateway backend. A replacement gets its predecessor's slot
label by one UID/resourceVersion-tested JSON patch; no Service selector changes.
Allow the first helper endpoint to be empty only after the recorded victim loss;
refuse foreign endpoints and unjournaled replacement binding.

A bounded streaming TLS transport shares OIDC cookies and CSRF headers with
business requests. Reuse upload/publication/export/SHA/search checks through an
explicit transport subclass; do not run those requests over plaintext Pod
forwards. Login uses the ordinary Gateway route; data/stream checks target both
admitted slots. An anonymous HTTP probe checks redirect to the fixed HTTPS origin
without sending cookies. Unknown-TLS-root and hidden-internal-route controls run
before credentials/business data. All mutation intents remain one-shot.

The loss client loads only SHA-verified immutable capsule modules from its
private execution directory. It reports a delivered NDJSON sources frame before
the existing strict model-active/PID gate admits one UID-preconditioned deletion.
No stream/model retry is allowed. Replacement keeps the same OIDC session and
data, then valid-token logout revokes old access on both slots and the ordinary
Service route and ends the IdP session. Native final proof still requires zero
SQL auth sessions: Authlib's post-logout credential-form probe creates only
signed-cookie OAuth state, not a server-side AuthSession row.

Only all 12 terminal native stages under the new exact capsule/recipe may clear
the selected `frontend.production_csrf` and `frontend.gateway_sso` checks. Partial
or mismatched stages and simulation keep them pending. Overall profile/trusted
qualification and certification remain separate; no historical report is signed
or adopted as this run's evidence. Close every owned process, stop the new
fixture/controller, retain data/diagnostics and return every fresh capacity loan.

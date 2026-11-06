# Owned frontend OIDC/CSRF probe

This continues approved F2: authenticate and log out through the Gateway, check
the production CSRF path, and preserve a shared session during frontend loss.
The user requested autonomous execution; the following implementation choices
are the agent's calls within that scope. Gateway installation remains a separate
pending platform approval; this design grants no permission to apply its package.

Use the existing home recipe: Keycloak 26.8.0, confidential client `knowledge`,
realm `okf-home`, ordinary authorization code flow without PKCE, full-path roles.
Keep the existing simulation runner unchanged. Add a separate bounded stdlib
HTTPS component that a future UID-bound native OIDC coordinator can call.

The transport accepts only `knowledge.okf-<lab>.svc.cluster.local` and
`identity.okf-<lab>.svc.cluster.local`, in the same namespace, mapped to one
explicit `127.0.0.1` port. TLS verifies the logical hostname with the private lab
CA; ambient DNS/proxy/CA settings and redirects cannot select another endpoint.
An empty independent trust store must reject the otherwise reachable TLS peer.

The client manually handles only the exact application login, Keycloak auth,
same-realm credential form, application callback, and RP logout transitions.
Validate state, redirect URI, issuer and client ID; reject duplicate parameters,
foreign redirects/forms, duplicate/ambiguous forms and oversized responses.
Never send a password to any other destination or log bodies, tokens or cookies.

Create durable private one-shot login/CSRF/logout intent files before any HTTP
action in that stage. Store cookies only in the same private directory; return
small allowlisted outcomes. Unknown actions are not replayable. Terminal results
can be read without another HTTP mutation; the later coordinator owns adoption.
This component is not a namespace admission gate or a signed evidence producer.

Require actual `mode=sso`, the expected username/role and secure HttpOnly session
cookie. Missing/wrong CSRF must return the exact `csrf_failed` denial while the
authenticated session remains intact. A correct token must permit logout,
revoke the previous application session and end the IdP session; a new login
must require credentials. The fixture tests use real loopback TLS and an HTTP
wire double, so they do not themselves qualify Keycloak, Gateway or production.

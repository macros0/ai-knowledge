# Staging companions

Files in this directory are optional companions for an isolated demo or test
environment. They are not part of the customer production topology.

`docker-compose.keycloak-demo.yml` starts a self-contained Keycloak only when
there is no customer-managed corporate IDB available. In a customer deployment,
do not include this Compose layer: configure `AUTH_PROVIDER=keycloak_oidc` with
the URL, realm, client ID, secret and redirect URIs supplied by the customer's
existing Keycloak/IDB.

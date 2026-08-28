"""Провайдеры аутентификации."""
from app.auth.providers.base import AuthProvider
from app.auth.providers.custom_client import CustomClientProvider
from app.auth.providers.direct_ldap import DirectLdapProvider
from app.auth.providers.disabled import DisabledProvider
from app.auth.providers.keycloak_oidc import KeycloakOidcProvider
from app.auth.providers.simulation import SimulationProvider

__all__ = [
    "AuthProvider",
    "CustomClientProvider",
    "DirectLdapProvider",
    "DisabledProvider",
    "KeycloakOidcProvider",
    "SimulationProvider",
]

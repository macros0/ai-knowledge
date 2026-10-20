"""Safe audit/session failure without replacing the current OIDC provider."""
from app.api.errors import ApiError


class AuthSessionStoreUnavailable(ApiError):
    def __init__(self):
        super().__init__(status_code=503, code="dependency_unavailable",
                         detail="Журнал безопасности временно недоступен")

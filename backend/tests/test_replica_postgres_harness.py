def test_disposable_url_repr_redacts_password_but_remains_a_driver_string():
    from tests.support.replica_postgres import PrivatePostgresUrl
    original = "postgresql+psycopg://replica:fixture-only-password@127.0.0.1:25431/replica_test_fixture01"
    value = PrivatePostgresUrl(original)
    assert "fixture-only-password" not in repr(value)
    assert isinstance(value, str) and str(value) == original

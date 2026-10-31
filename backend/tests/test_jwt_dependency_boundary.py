"""Exercise the real JOSE decoder used by Authlib, without an SSO fake."""
import pytest
from joserfc import jwt
from joserfc.errors import BadSignatureError, ExceededSizeError
from joserfc.jwk import OctKey


@pytest.mark.parametrize("value", [b"." * 200_000, b"a" * 200_000 + b".e30.signature"],
                         ids=["many-separators", "oversized-segment"])
def test_oversized_compact_jwt_rejected_before_segment_allocation(value):
    class GuardedBytes(bytes):
        def split(self, *args, **kwargs):
            pytest.fail("Oversized JWT reached segment allocation")

    key = OctKey.import_key(b"k" * 32)
    with pytest.raises(ExceededSizeError):
        jwt.decode(GuardedBytes(value), key, algorithms=["HS256"])


def test_real_signed_jwt_roundtrip_and_bad_signature():
    key = OctKey.import_key(b"k" * 32)
    token = jwt.encode({"alg": "HS256"}, {"sub": "user", "aud": "okf"}, key)
    assert jwt.decode(token, key, algorithms=["HS256"]).claims == {"sub": "user", "aud": "okf"}
    with pytest.raises(BadSignatureError):
        jwt.decode(token, OctKey.import_key(b"x" * 32), algorithms=["HS256"])

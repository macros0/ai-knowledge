import pytest
from pydantic import ValidationError
from app.config import Settings

@pytest.mark.parametrize("mode",["disabled","durable"])
@pytest.mark.parametrize("fmt",["native","logstash"])
def test_modes_and_formats(mode,fmt):
    settings=Settings(_env_file=None,audit_stdout_mode=mode,audit_stdout_format=fmt)
    assert settings.audit_stdout_mode==mode and settings.audit_stdout_format==fmt

def test_defaults_and_invalid_format():
    assert Settings(_env_file=None).audit_stdout_format=="native"
    with pytest.raises(ValidationError):Settings(_env_file=None,audit_stdout_format="unknown")

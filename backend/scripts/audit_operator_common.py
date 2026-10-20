"""Select explicit operator runtime configuration, never a root dotenv fallback."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def configure(path=None):
    from app import config
    if path is not None and not path.is_file():raise ValueError("Runtime configuration required")
    settings=config.Settings(_env_file=path if path is not None else None)
    if not settings.database_url and not settings.database_url_dev:raise ValueError("Explicit database configuration required")
    config.get_settings=lambda:settings
    return settings

"""Conftest: mock heavy dependencies so we can import encryption modules without the full OWUI app."""

import os
import sys
import types
from unittest.mock import MagicMock

# Determine the backend path for real module resolution
BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..")

# Mock heavy dependencies that get pulled in by open_webui.__init__
_MOCKED_MODULES = [
    "typer",
    "open_webui.models.chats",
    "open_webui.models.users",
]

# SQLAlchemy may not be installed locally — mock it if missing
try:
    import sqlalchemy  # noqa: F401
except ImportError:
    for sa_mod in [
        "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.orm.attributes",
        "sqlalchemy.orm.session",
    ]:
        sys.modules[sa_mod] = MagicMock()

for mod_name in _MOCKED_MODULES:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = MagicMock()

# Mock the Chat class specifically so db_encryption_shim can import it
chat_mock = sys.modules["open_webui.models.chats"]
chat_mock.Chat = type("Chat", (), {})

# Mock the Users class
users_mock = sys.modules["open_webui.models.users"]
users_mock.Users = MagicMock()

# Create real namespace packages with correct __path__ so submodule imports resolve
_PKG_PATHS = {
    "open_webui": os.path.join(BACKEND_DIR, "open_webui"),
    "open_webui.utils": os.path.join(BACKEND_DIR, "open_webui", "utils"),
    "open_webui.models": os.path.join(BACKEND_DIR, "open_webui", "models"),
}

for pkg, path in _PKG_PATHS.items():
    if pkg not in sys.modules or isinstance(sys.modules[pkg], MagicMock):
        mod = types.ModuleType(pkg)
        mod.__path__ = [path]
        mod.__package__ = pkg
        sys.modules[pkg] = mod

# Mock the logger ASSERT function used by encryption_utils
logger_mock = types.ModuleType("open_webui.utils.logger")
logger_mock.ASSERT = MagicMock()
sys.modules["open_webui.utils.logger"] = logger_mock

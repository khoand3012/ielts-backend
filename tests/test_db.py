def test_db_module_imports() -> None:
    from core.db import async_session_factory, engine, get_session  # noqa: F401
    from core.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin  # noqa: F401

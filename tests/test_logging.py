from core.logging import configure_logging, get_logger


def test_get_logger_returns_bound_logger() -> None:
    configure_logging()
    log = get_logger("test")
    log.info("hello", key="value")  # must not raise
    assert log is not None

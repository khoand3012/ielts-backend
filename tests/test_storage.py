import time

from core.services.storage import signed_get_url
from core.settings import get_settings


async def test_signed_get_url_is_signed_for_the_key() -> None:
    url = await signed_get_url("tests/x/a.mp3")
    assert "tests/x/a.mp3" in url
    assert "X-Amz-Signature=" in url


def test_signed_urls_outlive_the_longest_exam() -> None:
    settings = get_settings()
    longest = max(
        settings.reading_time_limit_minutes,
        settings.listening_time_limit_minutes,
        settings.writing_time_limit_minutes,
        settings.speaking_time_limit_minutes,
    )
    assert settings.signed_url_ttl_seconds >= longest * 60 + settings.session_grace_seconds


async def test_signing_many_keys_does_not_stall_the_event_loop() -> None:
    await signed_get_url("tests/warmup.mp3")
    start = time.perf_counter()
    for n in range(20):
        await signed_get_url(f"tests/x/{n}.mp3")
    assert time.perf_counter() - start < 0.5

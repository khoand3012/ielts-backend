from core.services.storage import signed_get_url


async def test_signed_get_url_is_signed_for_the_key() -> None:
    url = await signed_get_url("tests/x/a.mp3")
    assert "tests/x/a.mp3" in url
    assert "X-Amz-Signature=" in url

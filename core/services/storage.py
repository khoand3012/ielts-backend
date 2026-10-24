"""R2 (S3-compatible) object storage. Only the endpoint and credentials differ from S3."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aioboto3
from botocore.config import Config

from core.settings import get_settings

# (key, local file, content type) -> uploaded. Injected in tests so R2 is never called.
Uploader = Callable[[str, Path, str], Awaitable[None]]


@asynccontextmanager
async def _client() -> AsyncIterator[Any]:
    settings = get_settings()
    session = aioboto3.Session()
    async with session.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url,
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
        config=Config(signature_version="s3v4"),
    ) as client:
        yield client


async def upload_file(key: str, path: Path, content_type: str) -> None:
    async with _client() as client:
        await client.upload_file(
            str(path), get_settings().r2_bucket, key, ExtraArgs={"ContentType": content_type}
        )


async def signed_get_url(key: str) -> str:
    settings = get_settings()
    async with _client() as client:
        url: str = await client.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.r2_bucket, "Key": key},
            ExpiresIn=settings.signed_url_ttl_seconds,
        )
    return url

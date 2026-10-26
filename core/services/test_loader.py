"""Load a test from a JSON file (one skill per file) into a draft, optionally publishing it."""

import asyncio
import hashlib
import json
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import ContentProblemsError, ValidationAppError
from core.models import Module, Skill, TestVersion
from core.schemas.content import Problem, StaffContent
from core.services import catalog, storage
from core.services.content import asset_keys, parse_content


class TestFile(BaseModel):
    __test__ = False  # not a pytest test class
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(pattern=r"^[a-z0-9-]+$", max_length=100)
    title: str = Field(max_length=200)
    skill: Skill
    # Reading only: academic | general_training
    module: Module | None = None
    content: dict[str, Any]
    answer_key: dict[int, list[str]] = {}
    staff_content: dict[str, Any] = {}
    # R2 object key -> local file, relative to the test file
    assets: dict[str, str] = {}


@dataclass
class LoadResult:
    slug: str
    version: TestVersion
    problems: list[Problem]
    published: bool


async def _stored_key(slug: str, local: Path) -> str:
    digest = hashlib.sha256(await asyncio.to_thread(local.read_bytes)).hexdigest()[:16]
    return f"tests/{slug}/{digest}/{local.name}"


def _rename_assets(node: Any, renames: dict[str, str]) -> Any:
    """Copy of the content with each asset key (an `audio`/`image` value) swapped for its
    stored key."""
    if isinstance(node, dict):
        return {
            k: renames.get(v, v)
            if k in ("audio", "image") and isinstance(v, str)
            else _rename_assets(v, renames)
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [_rename_assets(v, renames) for v in node]
    return node


async def load_test_file(
    db: AsyncSession,
    path: Path,
    *,
    publish: bool,
    upload: storage.Uploader | None = None,
) -> LoadResult:
    upload = upload or storage.upload_file
    try:
        raw = await asyncio.to_thread(path.read_text, encoding="utf-8")
        file = TestFile.model_validate(json.loads(raw))
        content = parse_content(file.skill, file.content)
        staff_content = StaffContent.model_validate(file.staff_content)
    except (OSError, ValueError, ValidationError) as exc:
        raise ValidationAppError(f"invalid test file {path}: {exc}") from exc
    catalog.check_module(file.skill, file.module)

    for key in asset_keys(file.skill, content):
        if key not in file.assets:
            raise ValidationAppError(f"asset {key} has no file in 'assets'")
    files = {key: path.parent / rel for key, rel in file.assets.items()}
    for key, local in files.items():
        if not await asyncio.to_thread(local.is_file):
            raise ValidationAppError(f"asset file {local} for {key} does not exist")
    # Files live under a key built from their bytes, so loading a changed file never replaces
    # the object that a published (or running) version points at.
    stored = {key: await _stored_key(file.slug, local) for key, local in files.items()}
    for key, local in files.items():
        content_type = mimetypes.guess_type(local.name)[0] or "application/octet-stream"
        await upload(stored[key], local, content_type)
    content = parse_content(file.skill, _rename_assets(file.content, stored))

    version = await catalog.save_draft(
        db,
        slug=file.slug,
        title=file.title,
        skill=file.skill,
        content=content,
        answer_key=file.answer_key,
        staff_content=staff_content,
        module=file.module,
    )
    problems = catalog.problems_for(file.skill, version)
    published = False
    if publish:
        try:
            version = await catalog.publish(db, version.id)
            published = True
        except ContentProblemsError:
            await db.refresh(version)
    return LoadResult(file.slug, version, problems, published)

"""Load a test from a JSON file (one skill per file) into a draft, optionally publishing it."""

import asyncio
import json
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import ContentProblemsError, ValidationAppError
from core.models import Skill, TestVersion
from core.schemas.content import Problem, StaffContent
from core.services import catalog, storage
from core.services.content import asset_keys, parse_content


class TestFile(BaseModel):
    __test__ = False  # not a pytest test class
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(pattern=r"^[a-z0-9-]+$", max_length=100)
    title: str = Field(max_length=200)
    skill: Skill
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

    for key in asset_keys(file.skill, content):
        if key not in file.assets:
            raise ValidationAppError(f"asset {key} has no file in 'assets'")
    files = {key: path.parent / rel for key, rel in file.assets.items()}
    for key, local in files.items():
        if not await asyncio.to_thread(local.is_file):
            raise ValidationAppError(f"asset file {local} for {key} does not exist")
    for key, local in files.items():
        content_type = mimetypes.guess_type(local.name)[0] or "application/octet-stream"
        await upload(key, local, content_type)

    version = await catalog.save_draft(
        db,
        slug=file.slug,
        title=file.title,
        skill=file.skill,
        content=content,
        answer_key=file.answer_key,
        staff_content=staff_content,
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

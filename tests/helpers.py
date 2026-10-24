from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from core.models import Skill, TestVersion
from core.schemas.content import AnswerKey, StaffContent
from core.services import catalog
from core.services.content import parse_content
from tests.content_factories import (
    listening_content,
    listening_key,
    listening_staff,
    reading_content,
    reading_key,
    speaking_content,
    writing_content,
)

_DEFAULTS: dict[Skill, tuple[Any, Any, Any]] = {
    Skill.READING: (reading_content, reading_key, dict),
    Skill.LISTENING: (listening_content, listening_key, listening_staff),
    Skill.WRITING: (writing_content, dict, dict),
    Skill.SPEAKING: (speaking_content, dict, dict),
}


async def publish_test(
    db: AsyncSession,
    skill: Skill,
    *,
    slug: str | None = None,
    content: dict[str, Any] | None = None,
    key: AnswerKey | None = None,
) -> TestVersion:
    """Save and publish a valid factory test (a new version if `slug` already exists)."""
    make_content, make_key, make_staff = _DEFAULTS[skill]
    version = await catalog.save_draft(
        db,
        slug=slug or f"{skill}-{uuid7()}",
        title=f"{skill} test",
        skill=skill,
        content=parse_content(skill, content or make_content()),
        answer_key=make_key() if key is None else key,
        staff_content=StaffContent.model_validate(make_staff()),
    )
    return await catalog.publish(db, version.id)

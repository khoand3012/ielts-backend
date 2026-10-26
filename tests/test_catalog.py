from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from core.exceptions import ConflictError, ContentProblemsError, NotFoundError, ValidationAppError
from core.models import Module, Skill, Test, TestVersion
from core.schemas.content import AnswerKey, StaffContent
from core.services import catalog
from core.services.content import parse_content
from tests.content_factories import (
    listening_content,
    listening_key,
    reading_content,
    reading_key,
)


async def save(
    db: AsyncSession,
    slug: str,
    skill: Skill = Skill.READING,
    content: dict[str, Any] | None = None,
    key: AnswerKey | None = None,
    title: str = "T",
) -> TestVersion:
    return await catalog.save_draft(
        db,
        slug=slug,
        title=title,
        skill=skill,
        content=parse_content(skill, content or reading_content()),
        answer_key=reading_key() if key is None else key,
        staff_content=StaffContent(),
        module=Module.ACADEMIC if skill == Skill.READING else None,
    )


def slug() -> str:
    return f"cat-{uuid7()}"


async def test_first_save_creates_version_1_draft(db_session: AsyncSession) -> None:
    v = await save(db_session, slug())
    assert (v.version, v.status) == (1, "draft")


async def test_resave_replaces_the_draft(db_session: AsyncSession) -> None:
    s = slug()
    v1 = await save(db_session, s)
    v1b = await save(db_session, s, title="New title")
    assert v1b.id == v1.id
    assert v1b.version == 1
    test = await db_session.get(Test, v1.test_id)
    assert test is not None
    assert test.title == "New title"


async def test_save_after_publish_creates_version_2(db_session: AsyncSession) -> None:
    s = slug()
    v1 = await save(db_session, s)
    await catalog.publish(db_session, v1.id)
    v2 = await save(db_session, s)
    assert (v2.version, v2.status) == (2, "draft")


async def test_answer_key_round_trips_with_int_keys(db_session: AsyncSession) -> None:
    v = await save(db_session, slug())
    assert catalog.problems_for(Skill.READING, v) == []


async def test_publish_rejects_version_with_problems(db_session: AsyncSession) -> None:
    content = listening_content()
    content["parts"][0]["audio"] = None
    v = await save(db_session, slug(), Skill.LISTENING, content, listening_key())
    with pytest.raises(ContentProblemsError) as exc:
        await catalog.publish(db_session, v.id)
    problems: Any = exc.value.extra["problems"]
    assert problems[0]["code"] == "missing_audio"
    await db_session.refresh(v)
    assert v.status == "draft"


async def test_publish_retires_previous_version(db_session: AsyncSession) -> None:
    s = slug()
    v1 = await catalog.publish(db_session, (await save(db_session, s)).id)
    assert v1.published_at is not None
    v2 = await catalog.publish(db_session, (await save(db_session, s)).id)
    await db_session.refresh(v1)
    assert v1.status == "retired"
    test = await catalog.get_published_test(db_session, v2.test_id)
    assert test.current_version_id == v2.id


async def test_publishing_a_non_draft_conflicts(db_session: AsyncSession) -> None:
    v = await catalog.publish(db_session, (await save(db_session, slug())).id)
    with pytest.raises(ConflictError):
        await catalog.publish(db_session, v.id)


async def test_unpublished_test_not_listed_or_fetchable(db_session: AsyncSession) -> None:
    v = await save(db_session, slug(), Skill.READING)
    with pytest.raises(NotFoundError):
        await catalog.get_published_test(db_session, v.test_id)
    page, _ = await catalog.list_published(db_session, skill=Skill.READING, limit=100, offset=0)
    assert v.test_id not in {t.id for t in page}


async def test_published_test_listed_by_skill(db_session: AsyncSession) -> None:
    v = await catalog.publish(db_session, (await save(db_session, slug())).id)
    reading, _ = await catalog.list_published(db_session, skill=Skill.READING, limit=100, offset=0)
    writing, _ = await catalog.list_published(db_session, skill=Skill.WRITING, limit=100, offset=0)
    assert v.test_id in {t.id for t in reading}
    assert v.test_id not in {t.id for t in writing}


async def test_skill_change_for_slug_rejected(db_session: AsyncSession) -> None:
    s = slug()
    await save(db_session, s)
    with pytest.raises(ConflictError):
        await save(db_session, s, Skill.LISTENING, listening_content(), listening_key())


async def test_save_draft_checks_module(db_session: AsyncSession) -> None:
    with pytest.raises(ValidationAppError):
        await catalog.save_draft(
            db_session,
            slug=slug(),
            title="T",
            skill=Skill.READING,
            content=parse_content(Skill.READING, reading_content()),
            answer_key=reading_key(),
            staff_content=StaffContent(),
        )

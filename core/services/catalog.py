"""Tests and their versions: drafts, publishing, and what students may browse."""

import uuid
from datetime import UTC, datetime

from pydantic import TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import ConflictError, ContentProblemsError, NotFoundError, ValidationAppError
from core.models import Module, Skill, Test, TestVersion, VersionStatus
from core.schemas.content import AnswerKey, Content, Problem, StaffContent
from core.services.content import parse_content, validate_version

# JSONB object keys are strings; this restores the int question numbers on the way out.
_answer_key = TypeAdapter(AnswerKey)


def load_content(skill: Skill, version: TestVersion) -> Content:
    return parse_content(skill, version.content)


def load_answer_key(version: TestVersion) -> AnswerKey:
    return _answer_key.validate_python(version.answer_key)


def problems_for(skill: Skill, version: TestVersion) -> list[Problem]:
    return validate_version(skill, load_content(skill, version), load_answer_key(version))


def check_module(skill: Skill, module: Module | None) -> None:
    """Reading tests are Academic or General Training; no other skill has a module."""
    if skill == Skill.READING and module is None:
        raise ValidationAppError("reading tests need a module: academic or general_training")
    if skill != Skill.READING and module is not None:
        raise ValidationAppError("only reading tests have a module")


async def save_draft(
    db: AsyncSession,
    *,
    slug: str,
    title: str,
    skill: Skill,
    content: Content,
    answer_key: AnswerKey,
    staff_content: StaffContent,
    module: Module | None = None,
) -> TestVersion:
    """Replace the test's draft, or start a new draft version on top of the latest one."""
    check_module(skill, module)
    test = await db.scalar(select(Test).where(Test.slug == slug))
    if test is None:
        test = Test(skill=skill, slug=slug, title=title)
        db.add(test)
        await db.flush()
    elif test.skill != skill:
        raise ConflictError(f"test {slug} is a {test.skill} test, not {skill}")
    test.title = title

    documents = {
        "content": content.model_dump(mode="json"),
        "answer_key": _answer_key.dump_python(answer_key, mode="json"),
        "staff_content": staff_content.model_dump(mode="json"),
        "module": module,
    }
    draft = await db.scalar(
        select(TestVersion).where(
            TestVersion.test_id == test.id, TestVersion.status == VersionStatus.DRAFT
        )
    )
    if draft is None:
        latest = await db.scalar(
            select(func.max(TestVersion.version)).where(TestVersion.test_id == test.id)
        )
        draft = TestVersion(test_id=test.id, version=(latest or 0) + 1, **documents)
        db.add(draft)
    else:
        for field, value in documents.items():
            setattr(draft, field, value)
    await db.commit()
    await db.refresh(draft)
    return draft


async def publish(db: AsyncSession, version_id: uuid.UUID) -> TestVersion:
    version = await db.get(TestVersion, version_id, with_for_update=True)
    if version is None:
        raise NotFoundError("test version not found")
    if version.status != VersionStatus.DRAFT:
        raise ConflictError("only a draft can be published")
    test = await db.get(Test, version.test_id, with_for_update=True)
    assert test is not None  # FK

    problems = problems_for(Skill(test.skill), version)
    if any(p.severity == "error" for p in problems):
        await db.rollback()
        raise ContentProblemsError(
            "test version has problems", extra={"problems": [p.model_dump() for p in problems]}
        )

    if test.current_version_id is not None:
        previous = await db.get(TestVersion, test.current_version_id)
        if previous is not None:
            previous.status = VersionStatus.RETIRED
    version.status = VersionStatus.PUBLISHED
    version.published_at = datetime.now(UTC)
    test.current_version_id = version.id
    await db.commit()
    await db.refresh(version)
    return version


async def list_published(
    db: AsyncSession, *, skill: Skill | None = None, limit: int = 50, offset: int = 0
) -> tuple[list[Test], int]:
    stmt = select(Test).where(Test.current_version_id.is_not(None))
    if skill is not None:
        stmt = stmt.where(Test.skill == skill)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    page = await db.scalars(
        stmt.order_by(Test.created_at.desc(), Test.id.desc()).limit(limit).offset(offset)
    )
    return list(page), total or 0


async def get_published_test(db: AsyncSession, test_id: uuid.UUID) -> Test:
    test = await db.get(Test, test_id)
    if test is None or test.current_version_id is None:
        raise NotFoundError("test not found")
    return test

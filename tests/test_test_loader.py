import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from core.exceptions import ValidationAppError
from core.services.test_loader import load_test_file
from tests.content_factories import (
    listening_content,
    listening_key,
    reading_content,
    reading_key,
    writing_content,
)

SEED = Path(__file__).parent.parent / "seed" / "tests"


class FakeUploader:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Path, str]] = []

    async def __call__(self, key: str, path: Path, content_type: str) -> None:
        self.calls.append((key, path, content_type))


def write(tmp_path: Path, data: dict[str, Any]) -> Path:
    path = tmp_path / "test.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def make_file(skill: str, content: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"slug": f"load-{uuid7()}", "title": "T", "skill": skill, "content": content, **extra}


async def test_load_publishes_valid_reading(db_session: AsyncSession, tmp_path: Path) -> None:
    path = write(tmp_path, make_file("reading", reading_content(), answer_key=reading_key()))
    result = await load_test_file(db_session, path, publish=True, upload=FakeUploader())
    assert result.published is True
    assert result.problems == []
    assert result.version.status == "published"


async def test_load_without_publish_stays_draft(db_session: AsyncSession, tmp_path: Path) -> None:
    path = write(tmp_path, make_file("reading", reading_content(), answer_key=reading_key()))
    result = await load_test_file(db_session, path, publish=False, upload=FakeUploader())
    assert (result.published, result.version.status) == (False, "draft")


async def test_load_uploads_assets_to_their_keys(db_session: AsyncSession, tmp_path: Path) -> None:
    (tmp_path / "chart.svg").write_text("<svg/>", encoding="utf-8")
    data = make_file("writing", writing_content(), assets={"tests/f/task1.svg": "chart.svg"})
    upload = FakeUploader()
    result = await load_test_file(db_session, write(tmp_path, data), publish=True, upload=upload)
    [(key, local, content_type)] = upload.calls
    assert local == tmp_path / "chart.svg"
    assert content_type == "image/svg+xml"
    # stored under a key that depends on the file's bytes, and the content points at it
    assert key.startswith(f"tests/{data['slug']}/") and key.endswith("/chart.svg")
    assert result.version.content["tasks"][0]["image"] == key


async def test_changed_asset_never_overwrites_a_published_versions_file(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    (tmp_path / "chart.svg").write_text("<svg>v1</svg>", encoding="utf-8")
    data = make_file("writing", writing_content(), assets={"tests/f/task1.svg": "chart.svg"})
    path = write(tmp_path, data)
    upload = FakeUploader()
    v1 = (await load_test_file(db_session, path, publish=True, upload=upload)).version

    (tmp_path / "chart.svg").write_text("<svg>v2</svg>", encoding="utf-8")
    v2 = (await load_test_file(db_session, path, publish=False, upload=upload)).version

    first_key, second_key = (call[0] for call in upload.calls)
    assert first_key != second_key
    assert v1.content["tasks"][0]["image"] == first_key
    assert v2.content["tasks"][0]["image"] == second_key


async def test_missing_asset_file_rejected(db_session: AsyncSession, tmp_path: Path) -> None:
    data = make_file("writing", writing_content(), assets={"tests/f/task1.svg": "nope.svg"})
    with pytest.raises(ValidationAppError):
        await load_test_file(
            db_session, write(tmp_path, data), publish=False, upload=FakeUploader()
        )


async def test_asset_without_file_entry_rejected(db_session: AsyncSession, tmp_path: Path) -> None:
    data = make_file("writing", writing_content())
    with pytest.raises(ValidationAppError):
        await load_test_file(
            db_session, write(tmp_path, data), publish=False, upload=FakeUploader()
        )


async def test_load_with_problems_stays_draft(db_session: AsyncSession, tmp_path: Path) -> None:
    content = listening_content()
    for part in content["parts"]:
        part["audio"] = None
    path = write(tmp_path, make_file("listening", content, answer_key=listening_key()))
    result = await load_test_file(db_session, path, publish=True, upload=FakeUploader())
    assert result.published is False
    assert result.version.status == "draft"
    assert result.problems[0].code == "missing_audio"


async def test_seed_files_load(db_session: AsyncSession, tmp_path: Path) -> None:
    for skill in ("reading", "listening", "writing", "speaking"):
        source = SEED / f"practice-01-{skill}.json"
        data = json.loads(source.read_text(encoding="utf-8"))
        data["slug"] = f"seed-{skill}-{uuid7()}"
        # keep asset paths resolvable: write the copy next to the original's assets
        copy = SEED / f".tmp-{uuid7()}.json"
        copy.write_text(json.dumps(data), encoding="utf-8")
        try:
            result = await load_test_file(db_session, copy, publish=True, upload=FakeUploader())
        finally:
            copy.unlink()
        if skill == "listening":
            assert result.published is False
            assert [p.code for p in result.problems] == ["missing_audio"] * 4
        else:
            assert result.problems == [], (skill, result.problems)
            assert result.published is True

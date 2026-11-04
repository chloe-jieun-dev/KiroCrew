"""Tests for ``GET /api/project/tree?shallow=1``: one level per request.

The app, the sandbox passthrough and the project fixtures are
``test_project_tree.py``'s, imported so pytest registers them here too.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer
from test_project_tree import (  # noqa: F401
    _git,
    _make_app,
    _repo_template,
    mock_sel,
    passthrough_sandbox,
    plain_project,
    repo,
)

from kiro_crew.dashboard.file_api.project_tree import _project_tree_shallow_git


async def _get(project: Path, **query: str) -> tuple[int, dict]:
    async with TestClient(TestServer(_make_app(str(project)))) as client:
        resp = await client.get("/api/project/tree", params={"path": str(project), **query})
        return resp.status, await resp.json()


class TestShallowWalk:
    @pytest.mark.asyncio
    async def test_reads_the_root_alone_and_names_its_folders_lazy(
        self, plain_project, mock_sel  # noqa: F811
    ):
        (plain_project / "a" / "deep").mkdir(parents=True)
        (plain_project / "a" / "deep" / "leaf.txt").write_text("x")
        (plain_project / "b").mkdir()
        (plain_project / "top.txt").write_text("x")

        status, data = await _get(plain_project, shallow="1")
        assert status == 200
        assert data["paths"] == ["top.txt"]
        assert data["directories"] == ["a", "b"]
        assert data["lazyDirectories"] == ["a", "b"]
        # Not read is not cut: no folder is reported truncated or hidden-only.
        assert data["truncated"] is False
        assert data["truncatedDirectories"] == []
        assert data["hiddenOnlyDirectories"] == []

    @pytest.mark.asyncio
    async def test_a_folder_listing_is_one_level_too(self, plain_project, mock_sel):  # noqa: F811
        (plain_project / "a" / "deep" / "deeper").mkdir(parents=True)
        (plain_project / "a" / "deep" / "deeper" / "leaf.txt").write_text("x")
        (plain_project / "a" / "one.txt").write_text("x")

        status, data = await _get(plain_project, shallow="1", dir="a")
        assert status == 200
        assert data["dir"] == "a"
        assert data["paths"] == ["a/one.txt"]
        assert data["directories"] == ["a/deep"]
        assert data["lazyDirectories"] == ["a/deep"]

    @pytest.mark.asyncio
    async def test_without_the_flag_the_whole_tree_is_listed(
        self, plain_project, mock_sel  # noqa: F811
    ):
        (plain_project / "a" / "deep").mkdir(parents=True)
        (plain_project / "a" / "deep" / "leaf.txt").write_text("x")

        status, data = await _get(plain_project)
        assert status == 200
        assert data["paths"] == ["a/deep/leaf.txt"]
        assert data["lazyDirectories"] == []

    @pytest.mark.asyncio
    async def test_the_project_allow_list_still_applies(self, tmp_path, mock_sel):  # noqa: F811
        known = tmp_path / "known"
        known.mkdir()
        other = tmp_path / "other"
        other.mkdir()
        async with TestClient(TestServer(_make_app(str(known)))) as client:
            resp = await client.get(
                "/api/project/tree", params={"path": str(other), "shallow": "1"}
            )
        assert resp.status == 403

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
        reason="POSIX permission bits; root reads any folder",
    )
    async def test_a_folder_that_cannot_be_opened_is_named_unreadable_not_lazy(
        self, plain_project, mock_sel  # noqa: F811
    ):
        locked = plain_project / "locked"
        locked.mkdir(parents=True)
        (locked / "x.txt").write_text("x")
        (plain_project / "open").mkdir()
        locked.chmod(0)
        try:
            status, data = await _get(plain_project, shallow="1")
        finally:
            locked.chmod(0o755)
        assert status == 200
        assert data["directories"] == ["locked", "open"]
        assert data["unreadableDirectories"] == ["locked"]
        assert data["lazyDirectories"] == ["open"]


class TestShallowGit:
    @pytest.mark.asyncio
    async def test_a_repository_lists_one_level_and_keeps_gitignore(
        self, repo, mock_sel  # noqa: F811
    ):
        (repo / "ignored.log").write_text("x")
        (repo / "src" / "pkg").mkdir()
        (repo / "src" / "pkg" / "inner.py").write_text("x")
        status, data = await _get(repo, shallow="1")
        assert status == 200
        assert data["repo"] is True
        assert sorted(data["paths"]) == [".gitignore", "a.txt"]
        assert data["directories"] == ["src"]
        assert data["lazyDirectories"] == ["src"]

    def test_split_keeps_direct_files_and_first_segments(self):
        files, nested, lazy = _project_tree_shallow_git(
            ["a.txt", "src/mod.py", "src/pkg/x.py", "docs/i.md"], {"vendor", "src/sub"}
        )
        assert files == ["a.txt"]
        assert nested == {"vendor"}
        assert lazy == {"src", "docs"}

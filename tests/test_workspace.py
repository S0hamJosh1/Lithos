"""Tests for the workspace facade (evcide.workspace)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pytest

from evcide import workspace
from evcide.models import ProjectConfig

PROFILE = "seeed_xiao_nrf52840_sense"


def test_create_project_delegates_to_adapter(tmp_path):
    cfg = ProjectConfig(profile_id=PROFILE, name="blink", framework="zephyr",
                        target_dir=str(tmp_path / "p"))
    res = asyncio.run(workspace.create_project(cfg))
    assert res.success is True
    assert (tmp_path / "p" / "prj.conf").exists()


def test_import_project_classifies_when_no_profile(tmp_path):
    (tmp_path / "prj.conf").write_text("CONFIG_PRINTK=y\n", encoding="utf-8")
    (tmp_path / "CMakeLists.txt").write_text("set(BOARD xiao_ble_sense)\n", encoding="utf-8")
    meta, classification = asyncio.run(workspace.import_project(str(tmp_path)))
    assert classification is not None          # inferred the profile from markers
    assert meta.root


def test_import_with_explicit_profile_skips_classification(tmp_path):
    (tmp_path / "prj.conf").write_text("CONFIG_PRINTK=y\n", encoding="utf-8")
    meta, classification = asyncio.run(workspace.import_project(str(tmp_path), profile_id=PROFILE))
    assert classification is None              # supplied profile -> no classify needed
    assert meta.root


def test_import_raises_when_unclassifiable(tmp_path):
    with pytest.raises(workspace.ClassificationError):
        asyncio.run(workspace.import_project(str(tmp_path)))   # empty dir, no markers

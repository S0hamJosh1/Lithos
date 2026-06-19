"""Workspace manager — project-lifecycle facade (PDF architecture §11).

Centralizes create / import / board-detection so the API and frontend have one
stable project-lifecycle surface, independent of which board adapter handles a
given profile. Thin by design: it delegates to the adapter registry + classifier
and owns the cross-cutting glue (classify-when-no-profile), with no logic
duplicated across call sites. The HTTP layer keeps only HTTP concerns
(status-code mapping, WAL); the lifecycle lives here.
"""
from __future__ import annotations

from .adapters import (
    BoardAdapter,
    detect_all_boards,
    get_adapter_for_profile,
    list_adapters,
)
from .classify import classify_project
from .models import (
    DetectedBoard,
    ProjectClassification,
    ProjectConfig,
    ProjectMetadata,
    ProjectResult,
)


class ClassificationError(ValueError):
    """Raised when import is called without a profile_id and the project can't be
    classified from on-disk markers."""


async def create_project(config: ProjectConfig) -> ProjectResult:
    """Scaffold a project for `config.profile_id` via its adapter."""
    adapter = get_adapter_for_profile(config.profile_id)
    return await adapter.create_project(config)


async def import_project(
    path: str, profile_id: str | None = None
) -> tuple[ProjectMetadata, ProjectClassification | None]:
    """Import an existing project. When `profile_id` is omitted, classify it from
    on-disk markers; raise ClassificationError if undeterminable. Returns the
    metadata and the classification used (None when a profile_id was supplied).
    """
    classification: ProjectClassification | None = None
    if not profile_id:
        classification = classify_project(path)
        if classification is None:
            raise ClassificationError(
                "could not classify project from on-disk markers; pass profile_id explicitly"
            )
        profile_id = classification.profile_id
    adapter = get_adapter_for_profile(profile_id)
    meta = await adapter.import_project(path)
    return meta, classification


async def detect_boards() -> list[DetectedBoard]:
    """Probe the host for connected boards across every adapter family."""
    return await detect_all_boards()


def supported_adapters() -> list[BoardAdapter]:
    """The registered board adapters (for capability listing)."""
    return list_adapters()

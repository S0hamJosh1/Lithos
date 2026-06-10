"""BoardAdapter abstract base.

Maps directly to the TypeScript interface in PDF Section 13.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import (
    BoardFamily,
    BoardProfile,
    BuildResult,
    DetectedBoard,
    FlashResult,
    OutputConfig,
    OutputSession,
    ProjectConfig,
    ProjectMetadata,
    ProjectResult,
    RepairHint,
    VerificationContract,
    VerificationResult,
)


class BoardAdapter(ABC):
    """One adapter per board family."""

    #: Unique adapter id ("nrf52-adapter", "stm32-adapter", etc.).
    id: str

    #: Board family this adapter handles.
    family: BoardFamily

    #: Board profiles this adapter can target.
    profiles: dict[str, BoardProfile]

    # ----- Detection -----

    @abstractmethod
    async def detect(self) -> list[DetectedBoard]:
        """Probe the host for connected boards of this family.

        Should be evidence-based (USB VID/PID, serial enumeration, mass storage,
        probe enumeration, tool-specific identify commands) per PDF Section 15.
        Returns an empty list rather than raising when nothing is connected.
        """

    # ----- Project lifecycle -----

    @abstractmethod
    async def create_project(self, config: ProjectConfig) -> ProjectResult:
        """Scaffold a fresh project for this board family."""

    @abstractmethod
    async def import_project(self, path: str) -> ProjectMetadata:
        """Read an existing project on disk; classify framework + tooling."""

    # ----- Build / Flash -----

    @abstractmethod
    async def build(self, project_path: str) -> BuildResult:
        """Run the framework-appropriate build. Captures diagnostics."""

    @abstractmethod
    async def flash(self, project_path: str, device: DetectedBoard) -> FlashResult:
        """Flash artifact onto the device. Does not perform verification."""

    # ----- Runtime / verification -----

    @abstractmethod
    async def open_output_channel(
        self, device: DetectedBoard, config: OutputConfig
    ) -> OutputSession:
        """Open the requested receiver against the device; return a stream handle."""

    @abstractmethod
    async def verify(
        self, session: OutputSession, contract: VerificationContract
    ) -> VerificationResult:
        """Run a verification contract against the open session."""

    @abstractmethod
    async def repair_hints(self, result: VerificationResult) -> list[RepairHint]:
        """Convert a failed verification into actionable repair hints."""

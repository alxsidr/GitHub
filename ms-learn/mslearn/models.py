from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class ContentBlockType(Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    TABLE = "table"
    IMAGE = "image"
    NOTE = "note"
    CODE = "code"
    QUIZ = "quiz"


@dataclass
class ContentBlock:
    block_type: ContentBlockType
    text: str = ""
    level: int = 2  # heading level (2 or 3)
    image_url: str = ""
    image_alt: str = ""
    image_path: Path | None = None
    list_items: list[str] = field(default_factory=list)
    ordered: bool = False
    table_headers: list[str] = field(default_factory=list)
    table_rows: list[list[str]] = field(default_factory=list)
    note_type: str = ""  # "tip", "note", "warning", "important"
    quiz_question: str = ""
    quiz_options: list[str] = field(default_factory=list)


@dataclass
class Unit:
    uid: str
    title: str
    url: str
    duration_minutes: int = 0
    content_blocks: list[ContentBlock] = field(default_factory=list)
    is_knowledge_check: bool = False


@dataclass
class Module:
    uid: str
    title: str
    url: str
    duration_minutes: int = 0
    summary: str = ""
    units: list[Unit] = field(default_factory=list)


@dataclass
class LearningPath:
    uid: str
    title: str
    url: str
    duration_minutes: int = 0
    summary: str = ""
    modules: list[Module] = field(default_factory=list)


@dataclass
class Course:
    """Universal container — works for both learning paths and standalone modules."""
    title: str
    source_url: str
    learning_path: LearningPath | None = None
    modules: list[Module] = field(default_factory=list)

"""Export Course content to MP3 audio files using edge-tts (free)."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

import edge_tts

from ..models import ContentBlock, ContentBlockType, Course, Unit

log = logging.getLogger(__name__)

DEFAULT_VOICE = "en-US-AriaNeural"
MAX_CHUNK_CHARS = 4000  # edge-tts practical limit per request


class AudioExporter:
    def __init__(self, output_dir: Path, voice: str = DEFAULT_VOICE):
        self.output_dir = output_dir
        self.voice = voice
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def export(self, course: Course) -> list[Path]:
        """Convert course content to MP3 files (one per unit)."""
        return asyncio.run(self._export_async(course))

    async def _export_async(self, course: Course) -> list[Path]:
        generated: list[Path] = []

        for mod_idx, module in enumerate(course.modules, 1):
            mod_dir = self.output_dir / f"{mod_idx:02d}_{_slugify(module.title)}"
            mod_dir.mkdir(parents=True, exist_ok=True)

            for unit_idx, unit in enumerate(module.units, 1):
                text = self._flatten_to_narration(unit, module.title)
                if not text.strip():
                    log.info("Skipping empty unit: %s", unit.title)
                    continue

                filename = f"{unit_idx:02d}_{_slugify(unit.title)}.mp3"
                output_path = mod_dir / filename

                if output_path.exists():
                    log.info("Audio already exists: %s", output_path)
                    generated.append(output_path)
                    continue

                log.info("Generating audio: %s", filename)
                await self._generate_audio(text, output_path)
                generated.append(output_path)

        return generated

    def _flatten_to_narration(self, unit: Unit, module_title: str) -> str:
        """Convert structured content blocks into a narration script."""
        parts: list[str] = []
        parts.append(f"{module_title}. {unit.title}.")
        parts.append("")  # pause

        for block in unit.content_blocks:
            match block.block_type:
                case ContentBlockType.HEADING:
                    parts.append(f"{block.text}.")

                case ContentBlockType.PARAGRAPH:
                    parts.append(block.text)

                case ContentBlockType.LIST:
                    for i, item in enumerate(block.list_items):
                        if block.ordered:
                            parts.append(f"Number {i + 1}: {item}.")
                        else:
                            parts.append(f"{item}.")

                case ContentBlockType.IMAGE:
                    if block.image_alt:
                        parts.append(f"Diagram: {block.image_alt}.")

                case ContentBlockType.NOTE:
                    prefix = block.note_type.capitalize() if block.note_type else "Note"
                    parts.append(f"{prefix}: {block.text}")

                case ContentBlockType.QUIZ:
                    parts.append(f"Knowledge check question: {block.quiz_question}")
                    for i, opt in enumerate(block.quiz_options):
                        letter = chr(65 + i)
                        parts.append(f"Option {letter}: {opt}.")

                case ContentBlockType.TABLE | ContentBlockType.CODE:
                    pass  # skip — not useful in audio

            parts.append("")  # small pause between blocks

        return "\n".join(parts)

    async def _generate_audio(self, text: str, output_path: Path):
        """Generate MP3 using edge-tts, chunking if needed."""
        chunks = self._split_text(text)

        if len(chunks) == 1:
            communicate = edge_tts.Communicate(chunks[0], self.voice)
            await communicate.save(str(output_path))
        else:
            # Generate each chunk, then concatenate
            temp_files: list[Path] = []
            for i, chunk in enumerate(chunks):
                temp_path = output_path.with_suffix(f".part{i}.mp3")
                communicate = edge_tts.Communicate(chunk, self.voice)
                await communicate.save(str(temp_path))
                temp_files.append(temp_path)

            # Concatenate MP3 files (MP3 frames are independent)
            with open(output_path, "wb") as out_f:
                for temp in temp_files:
                    out_f.write(temp.read_bytes())
                    temp.unlink()

    def _split_text(self, text: str) -> list[str]:
        """Split text into chunks at paragraph boundaries."""
        if len(text) <= MAX_CHUNK_CHARS:
            return [text]

        chunks: list[str] = []
        current: list[str] = []
        current_len = 0

        for line in text.split("\n"):
            if current_len + len(line) + 1 > MAX_CHUNK_CHARS and current:
                chunks.append("\n".join(current))
                current = []
                current_len = 0
            current.append(line)
            current_len += len(line) + 1

        if current:
            chunks.append("\n".join(current))

        return chunks


def _slugify(text: str) -> str:
    """Convert text to a filesystem-safe slug."""
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text[:60].strip("-")

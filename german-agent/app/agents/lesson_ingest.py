import json
import logging
import os
import re
from pathlib import Path
from typing import TypedDict

from sqlalchemy.orm import Session

from app.database.models import (
    ExamplePattern,
    GrammarSubtopic,
    GrammarTopic,
    Lesson,
    Vocabulary,
    VocabProgress,
)
from app.services import claude_client, dictionary, pdf_processor

logger = logging.getLogger(__name__)

# Path to the ingest system prompt, relative to the project root
_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "ingest.txt"

# Valid lesson types and the filename prefix that maps to each
_TYPE_PREFIXES: list[tuple[str, str]] = [
    (r"^W\d+", "workbook"),
    (r"^E\d+", "extra"),
    (r"^T\d+", "test"),
    (r"^EL\d+", "exam_lesen"),
    (r"^ES\d+", "exam_schreiben"),
    (r"^EH\d+", "exam_hoeren"),
    (r"^ESP\d+", "exam_sprechen"),
]

# Valid word_type values accepted by the DB CHECK constraint
_VALID_WORD_TYPES = {
    "noun", "verb", "adjective", "adverb",
    "preposition", "conjunction", "other",
}


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------

class IngestSummary(TypedDict):
    lesson_id: int
    filename: str
    chapter: int | None
    topic: str | None
    lesson_type: str
    grammar_topics: int
    grammar_subtopics: int
    example_patterns: int
    vocabulary_extracted: int
    vocabulary_verified: int
    raw_text_length: int


# ---------------------------------------------------------------------------
# Filename parsing
# ---------------------------------------------------------------------------

def _parse_filename(filename: str) -> tuple[int | None, str | None, str]:
    """
    Extract chapter number, topic, and lesson type from a filename.

    Patterns:
        01_Alphabet_und_Aussprache.pdf  → chapter=1,  topic="Alphabet und Aussprache", type="textbook"
        W03_Workbook_Familie.pdf        → chapter=3,  topic="Familie",                 type="workbook"
        E01_Extra_Uebungen.pdf          → chapter=1,  topic="Uebungen",                type="extra"
        T01_Test_Kapitel_1.pdf          → chapter=1,  topic="Kapitel 1",               type="test"
        EL02_Lesen.pdf                  → chapter=2,  topic="Lesen",                   type="exam_lesen"

    Falls back to: chapter=None, topic=stem, type="textbook"
    """
    stem = Path(filename).stem  # strip .pdf

    # Determine type from prefix
    lesson_type = "textbook"
    for pattern, ltype in _TYPE_PREFIXES:
        if re.match(pattern, stem, re.IGNORECASE):
            lesson_type = ltype
            break

    # Extract chapter number (first sequence of digits in the stem)
    chapter: int | None = None
    num_match = re.search(r"\d+", stem)
    if num_match:
        chapter = int(num_match.group())

    # Extract topic: everything after the first underscore-separated token
    parts = stem.split("_", 1)
    if len(parts) == 2:
        topic = parts[1].replace("_", " ")
        # Remove a leading "Workbook ", "Extra ", "Test " prefix if present
        topic = re.sub(
            r"^(Workbook|Extra|Test|Lesen|Schreiben|Hoeren|Sprechen)\s+",
            "",
            topic,
            flags=re.IGNORECASE,
        ).strip()
    else:
        topic = stem

    return chapter, topic or None, lesson_type


# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------

def _load_system_prompt() -> str:
    """Load the ingest system prompt from disk. Cached after first read."""
    if not _PROMPT_PATH.exists():
        raise FileNotFoundError(f"Ingest prompt not found at {_PROMPT_PATH}")
    return _PROMPT_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Claude response parsing
# ---------------------------------------------------------------------------

def _parse_claude_response(raw: str) -> dict:
    """
    Parse Claude's JSON response.

    Strips markdown code fences if Claude added them despite instructions,
    then parses JSON. Returns {"grammar_topics": [], "vocabulary": []} on
    any parse failure rather than raising — we'd rather store a partial
    lesson than crash the pipeline.
    """
    text = raw.strip()

    # Strip ```json ... ``` or ``` ... ``` fences
    fence_match = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object at the top level")
        data.setdefault("grammar_topics", [])
        data.setdefault("vocabulary", [])
        return data
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("Failed to parse Claude response as JSON: %s\nRaw: %s", exc, raw[:500])
        return {"grammar_topics": [], "vocabulary": []}


# ---------------------------------------------------------------------------
# Main ingestion pipeline
# ---------------------------------------------------------------------------

async def ingest_lesson(
    pdf_bytes: bytes,
    filename: str,
    db: Session,
) -> IngestSummary:
    """
    Full PDF ingestion pipeline.

    Steps:
      A. Parse filename → chapter, topic, lesson_type
      B. Duplicate check — raise ValueError if already ingested
      C. Extract text from PDF
      D. Claude decomposition
      E. Persist: Lesson → GrammarTopics → GrammarSubtopics →
                  ExamplePatterns → Vocabulary → VocabProgress
      F. Return summary dict

    Args:
        pdf_bytes: Raw bytes of the uploaded PDF.
        filename:  Original filename (used for dedup and metadata parsing).
        db:        SQLAlchemy session.

    Returns:
        IngestSummary with counts of everything stored.

    Raises:
        ValueError: If the filename has already been ingested.
    """
    # --- A: Parse filename ---
    chapter, topic, lesson_type = _parse_filename(filename)
    logger.info(
        "Ingesting %r — chapter=%s topic=%r type=%s",
        filename, chapter, topic, lesson_type,
    )

    # --- B: Duplicate check ---
    existing = db.query(Lesson).filter(Lesson.filename == filename).first()
    if existing:
        raise ValueError(f"File {filename!r} has already been ingested (lesson_id={existing.id})")

    # --- C: Extract text ---
    raw_text = pdf_processor.extract_text(pdf_bytes)
    logger.info("Extracted %d characters from %r", len(raw_text), filename)

    if not raw_text.strip():
        raise ValueError(f"No text could be extracted from {filename!r}")

    # --- D: Claude decomposition ---
    system_prompt = _load_system_prompt()
    logger.info("Sending %r to Claude for decomposition…", filename)
    raw_response = await claude_client.complete(
        system_prompt=system_prompt,
        user_message=raw_text,
        max_tokens=8192,
    )
    parsed = _parse_claude_response(raw_response)
    grammar_topics_data: list[dict] = parsed.get("grammar_topics", [])
    vocabulary_data: list[dict] = parsed.get("vocabulary", [])

    logger.info(
        "Claude returned %d grammar topic(s) and %d vocabulary item(s)",
        len(grammar_topics_data), len(vocabulary_data),
    )

    # --- E: Persist everything in a single transaction ---
    try:
        # 1. Lesson record
        lesson = Lesson(
            filename=filename,
            chapter_number=chapter,
            topic=topic,
            type=lesson_type,
            raw_text=raw_text,
        )
        db.add(lesson)
        db.flush()  # get lesson.id without committing

        topic_count = 0
        subtopic_count = 0
        pattern_count = 0

        # 2. Grammar hierarchy
        for topic_data in grammar_topics_data:
            grammar_topic = GrammarTopic(
                lesson_id=lesson.id,
                topic_name=topic_data.get("topic_name", "Unknown"),
                explanation=topic_data.get("explanation"),
                examples=json.dumps(
                    topic_data.get("examples", []), ensure_ascii=False
                ),
            )
            db.add(grammar_topic)
            db.flush()
            topic_count += 1

            for sub_data in topic_data.get("subtopics", []):
                subtopic = GrammarSubtopic(
                    grammar_topic_id=grammar_topic.id,
                    subtopic_name=sub_data.get("subtopic_name", "Unknown"),
                    rules=sub_data.get("rules"),
                    examples=json.dumps(
                        sub_data.get("examples", []), ensure_ascii=False
                    ),
                )
                db.add(subtopic)
                db.flush()
                subtopic_count += 1

                for pattern_data in sub_data.get("patterns", []):
                    pattern = ExamplePattern(
                        subtopic_id=subtopic.id,
                        pattern_template=pattern_data.get("template", ""),
                        explanation=pattern_data.get("explanation"),
                    )
                    db.add(pattern)
                    pattern_count += 1

        # 3. Vocabulary + PONS verification + spaced repetition seed
        verified_count = 0

        for vocab_data in vocabulary_data:
            german_word = (vocab_data.get("german_word") or "").strip()
            english_translation = (vocab_data.get("english_translation") or "").strip()

            if not german_word or not english_translation:
                logger.warning("Skipping vocab entry with missing word or translation: %s", vocab_data)
                continue

            # Sanitise word_type
            word_type = (vocab_data.get("word_type") or "other").lower().strip()
            if word_type not in _VALID_WORD_TYPES:
                logger.warning("Invalid word_type %r for %r — defaulting to 'other'", word_type, german_word)
                word_type = "other"

            # PONS verification (async, per-word)
            pons_result = await dictionary.verify_word(german_word, db)

            article: str | None = None
            plural_form: str | None = None
            verified = False

            if pons_result:
                verified = True
                verified_count += 1
                article = pons_result.get("article")
                plural_form = pons_result.get("plural")
                # Trust PONS word_type over Claude if PONS returned one
                if pons_result.get("word_type") in _VALID_WORD_TYPES:
                    word_type = pons_result["word_type"]
            else:
                # For nouns, try to extract article from the german_word string itself
                # (Claude includes it: "der Apfel")
                if word_type == "noun":
                    stripped = german_word.split(None, 1)
                    if len(stripped) == 2 and stripped[0].lower() in {"der", "die", "das"}:
                        article = stripped[0].lower()

            vocab_record = Vocabulary(
                lesson_id=lesson.id,
                subtopic_id=None,  # flat list — not linked to subtopics at ingest time
                german_word=german_word,
                article=article,
                plural_form=plural_form,
                english_translation=english_translation,
                word_type=word_type,
                example_sentence=vocab_data.get("example_sentence"),
                verified_by_dictionary=verified,
            )
            db.add(vocab_record)
            db.flush()

            # Seed spaced repetition row with SM-2 defaults
            progress = VocabProgress(
                vocabulary_id=vocab_record.id,
                ease_factor=2.5,
                interval_days=1,
                next_review=None,   # scheduler sets this on first quiz
                times_correct=0,
                times_wrong=0,
                last_reviewed=None,
            )
            db.add(progress)

        db.commit()
        logger.info(
            "Ingestion complete for %r — lesson_id=%d topics=%d subtopics=%d "
            "patterns=%d vocab=%d verified=%d",
            filename, lesson.id,
            topic_count, subtopic_count, pattern_count,
            len(vocabulary_data), verified_count,
        )

    except Exception:
        db.rollback()
        logger.exception("DB error during ingestion of %r — rolled back", filename)
        raise

    return IngestSummary(
        lesson_id=lesson.id,
        filename=filename,
        chapter=chapter,
        topic=topic,
        lesson_type=lesson_type,
        grammar_topics=topic_count,
        grammar_subtopics=subtopic_count,
        example_patterns=pattern_count,
        vocabulary_extracted=len(vocabulary_data),
        vocabulary_verified=verified_count,
        raw_text_length=len(raw_text),
    )

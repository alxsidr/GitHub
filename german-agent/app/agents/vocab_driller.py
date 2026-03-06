import logging
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing_extensions import TypedDict

from sqlalchemy.orm import Session

from app.database.models import Lesson, Vocabulary, VocabProgress
from app.services import claude_client
from app.services.signals import (
    consume_signals,
    parse_detail,
    read_signals,
    write_signal,
)

logger = logging.getLogger(__name__)

_EXPLAIN_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "vocab_explain.txt"
_EXPLAIN_PROMPT: str | None = None  # loaded lazily

# Fallback distractors when DB has too few words of the same type
_FALLBACK_DISTRACTORS: dict[str, list[str]] = {
    "noun":        ["house", "dog", "book", "water", "table", "city", "child", "food"],
    "verb":        ["to go", "to eat", "to sleep", "to work", "to speak", "to read"],
    "adjective":   ["big", "small", "old", "new", "good", "bad", "fast", "slow"],
    "adverb":      ["quickly", "slowly", "always", "never", "often", "here", "there"],
    "preposition": ["in", "on", "at", "with", "from", "to", "by", "for"],
    "conjunction": ["and", "but", "or", "because", "when", "if", "although"],
    "other":       ["yes", "no", "please", "thank you", "hello", "goodbye"],
}

# How many days to pull forward a boosted word's next_review
_BOOST_DAYS = 2


# ---------------------------------------------------------------------------
# Internal data structures
# ---------------------------------------------------------------------------

class QuizQuestion(TypedDict):
    question_id: int          # vocabulary.id
    german_word: str
    word_type: str
    hint: str | None          # example_sentence as hint
    options: list[str]        # 4 choices (1 correct + 3 distractors)
    correct_answer: str
    subtopic: str | None
    chapter: int | None


class SessionStats(TypedDict):
    correct: int
    wrong: int
    noun_errors: int          # nouns answered incorrectly this session
    subtopic_correct: dict    # subtopic_name → correct count
    subtopic_wrong: dict      # subtopic_name → wrong count
    chapter_correct: dict     # chapter_number → correct count
    chapter_wrong: dict       # chapter_number → wrong count


class AnswerResult(TypedDict):
    correct: bool
    correct_answer: str
    explanation: str | None
    next_review: str          # ISO datetime
    streak: int               # times_correct for this word
    ease_factor: float
    interval_days: int
    session_stats: SessionStats
    signals_written: list[str]


def _empty_session_stats() -> SessionStats:
    return SessionStats(
        correct=0, wrong=0, noun_errors=0,
        subtopic_correct={}, subtopic_wrong={},
        chapter_correct={}, chapter_wrong={},
    )


# ---------------------------------------------------------------------------
# Signal reading: determine which vocab IDs to boost
# ---------------------------------------------------------------------------

def _get_boosted_vocab_ids(db: Session) -> tuple[set[int], list[str]]:
    """
    Read unconsumed signals addressed to vocab_driller.
    Returns (set of vocab IDs to boost, list of human-readable adjustments).
    """
    signals = read_signals(db, target_agent="vocab_driller", consumed=False)
    if not signals:
        return set(), []

    boosted_ids: set[int] = set()
    adjustments: list[str] = []
    signal_ids_to_consume: list[int] = []

    for signal in signals:
        detail = parse_detail(signal)
        signal_ids_to_consume.append(signal.id)

        if signal.signal_type == "article_errors":
            # Boost all nouns
            noun_ids = [
                row.id for row in
                db.query(Vocabulary.id)
                .filter(Vocabulary.word_type == "noun")
                .all()
            ]
            boosted_ids.update(noun_ids)
            adjustments.append(f"article_errors → boosted {len(noun_ids)} nouns")

        elif signal.signal_type == "grammar_weakness":
            subtopic_name = detail.get("subtopic") or detail.get("grammar_topic")
            if subtopic_name:
                from app.database.models import GrammarSubtopic
                subtopic = (
                    db.query(GrammarSubtopic)
                    .filter(GrammarSubtopic.subtopic_name.ilike(f"%{subtopic_name}%"))
                    .first()
                )
                if subtopic:
                    ids = [
                        row.id for row in
                        db.query(Vocabulary.id)
                        .filter(Vocabulary.subtopic_id == subtopic.id)
                        .all()
                    ]
                    boosted_ids.update(ids)
                    adjustments.append(
                        f"grammar_weakness '{subtopic_name}' → boosted {len(ids)} words"
                    )

        elif signal.signal_type == "hint_needed":
            vocab_area = detail.get("vocabulary_area") or detail.get("area")
            if vocab_area:
                ids = [
                    row.id for row in
                    db.query(Vocabulary.id)
                    .filter(Vocabulary.english_translation.ilike(f"%{vocab_area}%"))
                    .all()
                ]
                boosted_ids.update(ids)
                adjustments.append(
                    f"hint_needed '{vocab_area}' → boosted {len(ids)} words"
                )

    consume_signals(db, signal_ids_to_consume)
    return boosted_ids, adjustments


# ---------------------------------------------------------------------------
# Distractor generation
# ---------------------------------------------------------------------------

def _get_distractors(
    db: Session,
    correct_vocab: Vocabulary,
    n: int = 3,
) -> list[str]:
    """
    Pick n plausible wrong answer translations.
    Prefers same word_type from DB, falls back to hardcoded list.
    """
    word_type = correct_vocab.word_type or "other"
    correct_translation = correct_vocab.english_translation.lower().strip()

    # Query same word_type, different word, prefer same chapter
    candidates: list[str] = []

    # Same chapter first
    same_chapter = (
        db.query(Vocabulary.english_translation)
        .join(Lesson, Vocabulary.lesson_id == Lesson.id)
        .filter(
            Vocabulary.word_type == word_type,
            Vocabulary.id != correct_vocab.id,
            Lesson.chapter_number == db.query(Lesson.chapter_number)
            .filter(Lesson.id == correct_vocab.lesson_id)
            .scalar_subquery(),
        )
        .all()
    )
    candidates.extend(row.english_translation for row in same_chapter)

    # Then any same word_type
    any_type = (
        db.query(Vocabulary.english_translation)
        .filter(
            Vocabulary.word_type == word_type,
            Vocabulary.id != correct_vocab.id,
        )
        .all()
    )
    candidates.extend(row.english_translation for row in any_type)

    # Deduplicate and exclude correct answer
    seen: set[str] = {correct_translation}
    unique: list[str] = []
    for c in candidates:
        c_lower = c.lower().strip()
        if c_lower not in seen:
            seen.add(c_lower)
            unique.append(c)

    # Shuffle and take n
    random.shuffle(unique)
    distractors = unique[:n]

    # Fill remaining slots from fallback list
    if len(distractors) < n:
        fallback = [
            w for w in _FALLBACK_DISTRACTORS.get(word_type, _FALLBACK_DISTRACTORS["other"])
            if w.lower() not in seen
        ]
        random.shuffle(fallback)
        distractors.extend(fallback[: n - len(distractors)])

    return distractors[:n]


# ---------------------------------------------------------------------------
# Quiz generation
# ---------------------------------------------------------------------------

def generate_quiz(
    db: Session,
    chapter: int | None = None,
    limit: int = 10,
) -> dict:
    """
    Generate a vocabulary quiz with signal-weighted word selection.

    Returns:
        {
          "questions": [QuizQuestion, ...],
          "total_due": int,
          "signal_adjustments": [str, ...]
        }
    """
    now = datetime.utcnow()
    boosted_ids, adjustments = _get_boosted_vocab_ids(db)

    # --- Base query: all vocab (optionally filtered by chapter) ---
    base_q = db.query(Vocabulary)
    if chapter is not None:
        base_q = base_q.join(Lesson, Vocabulary.lesson_id == Lesson.id).filter(
            Lesson.chapter_number == chapter
        )

    all_vocab = base_q.all()
    vocab_by_id = {v.id: v for v in all_vocab}

    # --- Split into: due (has progress, next_review <= now), new (no progress) ---
    progress_map: dict[int, VocabProgress] = {}
    if vocab_by_id:
        progresses = (
            db.query(VocabProgress)
            .filter(VocabProgress.vocabulary_id.in_(list(vocab_by_id.keys())))
            .all()
        )
        progress_map = {p.vocabulary_id: p for p in progresses}

    due: list[Vocabulary] = []
    new_words: list[Vocabulary] = []

    for vocab in all_vocab:
        prog = progress_map.get(vocab.id)
        if prog is None:
            new_words.append(vocab)
        else:
            # Boosted words have their effective next_review pulled forward
            effective_next = prog.next_review
            if vocab.id in boosted_ids and effective_next:
                effective_next = effective_next - timedelta(days=_BOOST_DAYS)
            if effective_next is None or effective_next <= now:
                due.append(vocab)

    total_due = len(due)

    # Sort due words: boosted first, then by next_review ascending
    due.sort(key=lambda v: (
        0 if v.id in boosted_ids else 1,
        progress_map[v.id].next_review or datetime.min
        if v.id in progress_map else datetime.min,
    ))

    # Fill up to limit with new words if needed
    selected = due[:limit]
    if len(selected) < limit:
        remaining = limit - len(selected)
        random.shuffle(new_words)
        selected.extend(new_words[:remaining])

    if not selected:
        return {"questions": [], "total_due": 0, "signal_adjustments": adjustments}

    # --- Build QuizQuestion for each selected word ---
    questions: list[QuizQuestion] = []
    for vocab in selected:
        distractors = _get_distractors(db, vocab, n=3)
        options = [vocab.english_translation] + distractors
        random.shuffle(options)

        # Resolve subtopic name
        subtopic_name: str | None = None
        if vocab.subtopic_id:
            from app.database.models import GrammarSubtopic
            st = db.query(GrammarSubtopic).filter(
                GrammarSubtopic.id == vocab.subtopic_id
            ).first()
            subtopic_name = st.subtopic_name if st else None

        # Resolve chapter
        lesson = db.query(Lesson).filter(Lesson.id == vocab.lesson_id).first()
        chapter_num = lesson.chapter_number if lesson else None

        questions.append(QuizQuestion(
            question_id=vocab.id,
            german_word=vocab.german_word,
            word_type=vocab.word_type or "other",
            hint=vocab.example_sentence,
            options=options,
            correct_answer=vocab.english_translation,
            subtopic=subtopic_name,
            chapter=chapter_num,
        ))

    logger.info(
        "Quiz generated: %d questions (%d due, %d new) chapter=%s boosts=%d",
        len(questions), min(len(due), limit),
        max(0, len(selected) - min(len(due), limit)),
        chapter, len(boosted_ids),
    )

    return {
        "questions": questions,
        "total_due": total_due,
        "signal_adjustments": adjustments,
    }


# ---------------------------------------------------------------------------
# SM-2 update
# ---------------------------------------------------------------------------

def _apply_sm2(
    progress: VocabProgress,
    correct: bool,
    quality: int = 5,  # 5 = perfect, 3 = correct with difficulty, 0 = wrong
) -> VocabProgress:
    """
    Apply the SM-2 algorithm to a VocabProgress record.
    Modifies the record in-place and returns it.
    """
    now = datetime.utcnow()
    progress.last_reviewed = now

    if correct:
        progress.times_correct += 1
        if progress.times_correct == 1:
            progress.interval_days = 1
        elif progress.times_correct == 2:
            progress.interval_days = 3
        else:
            progress.interval_days = round(progress.interval_days * progress.ease_factor)
        # Update ease factor (SM-2 formula)
        progress.ease_factor = max(
            1.3,
            progress.ease_factor + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02),
        )
    else:
        progress.times_wrong += 1
        progress.interval_days = 1
        progress.ease_factor = max(1.3, progress.ease_factor - 0.2)

    progress.next_review = now + timedelta(days=progress.interval_days)
    return progress


# ---------------------------------------------------------------------------
# Explanation generation (optional Claude call)
# ---------------------------------------------------------------------------

def _load_explain_prompt() -> str | None:
    global _EXPLAIN_PROMPT
    if _EXPLAIN_PROMPT is None:
        if _EXPLAIN_PROMPT_PATH.exists():
            _EXPLAIN_PROMPT = _EXPLAIN_PROMPT_PATH.read_text(encoding="utf-8")
        else:
            _EXPLAIN_PROMPT = ""  # mark as checked, don't retry
    return _EXPLAIN_PROMPT or None


async def _generate_explanation(vocab: Vocabulary, user_answer: str) -> str | None:
    """Call Claude to generate an encouraging explanation for a wrong answer."""
    prompt_template = _load_explain_prompt()
    if not prompt_template:
        return None
    user_message = (
        f"Word: {vocab.german_word} ({vocab.word_type})\n"
        f"Correct: {vocab.english_translation}\n"
        f"Student said: {user_answer}\n"
        f"Example sentence: {vocab.example_sentence or 'N/A'}"
    )
    try:
        return await claude_client.complete(
            system_prompt=prompt_template,
            user_message=user_message,
            max_tokens=150,
        )
    except Exception as exc:
        logger.warning("Could not generate explanation: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Pattern detection → signal writing
# ---------------------------------------------------------------------------

def _detect_and_write_signals(
    db: Session,
    stats: SessionStats,
) -> list[str]:
    """
    Analyse session stats and write signals if thresholds are crossed.
    Returns list of human-readable descriptions of signals written.
    """
    written: list[str] = []

    # 1. article_errors: 3+ nouns wrong this session
    if stats["noun_errors"] >= 3:
        write_signal(
            db,
            source_agent="vocab_driller",
            signal_type="article_errors",
            detail={"noun_errors": stats["noun_errors"]},
            target_agent="corrector",
        )
        written.append(f"article_errors (noun_errors={stats['noun_errors']})")

    # 2. vocab_low_score: any subtopic with <50% and at least 2 attempts
    for subtopic, correct in stats["subtopic_correct"].items():
        wrong = stats["subtopic_wrong"].get(subtopic, 0)
        total = correct + wrong
        if total >= 2:
            score = correct / total
            if score < 0.5:
                write_signal(
                    db,
                    source_agent="vocab_driller",
                    signal_type="vocab_low_score",
                    detail={"subtopic": subtopic, "score": round(score, 2)},
                    target_agent="news_digest",
                )
                written.append(f"vocab_low_score for '{subtopic}' (score={score:.0%})")

    # 3. topic_mastered: chapter with >90% and at least 5 attempts
    for chapter, correct in stats["chapter_correct"].items():
        wrong = stats["chapter_wrong"].get(chapter, 0)
        total = correct + wrong
        if total >= 5:
            score = correct / total
            if score > 0.9:
                write_signal(
                    db,
                    source_agent="vocab_driller",
                    signal_type="topic_mastered",
                    detail={"chapter": chapter, "score": round(score, 2)},
                    target_agent="conversation",
                )
                written.append(f"topic_mastered chapter {chapter} (score={score:.0%})")

    return written


# ---------------------------------------------------------------------------
# Answer processing
# ---------------------------------------------------------------------------

async def process_answer(
    db: Session,
    question_id: int,
    user_answer: str,
    session_stats: SessionStats | None = None,
) -> AnswerResult:
    """
    Process a quiz answer, update SM-2, detect patterns, write signals.

    Args:
        db:            SQLAlchemy session.
        question_id:   vocabulary.id of the word being answered.
        user_answer:   The user's answer string.
        session_stats: Running stats dict from previous answers this session.
                       Pass None to start fresh (n8n passes this back each call).

    Returns:
        AnswerResult with updated session_stats for n8n to store.
    """
    if session_stats is None:
        session_stats = _empty_session_stats()

    # --- Fetch vocab ---
    vocab = db.query(Vocabulary).filter(Vocabulary.id == question_id).first()
    if not vocab:
        raise ValueError(f"Vocabulary id={question_id} not found")

    # --- Fetch or create progress ---
    progress = (
        db.query(VocabProgress)
        .filter(VocabProgress.vocabulary_id == question_id)
        .first()
    )
    if progress is None:
        progress = VocabProgress(
            vocabulary_id=question_id,
            ease_factor=2.5,
            interval_days=1,
            times_correct=0,
            times_wrong=0,
        )
        db.add(progress)

    # --- Evaluate answer ---
    correct = user_answer.strip().lower() == vocab.english_translation.strip().lower()

    # --- Update SM-2 ---
    quality = 5 if correct else 0
    _apply_sm2(progress, correct, quality)
    db.commit()

    # --- Update session stats ---
    if correct:
        session_stats["correct"] += 1
    else:
        session_stats["wrong"] += 1
        if vocab.word_type == "noun":
            session_stats["noun_errors"] += 1

    # Track subtopic stats
    if vocab.subtopic_id:
        from app.database.models import GrammarSubtopic
        st = db.query(GrammarSubtopic).filter(
            GrammarSubtopic.id == vocab.subtopic_id
        ).first()
        subtopic_name = st.subtopic_name if st else str(vocab.subtopic_id)
        if correct:
            session_stats["subtopic_correct"][subtopic_name] = (
                session_stats["subtopic_correct"].get(subtopic_name, 0) + 1
            )
        else:
            session_stats["subtopic_wrong"][subtopic_name] = (
                session_stats["subtopic_wrong"].get(subtopic_name, 0) + 1
            )

    # Track chapter stats
    lesson = db.query(Lesson).filter(Lesson.id == vocab.lesson_id).first()
    if lesson and lesson.chapter_number is not None:
        ch = str(lesson.chapter_number)
        if correct:
            session_stats["chapter_correct"][ch] = (
                session_stats["chapter_correct"].get(ch, 0) + 1
            )
        else:
            session_stats["chapter_wrong"][ch] = (
                session_stats["chapter_wrong"].get(ch, 0) + 1
            )

    # --- Pattern detection → signals ---
    signals_written = _detect_and_write_signals(db, session_stats)

    # --- Explanation for wrong answers ---
    explanation: str | None = None
    if not correct:
        if vocab.example_sentence:
            explanation = (
                f"Example: {vocab.example_sentence}"
            )
        else:
            explanation = await _generate_explanation(vocab, user_answer)

    logger.info(
        "Answer: vocab_id=%d correct=%s streak=%d next_review=%s",
        question_id, correct, progress.times_correct,
        progress.next_review.isoformat() if progress.next_review else "N/A",
    )

    return AnswerResult(
        correct=correct,
        correct_answer=vocab.english_translation,
        explanation=explanation,
        next_review=progress.next_review.isoformat() if progress.next_review else "",
        streak=progress.times_correct,
        ease_factor=round(progress.ease_factor, 2),
        interval_days=progress.interval_days,
        session_stats=session_stats,
        signals_written=signals_written,
    )

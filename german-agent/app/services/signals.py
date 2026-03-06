import json
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.database.models import LearningSignal

logger = logging.getLogger(__name__)

# Valid signal types — kept here as a reference; not enforced at DB level
SIGNAL_TYPES = {
    "article_errors",       # Corrector → Vocab Driller
    "grammar_weakness",     # Corrector → Conversation Bot / Exam Prep
    "vocab_low_score",      # Vocab Driller → Simple German Daily / Conversation Bot
    "hint_needed",          # Conversation Bot → Vocab Driller
    "topic_mastered",       # Vocab Driller → Conversation Bot
    "scenario_completed",   # Conversation Bot → Progress Report
    "exam_section_weak",    # Exam Prep → Vocab Driller / Conversation Bot
    "exam_section_strong",  # Exam Prep → internal
    "exam_writing_errors",  # Exam Prep → Sentence Corrector
}


def write_signal(
    db: Session,
    source_agent: str,
    signal_type: str,
    detail: dict,
    target_agent: str | None = None,
) -> LearningSignal:
    """
    Write a learning signal to the database.

    Args:
        db:           SQLAlchemy session.
        source_agent: Name of the agent writing the signal (e.g. "corrector").
        signal_type:  One of the defined signal type strings.
        detail:       Arbitrary dict with signal payload — stored as JSON.
        target_agent: Optional name of the intended consumer agent.

    Returns:
        The persisted LearningSignal instance.
    """
    if signal_type not in SIGNAL_TYPES:
        logger.warning(
            "Unknown signal_type %r from %r — writing anyway", signal_type, source_agent
        )

    signal = LearningSignal(
        source_agent=source_agent,
        signal_type=signal_type,
        target_agent=target_agent,
        detail_json=json.dumps(detail, ensure_ascii=False),
        consumed=False,
    )
    db.add(signal)
    db.commit()
    db.refresh(signal)

    logger.info(
        "Signal written: type=%r source=%r target=%r id=%d",
        signal_type, source_agent, target_agent, signal.id,
    )
    return signal


def read_signals(
    db: Session,
    target_agent: str,
    consumed: bool = False,
    limit: int = 20,
) -> list[LearningSignal]:
    """
    Read signals addressed to a specific agent.

    Args:
        db:           SQLAlchemy session.
        target_agent: Agent name to filter by (matches target_agent column).
        consumed:     If False (default), return only unconsumed signals.
        limit:        Maximum number of signals to return.

    Returns:
        List of LearningSignal instances, ordered oldest-first so agents
        process them in the order they were written.
    """
    query = db.query(LearningSignal).filter(
        LearningSignal.target_agent == target_agent,
        LearningSignal.consumed == consumed,
    )
    signals = query.order_by(LearningSignal.created_at.asc()).limit(limit).all()

    logger.debug(
        "read_signals: target=%r consumed=%s → %d result(s)",
        target_agent, consumed, len(signals),
    )
    return signals


def consume_signals(db: Session, signal_ids: list[int]) -> None:
    """
    Mark a list of signals as consumed.

    Args:
        db:         SQLAlchemy session.
        signal_ids: List of LearningSignal primary keys to mark consumed.
    """
    if not signal_ids:
        return

    updated = (
        db.query(LearningSignal)
        .filter(LearningSignal.id.in_(signal_ids))
        .all()
    )
    for signal in updated:
        signal.consumed = True

    db.commit()
    logger.info("Consumed %d signal(s): ids=%s", len(updated), signal_ids)


def get_recent_signals(
    db: Session,
    hours: int = 168,  # default: 7 days
) -> list[LearningSignal]:
    """
    Return all signals (any agent, any consumed state) from the past N hours.

    Useful for the progress report and the /api/context/recent endpoint.

    Args:
        db:    SQLAlchemy session.
        hours: Look-back window in hours. Defaults to 168 (7 days).

    Returns:
        List of LearningSignal instances, ordered newest-first.
    """
    since = datetime.utcnow() - timedelta(hours=hours)
    signals = (
        db.query(LearningSignal)
        .filter(LearningSignal.created_at >= since)
        .order_by(LearningSignal.created_at.desc())
        .all()
    )

    logger.debug(
        "get_recent_signals: last %dh → %d result(s)", hours, len(signals)
    )
    return signals


def parse_detail(signal: LearningSignal) -> dict:
    """
    Convenience helper: deserialise a signal's detail_json back to a dict.

    Returns an empty dict if the JSON is malformed (defensive).
    """
    try:
        return json.loads(signal.detail_json)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("Could not parse detail_json for signal id=%d: %s", signal.id, exc)
        return {}

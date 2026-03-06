import logging
import os
from datetime import datetime, timedelta

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

load_dotenv()

from app.database.db import get_db, init_db  # noqa: E402 — must come after load_dotenv
from app.database.models import (  # noqa: E402
    GrammarSubtopic,
    GrammarTopic,
    Lesson,
    LearningSignal,
    Vocabulary,
)
from app.agents.lesson_ingest import ingest_lesson  # noqa: E402
from app.agents.vocab_driller import generate_quiz, process_answer, SessionStats  # noqa: E402
from app.services.signals import parse_detail  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="German A1 Learning Agents")


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
def on_startup() -> None:
    logger.info("Initialising database…")
    init_db()
    logger.info("Database ready.")


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------

@app.middleware("http")
async def require_api_key(request: Request, call_next):
    """
    Enforce X-API-Key header on all endpoints except GET /api/health.
    Returns 401 if the key is absent or incorrect.
    """
    if request.url.path == "/api/health":
        return await call_next(request)

    expected_key = os.getenv("API_SECRET_KEY", "")
    provided_key = request.headers.get("X-API-Key", "")

    if not expected_key:
        # If no key is configured (local dev), allow all requests through
        logger.warning("API_SECRET_KEY is not set — auth is disabled")
        return await call_next(request)

    if provided_key != expected_key:
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or missing API key"},
        )

    return await call_next(request)


# ---------------------------------------------------------------------------
# GET /api/health
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health_check(db: Session = Depends(get_db)):
    """System status with real DB stats. No auth required."""
    try:
        lessons_count = db.query(Lesson).count()
        vocab_count = db.query(Vocabulary).count()
        signals_count = db.query(LearningSignal).filter(
            LearningSignal.consumed == False  # noqa: E712
        ).count()
        return {
            "status": "running",
            "timestamp": datetime.utcnow().isoformat(),
            "lessons_count": lessons_count,
            "vocab_count": vocab_count,
            "active_signals": signals_count,
            "agents_active": 2,  # lesson_ingest + vocab_driller live after Phase 2
            "phase": "phase_2",
        }
    except Exception as exc:
        logger.error("Health check DB query failed: %s", exc)
        return {
            "status": "degraded",
            "timestamp": datetime.utcnow().isoformat(),
            "error": str(exc),
        }


# ---------------------------------------------------------------------------
# POST /api/lesson/ingest
# ---------------------------------------------------------------------------

@app.post("/api/lesson/ingest")
async def lesson_ingest(
    file: UploadFile,
    filename: str = Form(...),
    db: Session = Depends(get_db),
):
    """
    Ingest a PDF lesson file.

    Multipart form fields:
      - file:     The PDF file upload.
      - filename: The canonical filename used for dedup and metadata parsing.

    Returns an IngestSummary dict on success.
    """
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    logger.info("Received ingest request for %r (%d bytes)", filename, len(pdf_bytes))

    try:
        summary = await ingest_lesson(pdf_bytes=pdf_bytes, filename=filename, db=db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Ingestion failed for %r", filename)
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {exc}")

    return summary


# ---------------------------------------------------------------------------
# POST /api/lesson/check
# ---------------------------------------------------------------------------

class CheckRequest(BaseModel):
    filenames: list[str]


@app.post("/api/lesson/check")
def lesson_check(body: CheckRequest, db: Session = Depends(get_db)):
    """
    Check which filenames from a list have already been ingested.

    Used by n8n to deduplicate before sending files to /ingest.

    Request body: { "filenames": ["file1.pdf", "file2.pdf", ...] }
    Response:     { "ingested": [...], "new": [...] }
    """
    if not body.filenames:
        return {"ingested": [], "new": []}

    existing = (
        db.query(Lesson.filename)
        .filter(Lesson.filename.in_(body.filenames))
        .all()
    )
    ingested_set = {row.filename for row in existing}

    return {
        "ingested": sorted(ingested_set),
        "new": sorted(f for f in body.filenames if f not in ingested_set),
    }


# ---------------------------------------------------------------------------
# GET /api/context/recent
# ---------------------------------------------------------------------------

@app.get("/api/context/recent")
def context_recent(db: Session = Depends(get_db)):
    """
    Return shared context used by all agents to personalise their responses.

    Includes grammar topics and vocabulary from lessons ingested in the last
    14 days, plus all unconsumed learning signals.
    """
    cutoff = datetime.utcnow() - timedelta(days=14)

    # Recent lessons
    recent_lessons = (
        db.query(Lesson)
        .filter(Lesson.ingested_at >= cutoff)
        .order_by(Lesson.ingested_at.desc())
        .all()
    )
    lesson_ids = [l.id for l in recent_lessons]

    # Grammar topics from recent lessons
    recent_topics = []
    if lesson_ids:
        topics = (
            db.query(GrammarTopic)
            .filter(GrammarTopic.lesson_id.in_(lesson_ids))
            .order_by(GrammarTopic.id.desc())
            .limit(50)
            .all()
        )
        recent_topics = [
            {
                "id": t.id,
                "topic_name": t.topic_name,
                "explanation": t.explanation,
                "lesson_id": t.lesson_id,
            }
            for t in topics
        ]

    # Grammar subtopics from recent lessons
    topic_ids = [t["id"] for t in recent_topics]
    recent_subtopics = []
    if topic_ids:
        subtopics = (
            db.query(GrammarSubtopic)
            .filter(GrammarSubtopic.grammar_topic_id.in_(topic_ids))
            .limit(100)
            .all()
        )
        recent_subtopics = [
            {
                "id": s.id,
                "subtopic_name": s.subtopic_name,
                "rules": s.rules,
                "grammar_topic_id": s.grammar_topic_id,
            }
            for s in subtopics
        ]

    # Active vocabulary from recent lessons
    active_vocabulary = []
    if lesson_ids:
        vocab = (
            db.query(Vocabulary)
            .filter(Vocabulary.lesson_id.in_(lesson_ids))
            .order_by(Vocabulary.id.desc())
            .limit(200)
            .all()
        )
        active_vocabulary = [
            {
                "german_word": v.german_word,
                "english_translation": v.english_translation,
                "word_type": v.word_type,
                "article": v.article,
                "example_sentence": v.example_sentence,
            }
            for v in vocab
        ]

    # Unconsumed learning signals
    active_signals_raw = (
        db.query(LearningSignal)
        .filter(LearningSignal.consumed == False)  # noqa: E712
        .order_by(LearningSignal.created_at.desc())
        .limit(50)
        .all()
    )
    active_signals = [
        {
            "id": s.id,
            "signal_type": s.signal_type,
            "source_agent": s.source_agent,
            "target_agent": s.target_agent,
            "detail": parse_detail(s),
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in active_signals_raw
    ]

    # Weak areas: aggregate signal_type counts from unconsumed signals
    weak_area_counts: dict[str, int] = {}
    for s in active_signals_raw:
        key = s.signal_type
        weak_area_counts[key] = weak_area_counts.get(key, 0) + 1
    weak_areas = [
        {"signal_type": k, "count": v}
        for k, v in sorted(weak_area_counts.items(), key=lambda x: -x[1])
    ]

    # Totals
    vocabulary_count = db.query(Vocabulary).count()
    lessons_count = db.query(Lesson).count()

    return {
        "recent_topics": recent_topics,
        "recent_subtopics": recent_subtopics,
        "active_vocabulary": active_vocabulary,
        "vocabulary_count": vocabulary_count,
        "lessons_count": lessons_count,
        "active_signals": active_signals,
        "weak_areas": weak_areas,
    }


# ---------------------------------------------------------------------------
# GET /api/vocab/quiz
# ---------------------------------------------------------------------------

@app.get("/api/vocab/quiz")
def vocab_quiz(
    chapter: int | None = None,
    limit: int = 10,
    db: Session = Depends(get_db),
):
    """
    Generate a vocabulary quiz.

    Query params:
      - chapter: (optional) restrict to a specific chapter number
      - limit:   number of questions to return (default 10)

    Returns:
      {
        "questions": [...],
        "total_due": int,
        "signal_adjustments": [str, ...]
      }
    """
    if limit < 1 or limit > 50:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 50")

    try:
        result = generate_quiz(db=db, chapter=chapter, limit=limit)
    except Exception as exc:
        logger.exception("Quiz generation failed")
        raise HTTPException(status_code=500, detail=f"Quiz generation failed: {exc}")

    return result


# ---------------------------------------------------------------------------
# POST /api/vocab/answer
# ---------------------------------------------------------------------------

class AnswerRequest(BaseModel):
    question_id: int
    user_answer: str
    session_stats: SessionStats | None = None


@app.post("/api/vocab/answer")
async def vocab_answer(body: AnswerRequest, db: Session = Depends(get_db)):
    """
    Submit an answer for a quiz question.

    Body:
      {
        "question_id": 42,
        "user_answer": "apple",
        "session_stats": { ... }   // pass back what the previous response returned
      }

    Returns an AnswerResult with the updated session_stats for n8n to store
    and pass into the next call.
    """
    try:
        result = await process_answer(
            db=db,
            question_id=body.question_id,
            user_answer=body.user_answer,
            session_stats=body.session_stats,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Answer processing failed for question_id=%d", body.question_id)
        raise HTTPException(status_code=500, detail=f"Answer processing failed: {exc}")

    return result

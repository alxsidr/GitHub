from datetime import datetime
from typing import Optional
from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, Float, ForeignKey,
    Integer, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.db import Base


# ---------------------------------------------------------------------------
# Lesson
# ---------------------------------------------------------------------------

class Lesson(Base):
    __tablename__ = "lessons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    chapter_number: Mapped[Optional[int]] = mapped_column(Integer)
    topic: Mapped[Optional[str]] = mapped_column(Text)
    type: Mapped[Optional[str]] = mapped_column(Text)
    raw_text: Mapped[Optional[str]] = mapped_column(Text)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "type IN ('textbook','workbook','extra','test','exam_lesen',"
            "'exam_schreiben','exam_hoeren','exam_sprechen')",
            name="ck_lessons_type",
        ),
    )

    # relationships
    grammar_topics: Mapped[list["GrammarTopic"]] = relationship(
        back_populates="lesson", cascade="all, delete-orphan"
    )
    vocabulary: Mapped[list["Vocabulary"]] = relationship(
        back_populates="lesson", cascade="all, delete-orphan"
    )
    exam_materials: Mapped[list["ExamMaterial"]] = relationship(
        back_populates="lesson", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Lesson id={self.id} filename={self.filename!r} type={self.type!r}>"


# ---------------------------------------------------------------------------
# GrammarTopic
# ---------------------------------------------------------------------------

class GrammarTopic(Base):
    __tablename__ = "grammar_topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lesson_id: Mapped[int] = mapped_column(Integer, ForeignKey("lessons.id"), nullable=False)
    topic_name: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[Optional[str]] = mapped_column(Text)
    examples: Mapped[Optional[str]] = mapped_column(Text)

    # relationships
    lesson: Mapped["Lesson"] = relationship(back_populates="grammar_topics")
    subtopics: Mapped[list["GrammarSubtopic"]] = relationship(
        back_populates="grammar_topic", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<GrammarTopic id={self.id} topic_name={self.topic_name!r}>"


# ---------------------------------------------------------------------------
# GrammarSubtopic
# ---------------------------------------------------------------------------

class GrammarSubtopic(Base):
    __tablename__ = "grammar_subtopics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    grammar_topic_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("grammar_topics.id"), nullable=False
    )
    subtopic_name: Mapped[str] = mapped_column(Text, nullable=False)
    rules: Mapped[Optional[str]] = mapped_column(Text)
    examples: Mapped[Optional[str]] = mapped_column(Text)

    # relationships
    grammar_topic: Mapped["GrammarTopic"] = relationship(back_populates="subtopics")
    patterns: Mapped[list["ExamplePattern"]] = relationship(
        back_populates="subtopic", cascade="all, delete-orphan"
    )
    vocabulary: Mapped[list["Vocabulary"]] = relationship(back_populates="subtopic")

    def __repr__(self) -> str:
        return f"<GrammarSubtopic id={self.id} subtopic_name={self.subtopic_name!r}>"


# ---------------------------------------------------------------------------
# ExamplePattern
# ---------------------------------------------------------------------------

class ExamplePattern(Base):
    __tablename__ = "example_patterns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subtopic_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("grammar_subtopics.id"), nullable=False
    )
    pattern_template: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[Optional[str]] = mapped_column(Text)

    # relationships
    subtopic: Mapped["GrammarSubtopic"] = relationship(back_populates="patterns")

    def __repr__(self) -> str:
        return f"<ExamplePattern id={self.id} template={self.pattern_template!r}>"


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

class Vocabulary(Base):
    __tablename__ = "vocabulary"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lesson_id: Mapped[int] = mapped_column(Integer, ForeignKey("lessons.id"), nullable=False)
    subtopic_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("grammar_subtopics.id"), nullable=True
    )
    german_word: Mapped[str] = mapped_column(Text, nullable=False)
    article: Mapped[Optional[str]] = mapped_column(Text)
    plural_form: Mapped[Optional[str]] = mapped_column(Text)
    english_translation: Mapped[str] = mapped_column(Text, nullable=False)
    word_type: Mapped[Optional[str]] = mapped_column(Text)
    example_sentence: Mapped[Optional[str]] = mapped_column(Text)
    verified_by_dictionary: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        CheckConstraint(
            "word_type IN ('noun','verb','adjective','adverb','preposition',"
            "'conjunction','other')",
            name="ck_vocabulary_word_type",
        ),
    )

    # relationships
    lesson: Mapped["Lesson"] = relationship(back_populates="vocabulary")
    subtopic: Mapped[Optional["GrammarSubtopic"]] = relationship(back_populates="vocabulary")
    progress: Mapped[Optional["VocabProgress"]] = relationship(
        back_populates="vocabulary", cascade="all, delete-orphan", uselist=False
    )

    def __repr__(self) -> str:
        return f"<Vocabulary id={self.id} german_word={self.german_word!r}>"


# ---------------------------------------------------------------------------
# VocabProgress  (spaced repetition state)
# ---------------------------------------------------------------------------

class VocabProgress(Base):
    __tablename__ = "vocab_progress"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vocabulary_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("vocabulary.id"), nullable=False
    )
    ease_factor: Mapped[float] = mapped_column(Float, default=2.5)
    interval_days: Mapped[int] = mapped_column(Integer, default=1)
    next_review: Mapped[Optional[datetime]] = mapped_column(DateTime)
    times_correct: Mapped[int] = mapped_column(Integer, default=0)
    times_wrong: Mapped[int] = mapped_column(Integer, default=0)
    last_reviewed: Mapped[Optional[datetime]] = mapped_column(DateTime)

    __table_args__ = (UniqueConstraint("vocabulary_id", name="uq_vocab_progress_vocabulary_id"),)

    # relationships
    vocabulary: Mapped["Vocabulary"] = relationship(back_populates="progress")

    def __repr__(self) -> str:
        return (
            f"<VocabProgress id={self.id} vocab_id={self.vocabulary_id} "
            f"interval={self.interval_days}d ease={self.ease_factor}>"
        )


# ---------------------------------------------------------------------------
# Mistake
# ---------------------------------------------------------------------------

class Mistake(Base):
    __tablename__ = "mistakes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_input: Mapped[str] = mapped_column(Text, nullable=False)
    correction: Mapped[str] = mapped_column(Text, nullable=False)
    error_type: Mapped[Optional[str]] = mapped_column(Text)
    grammar_topic: Mapped[Optional[str]] = mapped_column(Text)
    explanation: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "error_type IN ('article','word_order','verb_conjugation','case',"
            "'preposition','spelling','vocabulary','other')",
            name="ck_mistakes_error_type",
        ),
    )

    def __repr__(self) -> str:
        return f"<Mistake id={self.id} error_type={self.error_type!r}>"


# ---------------------------------------------------------------------------
# ConversationSession
# ---------------------------------------------------------------------------

class ConversationSession(Base):
    __tablename__ = "conversation_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scenario_type: Mapped[str] = mapped_column(Text, nullable=False)
    messages_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    hints_used: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(Text, default="active")
    score: Mapped[Optional[float]] = mapped_column(Float)

    __table_args__ = (
        CheckConstraint(
            "status IN ('active','completed','abandoned')",
            name="ck_conversation_sessions_status",
        ),
    )

    # relationships
    hint_usages: Mapped[list["HintUsage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return (
            f"<ConversationSession id={self.id} scenario={self.scenario_type!r} "
            f"status={self.status!r}>"
        )


# ---------------------------------------------------------------------------
# HintUsage
# ---------------------------------------------------------------------------

class HintUsage(Base):
    __tablename__ = "hint_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("conversation_sessions.id"), nullable=False
    )
    hint_context: Mapped[Optional[str]] = mapped_column(Text)
    options_shown: Mapped[Optional[str]] = mapped_column(Text)
    option_chosen: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # relationships
    session: Mapped["ConversationSession"] = relationship(back_populates="hint_usages")

    def __repr__(self) -> str:
        return f"<HintUsage id={self.id} session_id={self.session_id}>"


# ---------------------------------------------------------------------------
# LearningSignal  (cross-agent feedback loop)
# ---------------------------------------------------------------------------

class LearningSignal(Base):
    __tablename__ = "learning_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_agent: Mapped[str] = mapped_column(Text, nullable=False)
    target_agent: Mapped[Optional[str]] = mapped_column(Text)
    detail_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)

    def __repr__(self) -> str:
        return (
            f"<LearningSignal id={self.id} type={self.signal_type!r} "
            f"from={self.source_agent!r} to={self.target_agent!r} "
            f"consumed={self.consumed}>"
        )


# ---------------------------------------------------------------------------
# ExamMaterial
# ---------------------------------------------------------------------------

class ExamMaterial(Base):
    __tablename__ = "exam_materials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lesson_id: Mapped[int] = mapped_column(Integer, ForeignKey("lessons.id"), nullable=False)
    section: Mapped[str] = mapped_column(Text, nullable=False)
    material_type: Mapped[str] = mapped_column(Text, nullable=False)
    content_json: Mapped[str] = mapped_column(Text, nullable=False)
    source_filename: Mapped[Optional[str]] = mapped_column(Text)
    difficulty: Mapped[str] = mapped_column(Text, default="medium")

    __table_args__ = (
        CheckConstraint(
            "section IN ('lesen','schreiben','hoeren','sprechen')",
            name="ck_exam_materials_section",
        ),
        CheckConstraint(
            "material_type IN ('text_passage','question_set','writing_prompt',"
            "'audio_reference','speaking_card','answer_key')",
            name="ck_exam_materials_material_type",
        ),
        CheckConstraint(
            "difficulty IN ('easy','medium','hard')",
            name="ck_exam_materials_difficulty",
        ),
    )

    # relationships
    lesson: Mapped["Lesson"] = relationship(back_populates="exam_materials")

    def __repr__(self) -> str:
        return (
            f"<ExamMaterial id={self.id} section={self.section!r} "
            f"type={self.material_type!r}>"
        )


# ---------------------------------------------------------------------------
# ExamSession
# ---------------------------------------------------------------------------

class ExamSession(Base):
    __tablename__ = "exam_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_type: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(Text, default="active")
    section_scores: Mapped[str] = mapped_column(Text, default="{}")
    total_score: Mapped[Optional[float]] = mapped_column(Float)
    passing: Mapped[Optional[bool]] = mapped_column(Boolean)
    feedback_json: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "session_type IN ('lesen','schreiben','hoeren','sprechen','full')",
            name="ck_exam_sessions_session_type",
        ),
        CheckConstraint(
            "status IN ('active','completed','abandoned')",
            name="ck_exam_sessions_status",
        ),
    )

    # relationships
    answers: Mapped[list["ExamAnswer"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return (
            f"<ExamSession id={self.id} type={self.session_type!r} "
            f"status={self.status!r} score={self.total_score}>"
        )


# ---------------------------------------------------------------------------
# ExamAnswer
# ---------------------------------------------------------------------------

class ExamAnswer(Base):
    __tablename__ = "exam_answers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("exam_sessions.id"), nullable=False
    )
    section: Mapped[str] = mapped_column(Text, nullable=False)
    question_number: Mapped[Optional[int]] = mapped_column(Integer)
    user_answer: Mapped[Optional[str]] = mapped_column(Text)
    correct_answer: Mapped[Optional[str]] = mapped_column(Text)
    is_correct: Mapped[Optional[bool]] = mapped_column(Boolean)
    score: Mapped[Optional[float]] = mapped_column(Float)
    feedback: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # relationships
    session: Mapped["ExamSession"] = relationship(back_populates="answers")

    def __repr__(self) -> str:
        return (
            f"<ExamAnswer id={self.id} session_id={self.session_id} "
            f"section={self.section!r} correct={self.is_correct}>"
        )

# Phase 1: OneDrive Sync + Structured Decomposition + Signals DB

## Goal
Build the foundation: PDF ingestion with structured topic decomposition, dictionary verification, the learning signals service, and the shared context endpoint. After this phase, the database is populated with your lesson content in a rich hierarchy that all other agents will consume.

## What to Build

### 1. Database Setup (`app/database/`)

#### db.py
- Create SQLAlchemy engine from `DATABASE_URL` env var
- Session factory with `sessionmaker`
- `init_db()` function that creates all tables
- Call `init_db()` on app startup

#### models.py
- Define all 10 SQLAlchemy models matching the schema in CLAUDE.md
- Use `relationship()` for foreign keys (lesson → grammar_topics → grammar_subtopics → vocabulary etc.)
- Add `__repr__` for debugging

### 2. Services (`app/services/`)

#### claude_client.py
```python
# Shared Claude API wrapper
# - Load ANTHROPIC_API_KEY from env
# - Single async function: complete(system_prompt: str, user_message: str, model: str = "claude-sonnet-4-20250514") -> str
# - Handle rate limits with exponential backoff (3 retries)
# - Log token usage for cost tracking
```

#### pdf_processor.py
```python
# PDF text extraction service
# - Primary: PyMuPDF (fitz) for clean PDFs
# - Fallback: pdfplumber for complex layouts
# - OCR fallback: pytesseract + Pillow for scanned pages
# - Function: extract_text(pdf_bytes: bytes) -> str
# - Should handle mixed pages (some clean, some scanned)
```

#### dictionary.py
```python
# PONS Dictionary API wrapper with caching
# - Load PONS_API_KEY from env
# - Function: verify_word(german_word: str) -> dict | None
#   Returns: { article: str, plural: str, word_type: str, verified: True }
#   Returns None if word not found
# - Cache: before API call, check if word already verified in vocabulary table
# - Rate limiting: max 10 requests/second to PONS
# - PONS endpoint: https://api.pons.com/v1/dictionary?q={word}&l=deen
```

#### signals.py
```python
# Learning signals read/write service
# Functions:
# - write_signal(source_agent: str, signal_type: str, detail: dict, target_agent: str = None) -> Signal
# - read_signals(target_agent: str, consumed: bool = False, limit: int = 20) -> list[Signal]
# - consume_signals(signal_ids: list[int]) -> None
# - get_recent_signals(hours: int = 168) -> list[Signal]  # default 7 days
```

### 3. Lesson Ingestion Agent (`app/agents/lesson_ingest.py`)

This is the core of Phase 1. It receives a PDF, extracts text, sends it to Claude for structured decomposition, verifies vocabulary with PONS, and stores everything.

#### Ingestion Pipeline
```
PDF bytes + filename
  → pdf_processor.extract_text()
  → Parse filename for metadata (chapter number, topic, type)
  → Claude: structured decomposition (see prompt below)
  → For each vocabulary word: dictionary.verify_word()
  → Store: lesson, grammar_topics, grammar_subtopics, example_patterns, vocabulary
  → Return: summary of what was extracted
```

#### Filename Parsing
```python
# 01_Alphabet_und_Aussprache.pdf → chapter=1, topic="Alphabet und Aussprache", type="textbook"
# W03_Workbook_Familie.pdf → chapter=3, topic="Familie", type="workbook"
# E01_Extra_Uebungen.pdf → chapter=1, topic="Uebungen", type="extra"
# T01_Test_Kapitel_1.pdf → chapter=1, topic="Kapitel 1", type="test"
```

#### Claude Decomposition Prompt (`prompts/ingest.txt`)
```
You are a German A1 curriculum analyzer. Given raw text extracted from a German lesson PDF, decompose it into a structured learning hierarchy.

Return ONLY valid JSON with this structure:
{
  "grammar_topics": [
    {
      "topic_name": "Das Perfekt",
      "explanation": "Past tense formed with haben/sein + past participle",
      "subtopics": [
        {
          "subtopic_name": "Regular verbs with haben",
          "rules": "Most verbs use haben. Past participle: ge- + stem + -t",
          "examples": ["Ich habe gekauft", "Er hat gemacht"],
          "patterns": [
            {
              "template": "Ich habe ___ ge___t",
              "explanation": "Regular past participle pattern with haben"
            }
          ]
        }
      ]
    }
  ],
  "vocabulary": [
    {
      "german_word": "kaufen",
      "english_translation": "to buy",
      "word_type": "verb",
      "example_sentence": "Ich habe ein Buch gekauft."
    }
  ]
}

Rules:
- Extract ALL vocabulary words, including those in exercises and example sentences
- Identify grammar topics and decompose into sub-topics with specific rules
- For each sub-topic, provide sentence patterns that can be used for practice
- word_type must be one of: noun, verb, adjective, adverb, preposition, conjunction, other
- For nouns, include the article in the german_word field (e.g. "der Apfel")
- Keep explanations simple — this is A1 level
- If the text is from a workbook/exercise, extract vocabulary and grammar from the exercises too
```

### 4. Context Endpoint (`app/agents/` or in `main.py`)

#### GET /api/context/recent
Returns shared context that other agents use to personalize their responses.

```python
# Returns JSON:
{
  "recent_topics": [...],          # grammar topics from last 2 weeks
  "recent_subtopics": [...],       # subtopics with rules
  "active_vocabulary": [...],      # words added in last 2 weeks
  "vocabulary_count": 142,         # total words in DB
  "lessons_count": 8,              # total lessons ingested
  "active_signals": [...],         # unconsumed learning signals
  "weak_areas": [...]              # aggregated from signals
}
```

### 5. FastAPI App (`app/main.py`)

```python
# Setup:
# - Load .env with python-dotenv
# - Create FastAPI app with title "German A1 Learning Agents"
# - Init database on startup event
# - API key auth middleware (check X-API-Key header, skip for /api/health)

# Phase 1 endpoints:
# POST /api/lesson/ingest — accepts multipart file upload (PDF) + filename field
# POST /api/lesson/check — accepts JSON body: { "filenames": ["file1.pdf", ...] }
# GET  /api/context/recent — no params, returns context JSON
# GET  /api/health — returns { status, agents_active, vocab_count, lessons_count }
```

### 6. Docker Files

#### Dockerfile
As specified in CLAUDE.md. Include tesseract-ocr for OCR fallback.

#### docker-compose.yml
As specified in CLAUDE.md. Ensure volumes persist data and PDFs across restarts.

## Testing Checklist

After building, verify each step:

1. `GET /api/health` returns status with 0 lessons, 0 vocab
2. `POST /api/lesson/ingest` with a test PDF returns structured extraction summary
3. Vocabulary in DB has `verified_by_dictionary = True` for words found in PONS
4. `grammar_subtopics` table has decomposed sub-concepts, not just topic names
5. `example_patterns` table has sentence templates linked to subtopics
6. `POST /api/lesson/check` correctly identifies already-ingested files
7. `GET /api/context/recent` returns populated context after ingestion
8. Ingesting the same file twice is rejected (filename uniqueness)
9. Signal service can write and read signals (unit test)
10. OCR fallback works on a scanned PDF page

## Files to Create (in order)

1. `requirements.txt`
2. `.env.example`
3. `.gitignore`
4. `app/__init__.py`
5. `app/database/__init__.py`
6. `app/database/db.py`
7. `app/database/models.py`
8. `app/services/__init__.py`
9. `app/services/claude_client.py`
10. `app/services/pdf_processor.py`
11. `app/services/dictionary.py`
12. `app/services/signals.py`
13. `app/prompts/ingest.txt`
14. `app/agents/__init__.py`
15. `app/agents/lesson_ingest.py`
16. `app/main.py`
17. `Dockerfile`
18. `docker-compose.yml`
19. `README.md`

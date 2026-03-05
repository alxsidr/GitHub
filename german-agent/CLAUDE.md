# German A1 Learning Agents — Project Spec

## Overview

A personal German A1 learning system with 5 AI agents accessible via Telegram, orchestrated by n8n, powered by a Python FastAPI backend calling Claude API. Lesson content is auto-synced from OneDrive for Business. Agents share context through a cross-agent learning signal loop (informed by Knowledge Explorer, CSCW 2025).

## Architecture

```
OneDrive (PDFs) → n8n (orchestration) → Python FastAPI (AI brain) → Claude API + PONS Dictionary
                   ↕                        ↕
              Telegram (user)          SQLite (10 tables)
                                           ↕
                                   Learning Signals (cross-agent feedback)
```

### Infrastructure
- **Hostinger VPS** (existing) running Docker
- **n8n** (existing Docker container) — handles Telegram messaging, command routing, scheduling, OneDrive sync
- **Python API** (new Docker container) — FastAPI, agent logic, database, prompts
- **SQLite** — persistent storage in Docker volume
- n8n calls Python API via internal Docker network: `http://german-api:8000`

### Key Design Principles
1. **n8n handles all external I/O** — Telegram, OneDrive, cron scheduling. Python never talks to users directly.
2. **Python is the AI brain** — receives requests from n8n, processes with Claude, returns results.
3. **Cross-agent learning signals** — agents are not independent. They write signals when patterns are detected, and other agents read signals to adapt behavior.
4. **Structured topic decomposition** — lesson PDFs are parsed into topic → subtopic → vocabulary → pattern hierarchies, not flat word lists.
5. **Storytelling pedagogy** — daily stories are woven from current curriculum, not random content.

## Tech Stack

### Python Dependencies (requirements.txt)
```
fastapi>=0.104.0
uvicorn>=0.24.0
anthropic>=0.39.0
pymupdf>=1.23.0
pdfplumber>=0.10.0
pytesseract>=0.3.10
Pillow>=10.0.0
sqlalchemy>=2.0.0
python-dotenv>=1.0.0
python-multipart>=0.0.6
httpx>=0.25.0
```

### Environment Variables (.env)
```
ANTHROPIC_API_KEY=sk-ant-...
DATABASE_URL=sqlite:///data/german.db
API_SECRET_KEY=<random-string-for-n8n-auth>
PONS_API_KEY=<pons-dictionary-api-key>
```

## Project Structure

```
german-agent/
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app + all endpoint definitions
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── lesson_ingest.py     # Phase 1: PDF → structured decomposition + dictionary verify
│   │   ├── vocab_driller.py     # Phase 2: Spaced repetition + signal-aware quizzes
│   │   ├── corrector.py         # Phase 3: Sentence correction + mistake typing + signals
│   │   ├── review.py            # Phase 3: Weak spots quiz from cross-agent signals
│   │   ├── conversation.py      # Phase 4: Scenario roleplay + signal-driven topic selection
│   │   ├── hint.py              # Phase 4: Generate 3 hint replies for stuck users
│   │   └── news_digest.py       # Phase 5: Simplify text using storytelling + curriculum context
│   ├── services/
│   │   ├── __init__.py
│   │   ├── claude_client.py     # Anthropic API wrapper (shared by all agents)
│   │   ├── pdf_processor.py     # PDF text extraction with PyMuPDF + OCR fallback
│   │   ├── dictionary.py        # PONS API wrapper with SQLite caching
│   │   └── signals.py           # Learning signals read/write service
│   ├── database/
│   │   ├── __init__.py
│   │   ├── models.py            # All 10 SQLAlchemy models
│   │   └── db.py                # Database engine, session factory, init
│   └── prompts/
│       ├── ingest.txt           # System prompt: structured decomposition from PDF
│       ├── corrector.txt        # System prompt: correct German + classify errors
│       ├── review.txt           # System prompt: generate weak-spot exercises
│       ├── conversation.txt     # System prompt: A1 roleplay scenarios
│       ├── hint.txt             # System prompt: generate 3 A1 reply suggestions
│       └── simplifier.txt       # System prompt: storytelling with curriculum context
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

## Database Schema (10 tables)

### lessons
Tracks ingested PDFs.
```sql
id              INTEGER PRIMARY KEY
filename        TEXT UNIQUE NOT NULL
chapter_number  INTEGER
topic           TEXT
type            TEXT CHECK(type IN ('textbook', 'workbook', 'extra', 'test'))
raw_text        TEXT
ingested_at     DATETIME DEFAULT CURRENT_TIMESTAMP
```

### grammar_topics
Top-level grammar concepts extracted from lessons.
```sql
id              INTEGER PRIMARY KEY
lesson_id       INTEGER REFERENCES lessons(id)
topic_name      TEXT NOT NULL
explanation     TEXT
examples        TEXT
```

### grammar_subtopics
Decomposed sub-concepts within a grammar topic (Knowledge Explorer pattern).
```sql
id                INTEGER PRIMARY KEY
grammar_topic_id  INTEGER REFERENCES grammar_topics(id)
subtopic_name     TEXT NOT NULL
rules             TEXT
examples          TEXT
```

### example_patterns
Sentence templates linked to sub-topics for practice generation.
```sql
id              INTEGER PRIMARY KEY
subtopic_id     INTEGER REFERENCES grammar_subtopics(id)
pattern_template TEXT NOT NULL
explanation     TEXT
```

### vocabulary
Extracted and dictionary-verified words.
```sql
id                      INTEGER PRIMARY KEY
lesson_id               INTEGER REFERENCES lessons(id)
subtopic_id             INTEGER REFERENCES grammar_subtopics(id) NULL
german_word             TEXT NOT NULL
article                 TEXT
plural_form             TEXT
english_translation     TEXT NOT NULL
word_type               TEXT CHECK(word_type IN ('noun', 'verb', 'adjective', 'adverb', 'preposition', 'conjunction', 'other'))
example_sentence        TEXT
verified_by_dictionary  BOOLEAN DEFAULT FALSE
```

### vocab_progress
Spaced repetition state per word.
```sql
id              INTEGER PRIMARY KEY
vocabulary_id   INTEGER REFERENCES vocabulary(id) UNIQUE
ease_factor     REAL DEFAULT 2.5
interval_days   INTEGER DEFAULT 1
next_review     DATETIME
times_correct   INTEGER DEFAULT 0
times_wrong     INTEGER DEFAULT 0
last_reviewed   DATETIME
```

### mistakes
Logged errors from the Sentence Corrector.
```sql
id              INTEGER PRIMARY KEY
user_input      TEXT NOT NULL
correction      TEXT NOT NULL
error_type      TEXT CHECK(error_type IN ('article', 'word_order', 'verb_conjugation', 'case', 'preposition', 'spelling', 'vocabulary', 'other'))
grammar_topic   TEXT
explanation     TEXT
created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
```

### conversation_sessions
Active and completed roleplay sessions.
```sql
id              INTEGER PRIMARY KEY
scenario_type   TEXT NOT NULL
messages_json   TEXT NOT NULL DEFAULT '[]'
hints_used      INTEGER DEFAULT 0
started_at      DATETIME DEFAULT CURRENT_TIMESTAMP
completed_at    DATETIME
status          TEXT CHECK(status IN ('active', 'completed', 'abandoned')) DEFAULT 'active'
score           REAL
```

### hint_usage
Tracks individual hint button presses within conversations.
```sql
id              INTEGER PRIMARY KEY
session_id      INTEGER REFERENCES conversation_sessions(id)
hint_context    TEXT
options_shown   TEXT
option_chosen   TEXT
created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
```

### learning_signals
Cross-agent feedback loop. Agents write signals when patterns are detected; other agents read them to adapt.
```sql
id              INTEGER PRIMARY KEY
signal_type     TEXT NOT NULL
source_agent    TEXT NOT NULL
target_agent    TEXT
detail_json     TEXT NOT NULL
created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
consumed        BOOLEAN DEFAULT FALSE
```

**Signal types used:**
- `article_errors` — Corrector writes when user makes 3+ article mistakes in a session
- `grammar_weakness` — Corrector writes with specific grammar topic user struggles with
- `vocab_low_score` — Vocab Driller writes when user scores <50% on a topic area
- `hint_needed` — Conversation Bot writes when user requests hint, includes vocabulary area
- `topic_mastered` — Vocab Driller writes when user scores >90% consistently on a topic
- `scenario_completed` — Conversation Bot writes with performance details

## API Endpoints

All endpoints accept/return JSON. Authentication via `X-API-Key` header matching `API_SECRET_KEY`.

### Phase 1: OneDrive Sync + Ingestion
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/lesson/ingest` | Receive PDF file + filename, extract text, decompose into structured hierarchy via Claude, verify vocab with PONS, store all in DB |
| POST | `/api/lesson/check` | Receive list of filenames, return which are already ingested (for n8n dedup) |
| GET | `/api/context/recent` | Return recent topics, vocabulary, grammar subtopics, and active learning signals (used by other agents for context) |
| GET | `/api/health` | System status, DB stats, agent count |

### Phase 2: Vocab Driller
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/vocab/quiz?chapter=N` | Return 10 quiz questions from spaced repetition queue, weighted by learning signals |
| POST | `/api/vocab/answer` | Receive question_id + user_answer, return correct/wrong + explanation, update spaced repetition schedule, write signal if pattern detected |

### Phase 3: Sentence Corrector + Review
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/correct` | Receive German text, return correction + explanation + error_type, log to mistakes table, write learning signal if pattern detected |
| GET | `/api/review` | Generate quiz from top 3 mistake types in past 7 days + cross-agent signals, return 5 targeted exercises |
| GET | `/api/progress?days=7` | Weekly stats: words learned, common mistakes, hint usage, cross-agent signal analysis, weak areas |

### Phase 4: Conversation Bot + Hints
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/talk/start` | Start new scenario (informed by learning signals for weak areas), return scenario description + first bot message |
| POST | `/api/talk/reply` | Receive user reply + session_id, return bot response + optional feedback |
| POST | `/api/talk/hint` | Receive session_id, return 3 suggested A1-level replies based on current context, log hint usage + write signal |

### Phase 5: Simple German Daily
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/news/generate` | Receive raw news text, return A1-simplified story woven from recent lessons + vocab + signals, with vocab list and comprehension question |

## Cross-Agent Signal Flow

```
Sentence Corrector  →  Vocab Driller       : Article errors → increase article questions
Sentence Corrector  →  Conversation Bot    : Dativ struggles → Dativ-heavy scenarios
Vocab Driller       →  Simple German Daily : Low Perfekt score → Perfekt-focused story
Conversation Bot    →  Vocab Driller       : Food hint needed → resurface food vocabulary
Conversation Bot    →  Progress Report     : No hints used → highlight achievement
Vocab Driller       →  Conversation Bot    : Chapter mastered → unlock harder scenarios
```

Signals are stored in `learning_signals` table with `consumed` flag. Each agent queries unconsumed signals relevant to it before generating responses. After processing, signals are marked consumed.

## Docker Configuration

### Dockerfile
```dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y tesseract-ocr && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY .env.example .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### docker-compose.yml (add to existing n8n setup)
```yaml
services:
  german-api:
    build: .
    container_name: german-api
    restart: unless-stopped
    ports:
      - "8000:8000"
    volumes:
      - german-data:/app/data
      - german-pdfs:/app/pdfs
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - DATABASE_URL=sqlite:///data/german.db
      - API_SECRET_KEY=${API_SECRET_KEY}
      - PONS_API_KEY=${PONS_API_KEY}
    networks:
      - n8n-network

volumes:
  german-data:
  german-pdfs:

networks:
  n8n-network:
    external: true
```

## Build Order

1. **Phase 1** (Week 1): OneDrive Sync + Decomposition + Signals DB → see `specs/phase1.md`
2. **Phase 2** (Week 2): Vocab Driller (signal-aware) → see `specs/phase2.md`
3. **Phase 3** (Week 3): Sentence Corrector + Review + Signals → see `specs/phase3.md`
4. **Phase 4** (Week 4): Conversation Bot + Hints + Signal Scenarios → see `specs/phase4.md`
5. **Phase 5** (Week 5): Simple German Daily (storytelling) → see `specs/phase5.md`

## Coding Standards

- Python 3.11+, type hints on all functions
- Async endpoints where possible (FastAPI supports both)
- SQLAlchemy 2.0 style (declarative models, Session)
- All Claude API calls go through `claude_client.py` — never call anthropic directly from agents
- All signal reads/writes go through `signals.py` service
- System prompts stored as .txt files in `prompts/` directory, loaded at startup
- Error handling: return proper HTTP status codes, log errors, never crash the server
- API key auth: middleware checks `X-API-Key` header on all endpoints except `/api/health`

# Phase 4: Exam Prep (Lesen, Schreiben, Hören, Sprechen)

## Goal
Build a level-agnostic exam simulation agent driven entirely by uploaded materials. Supports all four sections of the Swiss Fide exam (and any other German exam). Materials live in a dedicated `exam/` subfolder on OneDrive. Works for A1 today, scales to B1+ by swapping materials.

## Prerequisites
- Phase 1: OneDrive sync + PDF ingestion working
- Phase 2: Vocab Driller populating learning signals
- Phase 3: Corrector writing grammar weakness signals

## OneDrive Folder Structure

```
German_A1_Lessons/
├── 01_Alphabet_und_Aussprache.pdf
├── ...
└── exam/
    ├── L01_Lesen_Mustertest_1.pdf          # Reading passages + questions
    ├── L02_Lesen_Mustertest_2.pdf
    ├── S01_Schreiben_Aufgabe_Email.pdf      # Writing prompts + criteria
    ├── S02_Schreiben_Aufgabe_Formular.pdf
    ├── H01_Hoeren_Mustertest_1.pdf          # Listening questions (pairs with audio)
    ├── H01_Hoeren_Audio_1.mp3               # Listening audio file
    ├── H02_Hoeren_Mustertest_2.pdf
    ├── H02_Hoeren_Audio_2.mp3
    ├── SP01_Sprechen_Themen_1.pdf           # Speaking cards / prompts
    ├── SP02_Sprechen_Themen_2.pdf
    └── ANS01_Antworten_Mustertest_1.pdf     # Answer keys (optional)
```

### File Naming Convention
| Prefix | Section | Example |
|---|---|---|
| `L` | Lesen (Reading) | L01_Lesen_Mustertest_1.pdf |
| `S` | Schreiben (Writing) | S01_Schreiben_Aufgabe_Email.pdf |
| `H` | Hören (Listening) | H01_Hoeren_Mustertest_1.pdf |
| `H` + `_Audio_` | Hören audio file | H01_Hoeren_Audio_1.mp3 |
| `SP` | Sprechen (Speaking) | SP01_Sprechen_Themen_1.pdf |
| `ANS` | Answer key | ANS01_Antworten_Mustertest_1.pdf |

## What to Build

### 1. Exam Material Ingestion (`app/agents/exam_prep.py`)

Extends the OneDrive Sync workflow. When files in the `exam/` subfolder are detected, they're routed to a specialized ingestion endpoint.

```python
async def ingest_exam_material(pdf_bytes: bytes, filename: str, section: str) -> IngestResult:
    """
    1. Parse filename for section type and test number
    2. Extract text from PDF via pdf_processor
    3. Send to Claude with section-specific parsing prompt
    4. Claude extracts structured content based on section:
       - Lesen: passages + questions + answer options + correct answers
       - Schreiben: writing task description + evaluation criteria + sample answer if present
       - Hören: questions + answer options (audio handled separately)
       - Sprechen: topic cards + expected discussion points + evaluation criteria
    5. Store in exam_materials table
    6. If answer key detected (ANS prefix): match to existing materials and update correct answers
    7. Return summary of what was extracted
    """
```

#### Exam Ingestion Prompt (added to `prompts/exam_ingest.txt`)
```
You are an exam material parser. Given raw text from a German language exam PDF, extract the structured content.

Section type: {section}

For LESEN (reading):
Return JSON with: passages (text + title), questions (question text + options A/B/C + correct answer + question type: multiple_choice/true_false/matching)

For SCHREIBEN (writing):
Return JSON with: task_description, format_required (email/letter/form/message), evaluation_criteria, word_count_target, sample_answer (if present in text)

For HOEREN (listening):
Return JSON with: questions (question text + options + correct answer), audio_reference (filename pattern to match), context_description

For SPRECHEN (speaking):
Return JSON with: topic_cards (topic + guiding_questions + expected_vocabulary + situation_description), format (monologue/dialogue/both), time_limit_seconds

Return ONLY valid JSON. Extract ALL questions and content from the document.
```

### 2. Exam Session Engine

#### Starting a Session
```python
async def start_exam(section: str = None) -> ExamSessionStart:
    """
    section: 'lesen', 'schreiben', 'hoeren', 'sprechen', or 'full'
    
    1. If 'full': create session with all 4 sections in order
    2. If specific section: create session for that section only
    3. Select materials not recently used (avoid repetition)
    4. Read learning signals to understand weak areas
    5. Create exam_session record
    6. Return first question/task
    """
```

#### Lesen (Reading Comprehension)
```python
async def handle_lesen(session_id: int, answer: str = None) -> LesenResponse:
    """
    Flow:
    1. Present a reading passage via Telegram
    2. Ask comprehension questions one at a time
    3. User replies with answer (A/B/C or text)
    4. Score against correct answer
    5. After all questions: return section score + feedback
    
    Scoring: points per correct answer, percentage of total
    """
```

#### Schreiben (Writing)
```python
async def handle_schreiben(session_id: int, user_text: str = None) -> SchreibenResponse:
    """
    Flow:
    1. Present writing task (e.g., "Write an email to your landlord...")
    2. User writes response in Telegram
    3. Send to Claude with evaluation prompt
    4. Claude evaluates on exam criteria:
       - Task completion (did they address all points?)
       - Vocabulary range and accuracy
       - Grammar accuracy
       - Format (correct greeting, closing, structure)
       - Word count appropriateness
    5. Return detailed score breakdown + specific feedback
    6. Write learning signal if specific weaknesses found
    """
```

#### Schreiben Evaluation Prompt (`prompts/exam_schreiben.txt`)
```
You are a certified German exam evaluator for the Swiss Fide exam.

The student was given this writing task:
{task_description}

Required format: {format_required}
Target word count: {word_count_target}

The student wrote:
"{user_text}"

Evaluate against these criteria and respond ONLY with valid JSON:
{
  "scores": {
    "task_completion": { "score": 0-5, "max": 5, "feedback": "..." },
    "vocabulary": { "score": 0-5, "max": 5, "feedback": "..." },
    "grammar": { "score": 0-5, "max": 5, "feedback": "..." },
    "format_structure": { "score": 0-5, "max": 5, "feedback": "..." }
  },
  "total_score": 0-20,
  "passing": true/false,
  "pass_threshold": 12,
  "word_count": 0,
  "overall_feedback": "Encouraging summary of performance",
  "specific_errors": [
    { "original": "...", "corrected": "...", "error_type": "...", "explanation": "..." }
  ],
  "strengths": ["..."],
  "areas_to_improve": ["..."],
  "model_answer": "A brief example of a good response for this task"
}

Be encouraging but honest. Score as a real examiner would.
```

#### Hören (Listening)
```python
async def handle_hoeren(session_id: int, answer: str = None) -> HoerenResponse:
    """
    Flow:
    1. Send audio file to user via Telegram (n8n handles file delivery)
    2. Present questions one at a time
    3. User answers
    4. Score against correct answers
    5. Option to replay audio (n8n resends the file)
    
    Note: Audio files are stored in OneDrive and delivered by n8n.
    The Python API only manages questions and scoring.
    Audio reference in exam_materials links to the OneDrive filename.
    """
```

#### Sprechen (Speaking)
```python
async def handle_sprechen(session_id: int, user_response: str = None) -> SprechenResponse:
    """
    Flow:
    1. Present a speaking card/topic (in Telegram as text)
    2. User responds in text (voice in future Phase 7)
    3. Claude evaluates as an exam assessor:
       - Relevance to topic
       - Vocabulary range
       - Grammar accuracy
       - Fluency indicators (sentence complexity, coherence)
       - Interaction ability (for dialogue tasks)
    4. Return evaluation with score + feedback
    
    Fide Sprechen format:
    - Part 1: Self-introduction
    - Part 2: Monologue on a topic (with preparation time note)
    - Part 3: Dialogue with examiner (Claude plays examiner role)
    """
```

#### Sprechen Evaluation Prompt (`prompts/exam_sprechen.txt`)
```
You are a certified Swiss Fide exam speaking assessor.

The student was given this speaking task:
{topic_card}

Task format: {format}
The student responded:
"{user_response}"

If this is a dialogue task, continue the conversation naturally as the examiner would, then evaluate after 4-6 exchanges.

For evaluation, respond ONLY with valid JSON:
{
  "is_dialogue_continuation": false,
  "examiner_reply": null,
  "evaluation": {
    "scores": {
      "relevance": { "score": 0-5, "max": 5, "feedback": "..." },
      "vocabulary": { "score": 0-5, "max": 5, "feedback": "..." },
      "grammar": { "score": 0-5, "max": 5, "feedback": "..." },
      "fluency_coherence": { "score": 0-5, "max": 5, "feedback": "..." },
      "interaction": { "score": 0-5, "max": 5, "feedback": "..." }
    },
    "total_score": 0-25,
    "passing": true/false,
    "pass_threshold": 15,
    "overall_feedback": "...",
    "suggested_phrases": ["Useful phrases the student could have used"],
    "model_response": "Example of a strong response"
  }
}

If is_dialogue_continuation is true, set evaluation to null and provide examiner_reply instead.
```

### 3. Full Mock Exam
```python
async def run_full_exam(session_id: int) -> FullExamProgress:
    """
    Runs all 4 sections in sequence:
    1. Lesen (reading) — ~20 minutes
    2. Hören (listening) — ~15 minutes
    3. Schreiben (writing) — ~30 minutes
    4. Sprechen (speaking) — ~15 minutes
    
    Between sections: show section score + brief break message
    After all sections: comprehensive score report + pass/fail
    
    Fide passing: typically 60% overall, no section below 40%
    """
```

### 4. Endpoints in `app/main.py`

#### POST /api/exam/ingest
```python
# Body: multipart file upload + section field
# Returns: { "materials_extracted": 5, "section": "lesen", "questions": 12, "source": "L01_..." }
```

#### POST /api/exam/start
```python
# Body: { "section": "lesen" }  or { "section": "full" }
# Returns: {
#   "session_id": 8,
#   "section": "lesen",
#   "total_questions": 12,
#   "first_content": { "type": "passage", "text": "...", "question": "...", "options": [...] },
#   "estimated_minutes": 20
# }
```

#### POST /api/exam/answer
```python
# Body: { "session_id": 8, "answer": "B" }
# Returns: {
#   "correct": true,
#   "correct_answer": "B",
#   "explanation": "...",
#   "score_so_far": { "correct": 5, "total": 6 },
#   "next_question": { ... } or null if section complete
# }
```

#### POST /api/exam/evaluate-writing
```python
# Body: { "session_id": 8, "text": "Sehr geehrte Frau Müller, ich schreibe Ihnen..." }
# Returns: { full schreiben evaluation JSON as shown in prompt above }
```

#### POST /api/exam/evaluate-speaking
```python
# Body: { "session_id": 8, "response": "Ich heisse Hans und ich komme aus..." }
# Returns: { full sprechen evaluation JSON or dialogue continuation }
```

#### GET /api/exam/score
```python
# Returns: {
#   "total_sessions": 12,
#   "by_section": {
#     "lesen": { "attempts": 4, "avg_score": 78, "trend": "improving", "last_pass": true },
#     "schreiben": { "attempts": 3, "avg_score": 65, "trend": "stable", "last_pass": true },
#     "hoeren": { "attempts": 3, "avg_score": 55, "trend": "declining", "last_pass": false },
#     "sprechen": { "attempts": 2, "avg_score": 70, "trend": "improving", "last_pass": true }
#   },
#   "full_mock_exams": { "attempts": 1, "last_score": 67, "passed": true },
#   "weakest_section": "hoeren",
#   "recommendation": "Focus on listening practice. Try /exam hoeren"
# }
```

#### GET /api/exam/next
```python
# Query: session_id
# Returns next question/task in the active session
```

### 5. Telegram Commands

```
/exam              — Show exam menu (pick a section or full mock)
/exam lesen        — Start reading comprehension practice
/exam schreiben    — Start writing task
/exam hoeren       — Start listening exercise
/exam sprechen     — Start speaking simulation
/exam full         — Full mock exam (all 4 sections)
/exam score        — View practice history and scores
```

### 6. n8n Workflows

#### Exam Router (part of Command Router)
```
Telegram Trigger → detect /exam command
  → If /exam lesen: HTTP POST /api/exam/start {section: "lesen"}
  → If /exam schreiben: HTTP POST /api/exam/start {section: "schreiben"}
  → If /exam hoeren: 
      HTTP POST /api/exam/start {section: "hoeren"}
      + Download audio from OneDrive → Send audio file via Telegram
  → If /exam sprechen: HTTP POST /api/exam/start {section: "sprechen"}
  → If /exam full: HTTP POST /api/exam/start {section: "full"}
  → If /exam score: HTTP GET /api/exam/score
  → Send formatted response to Telegram
```

#### Exam Material Sync (extend OneDrive Sync workflow)
```
Existing OneDrive Sync workflow:
  → After listing files, check for exam/ subfolder
  → For files in exam/:
      Parse prefix to determine section
      If .pdf: HTTP POST /api/exam/ingest with section type
      If .mp3: Store reference for Hören delivery
```

### 7. Signal Integration

#### Signals this agent READS:
| Signal | Source | Effect |
|---|---|---|
| `grammar_weakness` | Corrector | Highlight in writing evaluation, focus feedback on that area |
| `article_errors` | Corrector | Extra attention to articles in reading/writing scoring |
| `vocab_low_score` | Vocab Driller | Flag if exam question uses struggling vocabulary |
| `topic_mastered` | Vocab Driller | Can present harder exam materials |

#### Signals this agent WRITES:
| Signal | Trigger | Detail |
|---|---|---|
| `exam_section_weak` | Score below 60% in a section | `{ "section": "hoeren", "score": 45, "weak_areas": ["numbers", "directions"] }` |
| `exam_section_strong` | Score above 80% consistently | `{ "section": "lesen", "score": 85 }` |
| `exam_writing_errors` | Writing evaluation finds patterns | `{ "error_types": ["format", "verb_conjugation"], "examples": [...] }` |

## Testing Checklist

1. Exam PDF ingestion extracts questions correctly for all 4 sections
2. `/exam lesen` presents passage + questions, scores answers
3. `/exam schreiben` presents task, evaluates writing with detailed breakdown
4. `/exam hoeren` sends audio + questions (audio via n8n/Telegram)
5. `/exam sprechen` presents topic, evaluates response (and handles dialogue)
6. `/exam full` runs all 4 sections in sequence with final score
7. `/exam score` shows history with trends and recommendations
8. Answer keys (ANS prefix) correctly update materials with correct answers
9. Signals written: weak section triggers vocab/corrector adaptation
10. Previously used materials are avoided (no repetition in consecutive sessions)
11. Works with different exam formats (not hardcoded to one test structure)

## Files to Create/Modify

1. **Create** `app/agents/exam_prep.py`
2. **Create** `app/prompts/exam_ingest.txt`
3. **Create** `app/prompts/exam_lesen.txt`
4. **Create** `app/prompts/exam_schreiben.txt`
5. **Create** `app/prompts/exam_sprechen.txt`
6. **Modify** `app/database/models.py` — add exam_materials, exam_sessions, exam_answers tables
7. **Modify** `app/main.py` — add all /api/exam/* endpoints
8. **Modify** Phase 1 `lesson_ingest.py` — handle exam/ subfolder file prefix routing

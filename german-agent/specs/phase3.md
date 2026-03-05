# Phase 3: Sentence Corrector + Weak Spots Review + Signals

## Goal
Build a German sentence correction agent that explains errors, classifies them by type, tracks patterns over time, and writes learning signals. Add a /review command for targeted weak-spot practice and a /progress endpoint for weekly stats.

## Prerequisites
- Phase 1 complete: lessons, grammar topics, subtopics in DB
- Phase 2 complete: vocab_progress populated, signal writing tested

## What to Build

### 1. Sentence Corrector (`app/agents/corrector.py`)

```python
async def correct_sentence(user_input: str) -> CorrectionResult:
    """
    1. Load recent context: what grammar topics/subtopics user has studied (from DB)
    2. Load recent signals: what areas other agents flagged
    3. Build Claude prompt with context + user input
    4. Parse Claude response into structured correction
    5. Log mistake to mistakes table with error_type classification
    6. Pattern detection: if 3+ errors of same type in past 24h → write signal
    7. Return correction with explanation
    """
```

#### Correction Prompt (`prompts/corrector.txt`)
```
You are a patient German A1 tutor correcting a student's German writing.

The student has studied these grammar topics: {topics_list}
The student has studied these specific sub-topics: {subtopics_list}
Recent weak areas (from learning signals): {weak_areas}

Student wrote: "{user_input}"

Respond ONLY with valid JSON:
{
  "is_correct": false,
  "corrected_text": "Die corrected German sentence",
  "errors": [
    {
      "original": "the wrong part",
      "corrected": "the correct version",
      "error_type": "article",
      "explanation": "Brief, encouraging explanation in English",
      "grammar_topic": "Articles with Akkusativ"
    }
  ],
  "overall_feedback": "One sentence of encouragement + what to focus on",
  "difficulty_assessment": "appropriate" | "too_easy" | "too_hard"
}

Rules:
- error_type must be one of: article, word_order, verb_conjugation, case, preposition, spelling, vocabulary, other
- Be encouraging, not critical
- Reference grammar topics the student has studied when relevant
- If the sentence is correct, set is_correct to true and give positive feedback
- Keep explanations at A1 level — simple English, short sentences
- difficulty_assessment: compare sentence complexity to student's current level
```

#### Pattern Detection Logic
```python
# After logging a mistake, check for patterns:
def detect_patterns(session_mistakes: list[Mistake]) -> list[Signal]:
    signals = []
    
    # Count error types in last 24 hours
    recent = get_mistakes(hours=24)
    type_counts = Counter(m.error_type for m in recent)
    
    for error_type, count in type_counts.items():
        if count >= 3:
            signals.append(write_signal(
                source_agent="corrector",
                signal_type="grammar_weakness" if error_type != "article" else "article_errors",
                detail={"error_type": error_type, "count": count, "examples": [...last 3 mistakes...]},
                target_agent="vocab_driller"  # or None for all agents
            ))
    
    return signals
```

### 2. Weak Spots Review (`app/agents/review.py`)

```python
async def generate_review_quiz() -> ReviewQuiz:
    """
    1. Query mistakes table: last 7 days, group by error_type
    2. Read learning signals from all agents for additional context
    3. Identify top 3 weakest areas
    4. Ask Claude to generate 5 exercise sentences targeting those areas
    5. Each exercise has an intentional error for the user to find and correct
    6. Return quiz
    """
```

#### Review Prompt (`prompts/review.txt`)
```
You are a German A1 tutor creating a targeted practice quiz.

The student's weakest areas this week:
{weak_areas_with_examples}

Cross-agent insights:
{signal_summary}

Generate exactly 5 exercise sentences in German, each containing ONE intentional error related to the student's weak areas. Distribute across the weak areas.

Respond ONLY with valid JSON:
{
  "exercises": [
    {
      "sentence_with_error": "Ich gehe in die Bibliothek.",
      "error_type": "case",
      "corrected_sentence": "Ich gehe in die Bibliothek.",
      "hint": "Think about which case 'in' takes for location vs direction",
      "explanation": "Brief explanation of the grammar rule"
    }
  ],
  "weak_areas_summary": "This week you struggled most with: ...",
  "encouragement": "One sentence of positive reinforcement"
}
```

### 3. Progress Report

```python
async def generate_progress(days: int = 7) -> ProgressReport:
    """
    Aggregates data from all tables:
    
    1. Vocabulary stats:
       - Total words in DB
       - Words reviewed this period
       - New words learned (times_correct >= 3)
       - Words struggling with (times_wrong > times_correct)
    
    2. Mistake analysis:
       - Total corrections requested
       - Error type breakdown (pie chart data)
       - Most common error type
       - Improvement trend (this week vs last week)
    
    3. Signal analysis:
       - Signals written by each agent
       - Cross-agent patterns
       - Areas multiple agents flag as weak
    
    4. Hint usage (if Phase 4 is active):
       - Total hints used
       - Scenarios completed
       - Average hints per scenario (trend)
    
    5. Recommendations:
       - Top 3 areas to focus on
       - Suggested /review topics
       - Positive achievements to celebrate
    """
```

### 4. Endpoints in `app/main.py`

#### POST /api/correct
```python
# Body: { "text": "Ich habe ein Katze" }
# Returns: {
#   "is_correct": false,
#   "corrected_text": "Ich habe eine Katze",
#   "errors": [...],
#   "overall_feedback": "...",
#   "signals_written": ["article_errors: 3 article mistakes in 24h"]
# }
```

#### GET /api/review
```python
# No params (auto-selects weak areas)
# Returns: {
#   "exercises": [...],
#   "weak_areas_summary": "...",
#   "encouragement": "..."
# }
```

#### GET /api/progress
```python
# Query params: days (optional int, default 7)
# Returns: {
#   "period": "2025-02-24 to 2025-03-03",
#   "vocabulary": { total, reviewed, learned, struggling },
#   "corrections": { total, by_error_type, most_common, trend },
#   "signals": { by_agent, cross_agent_patterns },
#   "hints": { total, scenarios_completed, avg_per_scenario },
#   "recommendations": [...],
#   "achievements": [...]
# }
```

### 5. Signal Integration

#### Signals this agent READS:
| Signal Type | Source | How It's Used |
|---|---|---|
| `vocab_low_score` | Vocab Driller | Include in /review as additional weak area |
| `hint_needed` | Conversation Bot | Include vocabulary area in correction context |
| `topic_mastered` | Vocab Driller | Mention in progress report as achievement |

#### Signals this agent WRITES:
| Signal Type | Trigger | Detail |
|---|---|---|
| `article_errors` | 3+ article errors in 24h | `{ "count": 4, "examples": [...] }` |
| `grammar_weakness` | 3+ errors of any other type in 24h | `{ "error_type": "word_order", "count": 3, "examples": [...] }` |

## Testing Checklist

1. `/api/correct` returns structured correction with error types
2. Corrections reference grammar topics the user has studied
3. Mistakes are logged to DB with correct error_type
4. Pattern detection: make 3 article errors → signal written
5. `/api/review` generates exercises targeting actual weak spots
6. Review exercises include errors from cross-agent signals too
7. `/api/progress` returns comprehensive stats
8. Progress shows improvement trends (this week vs last)
9. Empty state handled: no mistakes yet → helpful message
10. Claude prompt includes context from decomposed subtopics

## Files to Create/Modify

1. **Create** `app/agents/corrector.py`
2. **Create** `app/agents/review.py`
3. **Create** `app/prompts/corrector.txt`
4. **Create** `app/prompts/review.txt`
5. **Modify** `app/main.py` — add `/api/correct`, `/api/review`, `/api/progress`

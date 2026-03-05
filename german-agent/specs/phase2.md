# Phase 2: Vocab Driller (Signal-Aware)

## Goal
Build a spaced repetition vocabulary quiz system that draws from ingested lesson content and adapts based on learning signals from other agents. Delivers quizzes both on-demand (/vocab) and as a scheduled morning push.

## Prerequisites
- Phase 1 complete: lessons ingested, vocabulary in DB, signals service working

## What to Build

### 1. Spaced Repetition Engine (`app/agents/vocab_driller.py`)

#### Algorithm: Modified SM-2
Based on the SuperMemo SM-2 algorithm, adapted for signal weighting.

```python
# Core SM-2 logic:
# After each review:
#   if correct:
#     times_correct += 1
#     if times_correct == 1: interval = 1
#     elif times_correct == 2: interval = 3
#     else: interval = round(interval * ease_factor)
#     ease_factor = max(1.3, ease_factor + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
#   if wrong:
#     times_wrong += 1
#     interval = 1
#     ease_factor = max(1.3, ease_factor - 0.2)
#   next_review = now + timedelta(days=interval)

# Signal weighting:
# Before selecting quiz words, check learning_signals for:
#   - "article_errors" from Corrector → boost priority of nouns (article practice)
#   - "grammar_weakness" with specific topic → boost words linked to that subtopic
#   - "hint_needed" with vocab area → boost words in that area
# Boosting = reduce next_review by 2 days (force earlier review)
```

#### Quiz Generation
```python
async def generate_quiz(chapter: int = None, limit: int = 10) -> list[QuizQuestion]:
    """
    1. Query vocab_progress for words where next_review <= now
    2. Apply signal weighting (boost flagged areas)
    3. If not enough due words, add new words from recent lessons
    4. If chapter filter, restrict to that chapter
    5. Shuffle and return `limit` questions
    
    Each QuizQuestion:
    {
      "question_id": 42,
      "german_word": "der Apfel",
      "word_type": "noun",
      "hint": "A common fruit",  # from example_sentence
      "options": ["apple", "orange", "bread", "milk"],  # 1 correct + 3 distractors
      "correct_answer": "apple",
      "subtopic": "Food vocabulary",
      "chapter": 3
    }
    """
```

#### Distractor Generation
```python
# Generate 3 wrong answer options:
# 1. Pick from same word_type in vocabulary table (other nouns if noun, other verbs if verb)
# 2. Prefer words from the same chapter or topic area (plausible distractors)
# 3. If not enough words in DB, use a small fallback list of common A1 words
# 4. Never include the correct answer in distractors
```

#### Answer Processing
```python
async def process_answer(question_id: int, user_answer: str) -> AnswerResult:
    """
    1. Look up correct answer from vocabulary table
    2. Compare (case-insensitive, strip whitespace)
    3. Update vocab_progress using SM-2 algorithm
    4. If word was wrong: return explanation with example sentence
    5. Pattern detection:
       - If user got 3+ nouns wrong in this session → write signal "article_errors"
       - If user scored <50% on a subtopic → write signal "vocab_low_score"
       - If user got >90% on a chapter → write signal "topic_mastered"
    6. Return result with correct answer, explanation, updated stats
    """
```

### 2. New Endpoints in `app/main.py`

#### GET /api/vocab/quiz
```python
# Query params: chapter (optional int), limit (optional int, default 10)
# Returns: { "questions": [...], "total_due": int, "signal_adjustments": [...] }
# signal_adjustments shows what signals influenced this quiz (for transparency)
```

#### POST /api/vocab/answer
```python
# Body: { "question_id": int, "user_answer": str }
# Returns: {
#   "correct": bool,
#   "correct_answer": str,
#   "explanation": str,       # example sentence or grammar note
#   "next_review": datetime,  # when this word will appear again
#   "streak": int,            # consecutive correct answers for this word
#   "session_stats": {        # running stats for current quiz session
#     "correct": 7,
#     "wrong": 3,
#     "signals_written": ["vocab_low_score for Perfekt verbs"]
#   }
# }
```

### 3. Initialize Vocab Progress

When Phase 1 ingests vocabulary, it does NOT create vocab_progress records. The Vocab Driller creates them on first encounter:

```python
# When generating a quiz, for any vocabulary word without a vocab_progress record:
# Create one with: ease_factor=2.5, interval_days=0, next_review=now, times_correct=0, times_wrong=0
# This means all new words are immediately eligible for quizzing
```

### 4. Prompt File (`prompts/` — not needed for Phase 2)
The Vocab Driller does NOT use Claude for quiz generation — it's pure algorithmic logic with database queries. Claude is only used in Phase 2 if we need to generate an explanation for a wrong answer that isn't already in the DB.

Optional: for wrong answers, call Claude to generate a brief, memorable explanation:
```
# prompts/vocab_explain.txt (optional, only used when explanation needed)
You are a friendly German A1 tutor. The student just got a vocabulary word wrong.

Word: {german_word} ({word_type})
Correct: {english_translation}
Student said: {user_answer}
Example sentence: {example_sentence}

Give a brief (1-2 sentences), encouraging explanation to help them remember this word. Use simple language.
```

## Signal Integration Details

### Signals this agent READS (from other agents):
| Signal Type | Source | How It Affects Quiz |
|---|---|---|
| `article_errors` | Corrector | Boost priority of nouns — more article practice |
| `grammar_weakness` | Corrector | Boost words linked to the weak grammar subtopic |
| `hint_needed` | Conversation Bot | Boost words in the vocabulary area where hint was needed |

### Signals this agent WRITES:
| Signal Type | Trigger | Detail |
|---|---|---|
| `vocab_low_score` | User scores <50% on a subtopic in a session | `{ "subtopic": "Perfekt verbs", "score": 0.4 }` |
| `topic_mastered` | User scores >90% on a chapter consistently (3+ sessions) | `{ "chapter": 3, "score": 0.95 }` |

## Testing Checklist

1. `/api/vocab/quiz` returns 10 questions with valid options
2. Questions are drawn from ingested lesson vocabulary
3. New words (no progress record) appear in quizzes
4. Correct answers update interval and ease_factor properly
5. Wrong answers reset interval to 1 day
6. Signal weighting: inject an "article_errors" signal, verify nouns get priority
7. Distractor options are plausible (same word type, no duplicates)
8. Pattern detection: get 3 nouns wrong → signal written
9. Chapter filter works: `/api/vocab/quiz?chapter=2` only returns Ch2 words
10. Session stats track running correct/wrong count

## Files to Create/Modify

1. **Create** `app/agents/vocab_driller.py`
2. **Create** `app/prompts/vocab_explain.txt` (optional)
3. **Modify** `app/main.py` — add `/api/vocab/quiz` and `/api/vocab/answer` endpoints

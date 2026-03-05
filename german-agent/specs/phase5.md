# Phase 5: Simple German Daily (Storytelling Pedagogy)

## Goal
Build a daily A1-level German story generator that weaves content from the user's current curriculum, reinforces weak vocabulary areas through narrative, and includes a comprehension question. Informed by the Knowledge Explorer storytelling pedagogy approach.

## Prerequisites
- Phase 1: Lesson content, vocabulary, grammar subtopics in DB
- Phase 2+3: Learning signals from Vocab Driller and Corrector
- Phase 4 (optional): Hint signals from Conversation Bot add more context

## What to Build

### 1. Story Generator (`app/agents/news_digest.py`)

#### Story Generation Pipeline
```python
async def generate_daily_story(raw_news_text: str = None) -> DailyStory:
    """
    1. Get curriculum context via /api/context/recent:
       - Recent lesson topics (last 2 weeks)
       - Active vocabulary (words added recently)
       - Weak areas from learning signals
       - Previously used story words (avoid repetition)
    
    2. If raw_news_text provided (from n8n RSS):
       - Use it as thematic inspiration
       - But rewrite completely at A1 level with curriculum vocab
    
    3. If no raw text (fallback):
       - Generate original A1 story based on curriculum themes
       - Topics from recent lessons make natural story themes
    
    4. Send context + optional news to Claude for story generation
    
    5. Parse response: story, vocab list, comprehension question
    
    6. Record which vocabulary words were used (for future dedup)
    
    7. Return structured daily story
    """
```

#### Story Context Builder
```python
async def build_story_context() -> StoryContext:
    """
    Queries the database to build rich context for Claude:
    
    1. Recent topics: grammar_topics from lessons ingested in last 14 days
       Example: ["Perfekt Tense", "Food and Shopping", "Family members"]
    
    2. Active vocabulary: words from vocabulary table, last 14 days, limit 50
       Prioritize: words with low vocab_progress scores (needs practice)
       Example: ["kaufen", "der Apfel", "gegangen", "die Milch"]
    
    3. Weak areas from signals:
       - article_errors → emphasize articles in story
       - grammar_weakness → use that grammar structure in story
       - vocab_low_score → include those vocabulary words
       - hint_needed → include vocabulary from that area
    
    4. Previously used words: words used in stories in last 7 days
       → Tell Claude to avoid these, use synonyms/new words instead
    
    5. Mastered areas from signals:
       → Can be referenced casually but shouldn't be the focus
    """
```

#### Story Prompt (`prompts/simplifier.txt`)
```
You are a German A1 story writer creating a short, engaging daily story for a beginner learner.

CURRICULUM CONTEXT (use this to shape the story):
Recent lesson topics: {recent_topics}
Active vocabulary to practice: {active_vocab}
Weak areas to reinforce: {weak_areas}
Words to AVOID (used in recent stories): {recently_used_words}
Mastered topics (can reference casually): {mastered_topics}

RAW NEWS INSPIRATION (optional, rewrite completely):
{raw_news_text_or_none}

Write a short German story (6-10 sentences) that:
1. Uses vocabulary from the student's active word list naturally in the narrative
2. Reinforces weak areas through context (e.g., if articles are weak, use many nouns with clear article context)
3. Tells an engaging mini-story with a character, a simple plot, and a small resolution
4. Uses ONLY A1 grammar: present tense, Perfekt (if studied), modal verbs, basic word order
5. Is about everyday life: shopping, cooking, visiting friends, going to work, etc.

Respond ONLY with valid JSON:
{
  "title": "Hans geht einkaufen",
  "story": "The full German story text, 6-10 sentences.",
  "vocabulary_list": [
    {
      "german": "einkaufen",
      "english": "to go shopping",
      "used_in_sentence": "Hans geht heute einkaufen."
    }
  ],
  "comprehension_question": {
    "question": "Was hat Hans im Supermarkt gekauft?",
    "answer": "Er hat Äpfel und Milch gekauft.",
    "question_english": "What did Hans buy at the supermarket?"
  },
  "grammar_practiced": ["Perfekt with haben", "Akkusativ articles"],
  "curriculum_alignment": "This story practices Perfekt tense and food vocabulary from Chapters 3-4"
}

Rules:
- Story must be self-contained and complete (beginning, middle, end)
- Use characters with German names (Hans, Anna, Petra, Max, etc.)
- Vocabulary list should include 5-8 key words, prioritizing active/weak vocab
- Comprehension question should be answerable from the story text
- Do NOT include English in the story itself — only in vocabulary list and question translation
- Make it interesting! A1 doesn't mean boring.
```

### 2. Story History Tracking

Add a lightweight tracking mechanism to avoid repetitive stories:

```python
# In the database, add tracking to learning_signals or a simple cache:
# After each story, write a signal:
write_signal(
    source_agent="news_digest",
    signal_type="story_generated",
    detail={
        "title": "Hans geht einkaufen",
        "vocabulary_used": ["einkaufen", "Apfel", "Milch", ...],
        "grammar_practiced": ["Perfekt", "Akkusativ"],
        "date": "2025-03-05"
    }
)

# When building context, query last 7 days of "story_generated" signals
# to get recently_used_words list
```

### 3. Endpoint in `app/main.py`

#### POST /api/news/generate
```python
# Body: { "raw_text": "Optional raw news text from RSS feed" }
# raw_text can be null/empty — story will be generated from curriculum alone
#
# Returns: {
#   "title": "Hans geht einkaufen",
#   "story": "Hans geht heute einkaufen. Er braucht Äpfel und Milch...",
#   "vocabulary_list": [
#     { "german": "einkaufen", "english": "to go shopping", "used_in_sentence": "..." }
#   ],
#   "comprehension_question": {
#     "question": "Was hat Hans im Supermarkt gekauft?",
#     "answer": "Er hat Äpfel und Milch gekauft.",
#     "question_english": "What did Hans buy at the supermarket?"
#   },
#   "grammar_practiced": ["Perfekt with haben", "Akkusativ articles"],
#   "curriculum_alignment": "Practices Chapters 3-4 vocabulary and Perfekt tense",
#   "signal_driven_elements": ["Included extra articles because of article_errors signal"]
# }
```

### 4. n8n Workflow Design (for reference)

The n8n workflow that calls this endpoint:
```
Cron Trigger (12:00 PM daily)
  → RSS Read node (Deutsche Welle Easy German or similar)
  → Extract first article text
  → HTTP Request: POST /api/news/generate with raw_text
  → Telegram: Send formatted message to user
      Title: 📖 {title}
      Story: {story}
      ---
      📝 Vocabulary:
      {formatted vocab list}
      ---
      ❓ {comprehension_question}
      (tap to reveal answer)
```

### 5. Signal Integration

#### Signals this agent READS:
| Signal | Source | Effect |
|---|---|---|
| `article_errors` | Corrector | Story emphasizes articles in context |
| `grammar_weakness` | Corrector | Story uses the weak grammar structure |
| `vocab_low_score` | Vocab Driller | Story includes struggling vocabulary |
| `hint_needed` | Conversation Bot | Story includes vocabulary from hint area |
| `topic_mastered` | Vocab Driller | Can reference casually, not focus |
| `story_generated` | Self (previous days) | Avoid repeating same words/themes |

#### Signals this agent WRITES:
| Signal | Trigger | Detail |
|---|---|---|
| `story_generated` | After each story | `{ vocabulary_used, grammar_practiced, date }` |

### 6. Fallback Strategy

If RSS feed fails or returns no content:
```python
# Fallback topics based on curriculum:
FALLBACK_THEMES = [
    "A day at the market",
    "Visiting a friend",
    "A trip to the park",
    "Cooking dinner",
    "A morning routine",
    "At the train station",
    "A birthday party",
    "The weather this week",
]
# Pick one not used recently, generate story purely from curriculum context
```

## Testing Checklist

1. `/api/news/generate` with raw_text returns curriculum-aligned story
2. `/api/news/generate` without raw_text generates story from curriculum alone
3. Story uses vocabulary from recent lessons (check against active_vocab)
4. Story avoids words used in previous 7 days' stories
5. Weak areas from signals are reflected in story content
6. Comprehension question is answerable from the story
7. Vocabulary list includes 5-8 words with translations
8. Story is 6-10 sentences, A1 grammar only
9. "story_generated" signal is written after each story
10. Multiple consecutive days produce different stories with varied themes

## Files to Create/Modify

1. **Create** `app/agents/news_digest.py`
2. **Create** `app/prompts/simplifier.txt`
3. **Modify** `app/main.py` — add `/api/news/generate`

## Full System Integration Test (End of Phase 5)

After Phase 5, run the complete cross-agent loop:

1. Upload a new lesson PDF via OneDrive → verify ingestion + decomposition
2. Run `/vocab` quiz → miss some words intentionally → verify signals written
3. Run `/correct` with intentional article errors → verify signals written
4. Run `/talk` → verify scenario selected based on signals from steps 2+3
5. Use hints during conversation → verify hint signals written
6. Run `/news` → verify story incorporates weak areas from all previous signals
7. Run `/progress` → verify comprehensive report across all agents
8. Next day's `/vocab` quiz should reflect signals from all interactions

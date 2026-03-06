# Phase 5: Conversation Scenario Bot + Hint Button

## Goal
Build an interactive A1 roleplay agent that simulates real-life scenarios, adapts scenario selection based on learning signals (targeting weak vocabulary areas), and includes a hint system for when the user gets stuck.

## Prerequisites
- Phase 1: Vocabulary and grammar context available
- Phase 2: Vocab Driller writing signals about weak/mastered areas
- Phase 3: Corrector writing signals about grammar weaknesses

## What to Build

### 1. Conversation Engine (`app/agents/conversation.py`)

#### Scenario Selection (Signal-Driven)
```python
A1_SCENARIOS = [
    {"type": "restaurant", "vocab_area": "food", "grammar": ["modal_verbs", "akkusativ"]},
    {"type": "shopping", "vocab_area": "clothing", "grammar": ["numbers", "akkusativ"]},
    {"type": "doctor", "vocab_area": "body_health", "grammar": ["modal_verbs", "dativ"]},
    {"type": "directions", "vocab_area": "places", "grammar": ["prepositions", "imperativ"]},
    {"type": "introduction", "vocab_area": "personal", "grammar": ["present_tense", "sein_haben"]},
    {"type": "apartment", "vocab_area": "housing", "grammar": ["dativ", "adjectives"]},
    {"type": "phone_call", "vocab_area": "time_dates", "grammar": ["future", "modal_verbs"]},
    {"type": "travel", "vocab_area": "transport", "grammar": ["perfekt", "prepositions"]},
    {"type": "birthday", "vocab_area": "celebrations", "grammar": ["dativ", "perfekt"]},
    {"type": "supermarket", "vocab_area": "food", "grammar": ["numbers", "akkusativ"]},
]

async def select_scenario() -> dict:
    """
    1. Read learning signals for weak areas
    2. Score each scenario by relevance to weak areas:
       - +3 if scenario vocab_area matches a "hint_needed" signal
       - +2 if scenario grammar matches a "grammar_weakness" signal
       - +1 if scenario vocab_area matches a "vocab_low_score" signal
       - -2 if scenario type was used in last 3 sessions (avoid repetition)
    3. Select highest-scored scenario (with randomness to avoid predictability)
    4. Return scenario config
    """
```

#### Starting a Conversation
```python
async def start_conversation(scenario_type: str = None) -> ConversationStart:
    """
    1. Select scenario (auto or specified)
    2. Create conversation_session in DB (status='active')
    3. Build Claude prompt with scenario + user's vocab level context
    4. Get first bot message from Claude
    5. Return: scenario description (in English), first message (in German), session_id
    """
```

#### Continuing a Conversation
```python
async def reply(session_id: int, user_message: str) -> ConversationReply:
    """
    1. Load session from DB (messages_json)
    2. Append user message to history
    3. Check if conversation should end (5-8 exchanges is typical for A1)
    4. Send full history + user message to Claude
    5. Parse Claude response
    6. If conversation complete: generate scoring, write signals, set status='completed'
    7. Save updated messages_json
    8. Return: bot_reply, is_complete, feedback (if complete), score (if complete)
    """
```

#### Conversation Prompt (`prompts/conversation.txt`)
```
You are playing a character in a German A1 conversation practice scenario.

Scenario: {scenario_description}
Your role: {bot_role}
Student's German level: A1 (beginner, ~{vocab_count} words learned)
Student's recent vocabulary: {recent_vocab_sample}
Student's weak areas: {weak_areas_from_signals}

Conversation history:
{messages_history}

Student just said: "{user_message}"

Respond ONLY with valid JSON:
{
  "reply": "Your German response (A1 level, short sentences)",
  "is_natural_end": false,
  "internal_notes": "Brief note on student's language quality",
  "vocabulary_used": ["word1", "word2"],
  "errors_noticed": ["brief note on any errors in student's message"]
}

Rules:
- Stay in character. Respond naturally as your role would.
- Use only A1-level German: simple present tense, basic vocabulary, short sentences.
- If the student makes a mistake, do NOT correct them mid-conversation — just continue naturally.
- Gently guide the conversation toward using vocabulary from their weak areas.
- After 5-8 exchanges, set is_natural_end to true and wrap up the conversation naturally.
- errors_noticed is for the scoring at the end — do not show to student during conversation.
```

#### End-of-Conversation Scoring
```python
async def score_conversation(session: ConversationSession) -> Score:
    """
    Analyze the full conversation:
    1. Count errors noticed by Claude across all messages
    2. Count hints used (from hint_usage table)
    3. Evaluate vocabulary range (unique words used)
    4. Calculate score: (correct exchanges / total exchanges) * 100, minus 5 per hint used
    5. Write signals:
       - If hints > 2 for a vocab area → "hint_needed" signal
       - If no hints used → "scenario_completed" with high score
    6. Return: score (0-100), feedback text, areas to improve, achievements
    """
```

### 2. Hint Generator (`app/agents/hint.py`)

```python
async def generate_hints(session_id: int) -> HintResult:
    """
    1. Load conversation session and full message history
    2. Get the last bot message (what the user needs to respond to)
    3. Get user's vocabulary level from DB
    4. Ask Claude for 3 suggested replies at A1 level
    5. Log hint usage to hint_usage table
    6. Increment session.hints_used
    7. Return 3 options
    """
```

#### Hint Prompt (`prompts/hint.txt`)
```
You are helping a German A1 student who is stuck in a conversation.

Scenario: {scenario_description}
The other person just said: "{last_bot_message}"

Conversation so far:
{messages_history}

Student's known vocabulary (recent): {recent_vocab}

Generate exactly 3 possible replies the student could use, from simplest to most advanced (but all A1 level).

Respond ONLY with valid JSON:
{
  "options": [
    {
      "text": "Einen Kaffee, bitte.",
      "difficulty": "easy",
      "vocabulary_practiced": ["Kaffee", "bitte"]
    },
    {
      "text": "Ich möchte einen Kaffee mit Milch, bitte.",
      "difficulty": "medium",
      "vocabulary_practiced": ["möchte", "Kaffee", "Milch"]
    },
    {
      "text": "Kann ich bitte einen Kaffee und ein Stück Kuchen haben?",
      "difficulty": "challenging",
      "vocabulary_practiced": ["kann", "Kaffee", "Stück", "Kuchen"]
    }
  ],
  "learning_note": "All three options practice ordering with 'bitte'. The harder options add modal verbs."
}
```

### 3. Endpoints in `app/main.py`

#### POST /api/talk/start
```python
# Body: { "scenario_type": "restaurant" }  (optional — auto-selects if omitted)
# Returns: {
#   "session_id": 15,
#   "scenario": "You are at a café in Berlin. The waiter approaches your table.",
#   "bot_role": "Waiter at a café",
#   "first_message": "Guten Tag! Was möchten Sie bestellen?",
#   "hint_available": true,
#   "signal_driven": "Selected because you need food vocabulary practice"
# }
```

#### POST /api/talk/reply
```python
# Body: { "session_id": 15, "message": "Ich möchte ein Kaffee" }
# Returns: {
#   "reply": "Sehr gut! Mit Milch und Zucker?",
#   "is_complete": false,
#   "hint_available": true,
#   "exchange_number": 3,
#   "exchanges_remaining": "2-5 more"
# }
# 
# When is_complete=true, additionally returns:
# {
#   "score": 75,
#   "feedback": "Good conversation! You used 12 unique words...",
#   "errors_found": ["ein Kaffee → einen Kaffee (Akkusativ)"],
#   "hints_used": 1,
#   "achievements": ["First restaurant scenario completed!"],
#   "signals_written": [...]
# }
```

#### POST /api/talk/hint
```python
# Body: { "session_id": 15 }
# Returns: {
#   "options": [
#     { "id": "a", "text": "Einen Kaffee, bitte.", "difficulty": "easy" },
#     { "id": "b", "text": "Ich möchte einen Kaffee mit Milch.", "difficulty": "medium" },
#     { "id": "c", "text": "Kann ich einen Kaffee und Kuchen haben?", "difficulty": "challenging" }
#   ],
#   "learning_note": "...",
#   "hints_used_so_far": 2
# }
```

### 4. Signal Integration

#### Signals this agent READS:
| Signal | Source | Effect |
|---|---|---|
| `article_errors` | Corrector | Select scenarios that practice articles heavily |
| `grammar_weakness` | Corrector | Match scenarios to weak grammar areas |
| `vocab_low_score` | Vocab Driller | Select scenarios using struggling vocabulary |
| `topic_mastered` | Vocab Driller | Unlock more complex scenario variants |

#### Signals this agent WRITES:
| Signal | Trigger | Detail |
|---|---|---|
| `hint_needed` | User requests hint | `{ "vocab_area": "food", "scenario": "restaurant", "context": "ordering" }` |
| `scenario_completed` | Conversation ends | `{ "scenario": "restaurant", "score": 75, "hints": 1, "exchanges": 6 }` |

## Testing Checklist

1. `/api/talk/start` returns scenario with first German message
2. Auto-selection favors scenarios matching weak areas from signals
3. Conversation maintains state across multiple `/api/talk/reply` calls
4. Conversation ends naturally after 5-8 exchanges
5. End-of-conversation scoring includes error analysis and hint count
6. `/api/talk/hint` returns 3 graduated difficulty options
7. Hint usage is logged and counted in session
8. Signals written: hint_needed after hints, scenario_completed after end
9. Previous scenario types are avoided (no immediate repetition)
10. Claude stays in character and uses A1-level German

## Files to Create/Modify

1. **Create** `app/agents/conversation.py`
2. **Create** `app/agents/hint.py`
3. **Create** `app/prompts/conversation.txt`
4. **Create** `app/prompts/hint.txt`
5. **Modify** `app/main.py` — add `/api/talk/start`, `/api/talk/reply`, `/api/talk/hint`

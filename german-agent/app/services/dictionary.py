import asyncio
import logging
import os

import httpx
from sqlalchemy.orm import Session

from app.database.models import Vocabulary

logger = logging.getLogger(__name__)

PONS_API_URL = "https://api.pons.com/v1/dictionary"
# PONS free tier allows 10 requests/second; enforce a small delay between calls
_RATE_LIMIT_DELAY = 0.1  # seconds


def _parse_pons_response(data: list) -> dict | None:
    """
    Parse the PONS API JSON response and extract article, plural, word_type.

    PONS returns a list of result objects. Each has a "hits" list, each hit
    has a "roms" list with "wordclass" and "arabs" entries. We take the
    first usable hit.

    Returns a dict on success, None if the response is empty or unparseable.
    """
    try:
        if not data:
            return None

        for result in data:
            for hit in result.get("hits", []):
                roms = hit.get("roms", [])
                for rom in roms:
                    wordclass = rom.get("wordclass", "").lower()
                    headword_full = rom.get("headword_full", "")

                    # Map PONS wordclass to our schema word_type
                    word_type = _map_wordclass(wordclass)

                    # Extract article from headword_full for nouns
                    # PONS formats nouns as "der/die/das Wort" in headword_full
                    article = None
                    plural = None
                    if word_type == "noun":
                        article, plural = _extract_noun_details(headword_full, rom)

                    return {
                        "article": article,
                        "plural": plural,
                        "word_type": word_type,
                        "verified": True,
                    }
    except (KeyError, IndexError, TypeError) as exc:
        logger.warning("Failed to parse PONS response: %s", exc)

    return None


def _map_wordclass(wordclass: str) -> str:
    """Map PONS wordclass strings to our schema's word_type values."""
    mapping = {
        "noun": "noun",
        "substantiv": "noun",
        "verb": "verb",
        "verbum": "verb",
        "adjective": "adjective",
        "adjektiv": "adjective",
        "adverb": "adverb",
        "preposition": "preposition",
        "präposition": "preposition",
        "conjunction": "conjunction",
        "konjunktion": "conjunction",
    }
    for key, value in mapping.items():
        if key in wordclass:
            return value
    return "other"


def _extract_noun_details(headword_full: str, rom: dict) -> tuple[str | None, str | None]:
    """
    Extract article and plural form for nouns from PONS data.

    headword_full may contain HTML like "<span class='genus'>m</span>".
    We do a simple text search for common patterns.
    """
    article = None
    plural = None

    # Try to get grammatical gender from headword_full text
    hw_lower = headword_full.lower()
    if ">m<" in hw_lower or "genus'>m" in hw_lower:
        article = "der"
    elif ">f<" in hw_lower or "genus'>f" in hw_lower:
        article = "die"
    elif ">n<" in hw_lower or "genus'>n" in hw_lower:
        article = "das"

    # Try to find plural in the inflections list if available
    inflections = rom.get("inflections", [])
    for inflection in inflections:
        if "pl" in inflection.get("number", "").lower():
            plural = inflection.get("form")
            break

    return article, plural


async def verify_word(
    german_word: str,
    db: Session,
) -> dict | None:
    """
    Verify a German word using the PONS dictionary API, with DB cache.

    Lookup order:
      1. Check the vocabulary table: if the word exists with
         verified_by_dictionary=True, return cached data immediately.
      2. If PONS_API_KEY is not configured, skip API call and return None.
      3. Call PONS API, parse result, return dict or None.

    Args:
        german_word: The German word to look up (may include article, e.g. "der Apfel").
        db:          SQLAlchemy session (used for cache lookup only — callers write to DB).

    Returns:
        {"article": str|None, "plural": str|None, "word_type": str, "verified": True}
        or None if word not found / API unavailable.
    """
    # Strip leading article for the API query (PONS expects bare words)
    query_word = _strip_article(german_word)

    # --- Cache check ---
    cached = (
        db.query(Vocabulary)
        .filter(
            Vocabulary.german_word == german_word,
            Vocabulary.verified_by_dictionary.is_(True),
        )
        .first()
    )
    if cached:
        logger.debug("Cache hit for %r", german_word)
        return {
            "article": cached.article,
            "plural": cached.plural_form,
            "word_type": cached.word_type,
            "verified": True,
        }

    # --- API key check ---
    api_key = os.getenv("PONS_API_KEY")
    if not api_key:
        logger.debug("PONS_API_KEY not set — skipping dictionary verification for %r", german_word)
        return None

    # --- Rate limiting ---
    await asyncio.sleep(_RATE_LIMIT_DELAY)

    # --- PONS API call ---
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                PONS_API_URL,
                params={"q": query_word, "l": "deen"},
                headers={"X-Secret": api_key},
            )

        if response.status_code == 204:
            # No content — word not found in PONS
            logger.debug("PONS: word %r not found (204)", query_word)
            return None

        if response.status_code != 200:
            logger.warning(
                "PONS API returned %d for %r", response.status_code, query_word
            )
            return None

        data = response.json()
        result = _parse_pons_response(data)

        if result:
            logger.debug("PONS verified %r → %s", german_word, result)
        else:
            logger.debug("PONS returned data for %r but parsing found nothing useful", german_word)

        return result

    except httpx.TimeoutException:
        logger.warning("PONS API timed out for %r", query_word)
        return None
    except httpx.RequestError as exc:
        logger.warning("PONS API request failed for %r: %s", query_word, exc)
        return None
    except Exception as exc:
        logger.error("Unexpected error verifying %r: %s", query_word, exc)
        return None


def _strip_article(word: str) -> str:
    """
    Remove leading German articles from a word string.

    e.g. "der Apfel" → "Apfel", "die Katze" → "Katze", "kaufen" → "kaufen"
    """
    articles = {"der", "die", "das", "ein", "eine", "einen", "einem", "einer", "eines"}
    parts = word.strip().split(None, 1)
    if len(parts) == 2 and parts[0].lower() in articles:
        return parts[1]
    return word.strip()

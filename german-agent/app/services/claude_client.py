import asyncio
import logging
import os

import anthropic

logger = logging.getLogger(__name__)

# Module-level client — created once, reused across all calls
_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY environment variable is not set")
        _client = anthropic.AsyncAnthropic(api_key=api_key)
    return _client


async def complete(
    system_prompt: str,
    user_message: str,
    model: str = "claude-sonnet-4-5",
    max_tokens: int = 4096,
) -> str:
    """
    Send a single-turn completion request to Claude.

    Retries up to 3 times with exponential backoff on rate-limit or
    overload errors (HTTP 429 / 529). Raises RuntimeError if all retries
    are exhausted.

    Args:
        system_prompt: The system prompt (loaded from a .txt file by callers).
        user_message:  The user-turn content.
        model:         Claude model ID.
        max_tokens:    Maximum tokens in the response.

    Returns:
        The assistant's text response as a plain string.
    """
    client = _get_client()
    max_retries = 3

    for attempt in range(max_retries):
        try:
            response = await client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )

            # Log token usage for cost tracking
            usage = response.usage
            logger.info(
                "Claude usage — model=%s input_tokens=%d output_tokens=%d",
                model,
                usage.input_tokens,
                usage.output_tokens,
            )

            return response.content[0].text

        except anthropic.RateLimitError as exc:
            wait = 2 ** attempt  # 1s, 2s, 4s
            logger.warning(
                "Rate limit hit (attempt %d/%d). Retrying in %ds. %s",
                attempt + 1, max_retries, wait, exc,
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(wait)
            else:
                raise RuntimeError(
                    f"Claude rate limit exceeded after {max_retries} attempts"
                ) from exc

        except anthropic.APIStatusError as exc:
            # Retry on 529 (overloaded); re-raise immediately on other status errors
            if exc.status_code == 529:
                wait = 2 ** attempt
                logger.warning(
                    "Claude overloaded (attempt %d/%d). Retrying in %ds.",
                    attempt + 1, max_retries, wait,
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(wait)
                else:
                    raise RuntimeError(
                        f"Claude API overloaded after {max_retries} attempts"
                    ) from exc
            else:
                logger.error("Claude API error (status %d): %s", exc.status_code, exc)
                raise

        except anthropic.APIConnectionError as exc:
            logger.error("Claude connection error: %s", exc)
            raise RuntimeError("Failed to connect to Claude API") from exc

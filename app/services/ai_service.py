import json
import logging
import re
import httpx

from app.config import get_settings

logger = logging.getLogger("plexai.ai")
settings = get_settings()

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"


def _normalize_title(title: str) -> str:
    """Normalize a media title for fuzzy comparison.

    Converts to lowercase, strips trailing year in parentheses (e.g. '(2013)'),
    and removes leading/trailing whitespace.  This makes 'Rush (2013)' and 'Rush'
    compare as equal, which is critical for the AI ID-mismatch fallback logic.
    """
    title = title.lower()
    title = re.sub(r"\s*\(\d{4}\)\s*$", "", title)
    return title.strip()


class AIService:
    """Service for generating recommendations using OpenRouter LLM."""

    def __init__(self, api_key: str = None, model: str = None):
        self.api_key = api_key or settings.openrouter_api_key
        raw_model = model or settings.openrouter_model
        
        # OpenRouter-specific mapping: automatically upgrade deprecated 1.5 strings to the active 2.5 model
        deprecated_models = [
            "google/gemini-1.5-pro",
            "google/gemini-1.5-pro-latest",
            "google/gemini-pro-1.5"
        ]
        
        if raw_model in deprecated_models:
            self.model = "google/gemini-2.5-pro"
            logger.info(f"Automatically upgraded deprecated model '{raw_model}' to '{self.model}'")
        else:
            self.model = raw_model

    async def generate_recommendations(
        self,
        watch_history: list[dict],
        available_content: list[dict],
        past_recommendations: list[dict] = None,
        movies_count: int = None,
        shows_count: int = None,
    ) -> dict:
        """Generate personalized recommendations using AI.

        Args:
            watch_history: List of items the user has watched
            available_content: List of items available in the library (unwatched)
            past_recommendations: Previously recommended items (for feedback loop)
            movies_count: Number of movie recommendations
            shows_count: Number of show recommendations

        Returns:
            Dict with 'movies' and 'shows' lists of recommended items
        """
        m_count = movies_count or settings.playlist_size
        s_count = shows_count or settings.playlist_size

        # Split available content by type
        available_movies = [c for c in available_content if c["type"] == "movie"]
        available_shows = [c for c in available_content if c["type"] == "show"]

        # Build the prompt
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(
            watch_history, available_movies, available_shows,
            past_recommendations, m_count, s_count
        )

        logger.info(
            f"Sending recommendation request to OpenRouter ({self.model}). "
            f"History: {len(watch_history)} items, "
            f"Available: {len(available_movies)} movies + {len(available_shows)} shows"
        )
        logger.info(
            f"Prompt size: system={len(system_prompt)} chars, "
            f"user={len(user_prompt)} chars, "
            f"total={len(system_prompt) + len(user_prompt)} chars"
        )

        # Log full prompts for debugging
        logger.info(f"--- SYSTEM PROMPT ---\n{system_prompt}\n---------------------")
        logger.info(f"--- USER PROMPT ---\n{user_prompt}\n---------------------")

        request_body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.25,
            "max_tokens": 8192,
            "response_format": {"type": "json_object"},
        }

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                OPENROUTER_API_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://plexai-curator.local",
                    "X-Title": "PlexAI Personal Curator",
                },
                json=request_body,
            )
            
            if resp.status_code != 200:
                raise Exception(f"OpenRouter Gateway Error Body: {resp.text}")
                
            resp.raise_for_status()
            data = resp.json()

        # Log token usage
        usage = data.get("usage", {})
        logger.info(
            f"AI token usage: "
            f"prompt={usage.get('prompt_tokens', '?')}, "
            f"completion={usage.get('completion_tokens', '?')}, "
            f"total={usage.get('total_tokens', '?')}"
        )

        # Safely parse the response to prevent NoneType crashes
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        content = message.get("content")

        if content is None:
            finish_reason = choice.get("finish_reason", "unknown")
            logger.error(f"❌ AI returned None for content. Finish reason: {finish_reason}")
            logger.error(f"Raw choice data: {choice}")
            # Assign an empty JSON schema to prevent the len() crash and allow graceful failure
            content = "{}"

        # Safe length calculation
        logger.info(f"AI raw response ({len(content)} chars):\n{content}")

        result = self._parse_response(content, available_content)
        movies = [r for r in result if r.get("type") == "movie"][:m_count]
        shows = [r for r in result if r.get("type") == "show"][:s_count]
        logger.info(f"AI generated {len(movies)} movie + {len(shows)} show recommendations")
        return {"movies": movies, "shows": shows}

    def _build_system_prompt(self) -> str:
        return """You are an expert Content Curator for a personal Plex media server.
Your task is to deeply analyze a user's watch history, identify their specific tone/vibe preferences, and recommend unwatched content strictly from the provided library.

CRITICAL RULES:
1. STRICT LIBRARY MATCH: You MUST ONLY recommend items from the AVAILABLE POOLS below. DO NOT hallucinate or invent titles/IDs.
2. NEVER RECOMMEND WATCHED: NEVER recommend any item that appears in the USER WATCH HISTORY section. Those items have already been watched. Recommending a watched item is a critical failure.
3. NO DUPLICATES: Each 'rating_key' must appear exactly once. Do not recommend the same item twice.
4. TYPE ACCURACY: Do not mix pools. Items from the 'AVAILABLE MOVIES' pool must be marked as "movie". Items from the 'AVAILABLE TV SHOWS' pool must be marked as "show".
5. EXACT TITLE: Copy the title field EXACTLY as it appears in the pool. Do NOT translate, transliterate, or modify the title in any way. The title must be character-for-character identical to the pool entry.
6. TONE CONSISTENCY: Pay close attention to the maturity, violence, and dramatic tone of the history. If they watch gritty crime/thrillers, DO NOT recommend lighthearted romantic comedies or kids' shows. This rule is ABSOLUTE and overrides all other rules.
7. MIX STRATEGY: 70% of recommendations should perfectly match the history vibe (same genre, same tone). 30% may explore adjacent genres (e.g., Sci-Fi or Horror instead of just Crime), BUT they MUST strictly maintain the same MATURITY LEVEL and DARK TONE as the watch history. Absolutely NO kids/family/animated movies if the history is mature/dark content.
8. CREATORS/ACTORS: If the user watches content from a specific author/director (e.g., Harlan Coben, Marvel), prioritize available content from the same universe/creator if applicable.
9. SAFETY BYPASS: Keep the generated text in the 'vibe_analysis' and 'reason' fields highly clinical, analytical, and family-friendly. Do NOT use overly graphic, violent, or explicit words when describing action/thriller recommendations, as it will trigger upstream API safety filters.
10. JSON FORMAT: You MUST respond with a valid JSON object matching the exact schema below.

EXPECTED JSON SCHEMA:
{
  "vibe_analysis": "Write 2-3 sentences in English analyzing the user's taste, preferred genres, and emotional tone based on their history. Doing this helps you make better choices.",
  "recommendations": [
    {
      "rating_key": "12345",
      "title": "EXACT title copied character-for-character from the pool — NO translation",
      "type": "movie",
      "reason": "Brief reason in English explaining why the PLOT fits the vibe_analysis"
    }
  ]
}"""

    def _build_user_prompt(
        self,
        watch_history: list[dict],
        available_movies: list[dict],
        available_shows: list[dict],
        past_recommendations: list[dict] = None,
        movies_count: int = 15,
        shows_count: int = 15,
    ) -> str:
        history_str = self._format_items_for_prompt(watch_history)
        movies_str = self._format_items_for_prompt(available_movies)
        shows_str = self._format_items_for_prompt(available_shows)

        return f"""
TASK: Select exactly {movies_count} MOVIES and {shows_count} TV SHOWS that perfectly match the user's taste.
IMPORTANT: You may ONLY select from the AVAILABLE POOLS. Do NOT pick anything from the WATCH HISTORY.

{'='*60}
SECTION 1 — USER WATCH HISTORY (READ ONLY — do NOT recommend these)
{'='*60}
{history_str}

{'='*60}
SECTION 2 — AVAILABLE MOVIES POOL (Select exactly {movies_count} from HERE ONLY)
{'='*60}
{movies_str}

{'='*60}
SECTION 3 — AVAILABLE TV SHOWS POOL (Select exactly {shows_count} from HERE ONLY)
{'='*60}
{shows_str}

Remember: Output a valid JSON object containing "vibe_analysis" and the "recommendations" array.
All recommendations MUST come from Section 2 or Section 3. NEVER from Section 1.
"""

    def _format_items_for_prompt(self, items: list[dict]) -> str:
        lines = []
        for item in items:
            # Genres intentionally omitted to force the model to read the summary
            summary = item.get("summary", "")[:400] if item.get("summary") else "No summary available"

            line = (
                f"ID:{item.get('rating_key')} | "
                f"Title: {item.get('title')} ({item.get('year')}) | "
                f"Summary: {summary}"
            )
            lines.append(line)
        return "\n".join(lines)

    def _parse_response(self, content: str, available_content: list[dict] = None) -> list[dict]:
        """Parse the AI response and validate rating_key IDs against available_content.

        LLMs are known to confuse numeric IDs when working with large lists.
        This method cross-references every recommendation's rating_key with the
        actual available_content pool. If the ID doesn't match the title, it
        attempts to find the correct ID by searching for the title. If no match
        is found at all, the recommendation is discarded.
        """
        content = content.strip()

        # Remove markdown code block if present
        if content.startswith("```"):
            lines = content.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines)

        try:
            parsed_data = json.loads(content)

            # Log the vibe analysis so we can see what the model "thought"
            if isinstance(parsed_data, dict) and "vibe_analysis" in parsed_data:
                logger.info(f"AI Vibe Analysis: {parsed_data['vibe_analysis']}")

            # Extract recommendations list from the new schema
            if isinstance(parsed_data, dict):
                recommendations = parsed_data.get("recommendations", [])
            elif isinstance(parsed_data, list):
                # Fallback: model returned a plain array
                recommendations = parsed_data
            else:
                recommendations = []

            # Build lookup maps from available_content for ID validation
            key_to_item = {}
            title_to_item = {}  # normalized title -> item
            if available_content:
                for item in available_content:
                    key_to_item[str(item["rating_key"])] = item
                    # Store under normalized title so year/spacing differences don't break lookup
                    title_to_item[_normalize_title(item["title"])] = item

            valid = []
            seen_keys = set()  # Deduplication safety net

            for rec in recommendations:
                if "rating_key" not in rec or "title" not in rec:
                    continue

                r_key = str(rec["rating_key"])
                ai_title = rec["title"].strip()

                # --- ID Validation (only when available_content is provided) ---
                if available_content:
                    matched_item = key_to_item.get(r_key)

                    if matched_item:
                        # ID exists — verify the title roughly matches
                        real_title = matched_item["title"].lower()
                        ai_title_lower = ai_title.lower()
                        titles_match = (
                            ai_title_lower in real_title or
                            real_title in ai_title_lower
                        )
                        if not titles_match:
                            # Classic LLM ID mismatch — try to find the correct item by title
                            logger.warning(
                                f"ID MISMATCH: AI said ID={r_key} is '{ai_title}' "
                                f"but that ID belongs to '{matched_item['title']}'. "
                                f"Searching by title..."
                            )
                            found = title_to_item.get(_normalize_title(ai_title))
                            if found:
                                logger.info(
                                    f"ID FIXED: '{ai_title}' corrected from "
                                    f"ID={r_key} to ID={found['rating_key']}"
                                )
                                r_key = str(found["rating_key"])
                                matched_item = found
                            else:
                                logger.warning(
                                    f"SKIPPED: Could not find '{ai_title}' in available content."
                                )
                                continue
                    else:
                        # ID not found at all — try to recover by title
                        logger.warning(
                            f"UNKNOWN ID: AI returned ID={r_key} for '{ai_title}' "
                            f"which is not in available content. Searching by title..."
                        )
                        found = title_to_item.get(_normalize_title(ai_title))
                        if found:
                            logger.info(
                                f"ID RECOVERED: '{ai_title}' found with ID={found['rating_key']}"
                            )
                            r_key = str(found["rating_key"])
                            matched_item = found
                        else:
                            logger.warning(
                                f"SKIPPED: '{ai_title}' (ID={r_key}) not found anywhere in available content."
                            )
                            continue

                    # Use validated data from the real library item
                    if r_key not in seen_keys:
                        seen_keys.add(r_key)
                        valid.append({
                            "rating_key": r_key,
                            "title": matched_item["title"],
                            "type": matched_item.get("type", rec.get("type", "movie")),
                            "reason": rec.get("reason", ""),
                        })
                else:
                    # No available_content provided — fallback to original behaviour
                    if r_key not in seen_keys:
                        seen_keys.add(r_key)
                        valid.append({
                            "rating_key": r_key,
                            "title": ai_title,
                            "type": rec.get("type", "movie"),
                            "reason": rec.get("reason", ""),
                        })

            return valid

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse AI response as JSON. Error: {e}\nContent: {content[:500]}")
            return []


# Singleton
ai_service = AIService()

import json
import logging
import httpx

from app.config import get_settings

logger = logging.getLogger("plexai.ai")
settings = get_settings()

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"


class AIService:
    """Service for generating recommendations using OpenRouter LLM."""

    def __init__(self, api_key: str = None, model: str = None):
        self.api_key = api_key or settings.openrouter_api_key
        self.model = model or settings.openrouter_model

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
            "temperature": 0.7,
            "max_tokens": 4096,
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

        # Parse the response
        content = data["choices"][0]["message"]["content"]
        logger.info(f"AI raw response ({len(content)} chars):\n{content}")

        result = self._parse_response(content)
        movies = [r for r in result if r.get("type") == "movie"][:m_count]
        shows = [r for r in result if r.get("type") == "show"][:s_count]
        logger.info(f"AI generated {len(movies)} movie + {len(shows)} show recommendations")
        return {"movies": movies, "shows": shows}

    def _build_system_prompt(self) -> str:
        return """You are an expert movie and TV show recommender for a personal Plex media server.

Your job is to analyze a user's watch history and recommend unwatched content from the server's library.
You MUST recommend BOTH movies AND TV shows separately.

RULES:
1. Only recommend items from the AVAILABLE CONTENT lists - never suggest items not in the library.
2. For TV shows, recommend the ENTIRE SHOW (use the show's rating_key), not individual episodes.
3. Consider genre preferences, directors, actors, and ratings from the watch history.
4. MIX STRATEGY: 70% of recommendations should be similar to watch history (Exploitation), and 30% should be new/different high-rated content (Exploration).
5. Provide variety - don't recommend only one genre or franchise.
5. Respond ONLY with a valid JSON array, no other text.
6. Each item MUST have "type" set to either "movie" or "show" correctly.

RESPONSE FORMAT:
[
  {
    "rating_key": "12345",
    "title": "Movie Title",
    "type": "movie",
    "reason": "Brief reason for recommendation in Hebrew"
  },
  {
    "rating_key": "67890",
    "title": "Show Title",
    "type": "show",
    "reason": "Brief reason for recommendation in Hebrew"
  }
]"""

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
TASK: You are a sophisticated Content Curator. Your goal is to understand the user's specific taste based on the PLOT and THEMES of what they watched, not just the genre tags.

STEP 1: ANALYZE WATCH HISTORY
Read the 'Summary' of the items below. Define the user's "Vibe":
- Do they like dark, gritty, realistic stories?
- Do they prefer lighthearted, escapist fun?
- Do they like complex anti-heroes or classic good guys?
- Note the level of violence, drama, and maturity.

USER WATCH HISTORY:
{history_str}

STEP 2: SELECT RECOMMENDATIONS
Select exactly {movies_count} MOVIES and {shows_count} TV SHOWS from the available pools.
MATCHING LOGIC:
1. **Plot Similarity**: Prioritize items where the 'Summary' sounds like a story the user would enjoy based on Step 1.
2. **Tone Consistency**: If the user watches "Tulsa King" (Gritty Mafia), do NOT recommend "The Magic School Bus" just because it's popular. Keep the maturity level consistent.
3. **Genre Nuance**: "Action" can be a Marvel movie or a brutal war movie. Use the Summary to distinguish between them and match the user's preference.

AVAILABLE MOVIES POOL:
{movies_str}

AVAILABLE TV SHOWS POOL:
{shows_str}

OUTPUT FORMAT:
Return a single JSON array containing all {movies_count + shows_count} items.
The 'reason' field must be in Hebrew and explain the connection based on the PLOT/THEME (e.g., "דרמת פשע מחוספסת עם אנטי-גיבור, בדומה לטולסה קינג שאהבת").
IMPORTANT: Ensure the JSON is valid.
"""

    def _format_items_for_prompt(self, items: list[dict]) -> str:
        lines = []
        for item in items:
            genres = ", ".join(item.get("genres", [])[:3]) # Up to 3 genres
            # INCREASED SUMMARY LIMIT: Giving the AI more context to understand the plot
            summary = item.get("summary", "")[:400] if item.get("summary") else "No summary available"
            
            line = (
                f"ID:{item.get('rating_key')} | "
                f"Title: {item.get('title')} ({item.get('year')}) | "
                f"Genres: {genres} | "
                f"Summary: {summary}"
            )
            lines.append(line)
        return "\n".join(lines)

    def _parse_response(self, content: str) -> list[dict]:
        """Parse the AI response to extract recommendations."""
        # Try to find JSON array in the response
        content = content.strip()

        # Remove markdown code block if present
        if content.startswith("```"):
            lines = content.split("\n")
            # Remove first and last lines (```json and ```)
            content = "\n".join(lines[1:-1])

        try:
            recommendations = json.loads(content)
            if isinstance(recommendations, list):
                # Validate each recommendation has required fields
                valid = []
                for rec in recommendations:
                    if "rating_key" in rec and "title" in rec:
                        valid.append({
                            "rating_key": str(rec["rating_key"]),
                            "title": rec["title"],
                            "type": rec.get("type", "movie"),
                            "reason": rec.get("reason", ""),
                        })
                return valid
        except json.JSONDecodeError:
            logger.error(f"Failed to parse AI response as JSON: {content[:200]}")

        return []


# Singleton
ai_service = AIService()

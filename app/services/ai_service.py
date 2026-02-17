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

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                OPENROUTER_API_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://plexai-curator.local",
                    "X-Title": "PlexAI Personal Curator",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.7,
                    "max_tokens": 4096,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        # Parse the response
        content = data["choices"][0]["message"]["content"]
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
4. Avoid recommending items that were previously recommended but not watched (negative signal).
5. Provide variety - don't recommend only one genre.
6. Respond ONLY with a valid JSON array, no other text.
7. Each item MUST have "type" set to either "movie" or "show" correctly.

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
        # Format watch history
        history_str = self._format_items_for_prompt(watch_history[:50])

        # Format available content - movies and shows separately
        movies_str = self._format_items_for_prompt(available_movies[:200])
        shows_str = self._format_items_for_prompt(available_shows[:200])

        # Format negative feedback
        feedback_str = ""
        if past_recommendations:
            rejected = [r for r in past_recommendations if r.get("was_removed") or not r.get("was_watched")]
            if rejected:
                feedback_str = "\n\n--- PREVIOUSLY RECOMMENDED BUT NOT WATCHED (avoid these patterns) ---\n"
                for item in rejected[:20]:
                    feedback_str += f"- {item.get('title', 'Unknown')}\n"

        return f"""Analyze this user's watch history and recommend content from the available library.

You MUST recommend EXACTLY {movies_count} MOVIES and EXACTLY {shows_count} TV SHOWS.

--- WATCH HISTORY (what the user enjoyed) ---
{history_str}

--- AVAILABLE MOVIES (choose {movies_count} movies from these ONLY, type must be "movie") ---
{movies_str}

--- AVAILABLE TV SHOWS (choose {shows_count} shows from these ONLY, type must be "show") ---
{shows_str}
{feedback_str}

IMPORTANT: Recommend exactly {movies_count} movies AND {shows_count} TV shows.
Make sure the "type" field matches: "movie" for movies, "show" for TV shows.
Respond with a single JSON array containing all {movies_count + shows_count} items."""

    def _format_items_for_prompt(self, items: list[dict]) -> str:
        """Format items for the AI prompt, keeping it concise."""
        lines = []
        for item in items:
            genres = ", ".join(item.get("genres", [])[:3]) if item.get("genres") else "N/A"
            directors = ", ".join(item.get("directors", [])[:2]) if item.get("directors") else ""
            actors = ", ".join(item.get("actors", [])[:3]) if item.get("actors") else ""

            line = (
                f"[{item.get('rating_key')}] "
                f"{item.get('title', 'Unknown')} ({item.get('year', 'N/A')}) "
                f"| Type: {item.get('type', 'unknown')} "
                f"| Genres: {genres}"
            )
            if directors:
                line += f" | Dir: {directors}"
            if actors:
                line += f" | Cast: {actors}"
            if item.get("rating"):
                line += f" | Rating: {item['rating']}"
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

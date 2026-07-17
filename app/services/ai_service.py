import json
import logging
import re
import httpx

from app.config import get_settings

logger = logging.getLogger("plexai.ai")
settings = get_settings()

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"


def _normalize_title(title: str) -> str:
    """Normalize a media title for fuzzy comparison."""
    title = title.lower()
    title = re.sub(r"\s*\(\d{4}\)\s*$", "", title)
    return title.strip()


class AIService:
    """Service for generating recommendations using OpenRouter LLM."""

    def __init__(self, api_key: str = None, model: str = None):
        self.api_key = api_key or settings.openrouter_api_key
        raw_model = model or settings.openrouter_model
        
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
        """Generate personalized recommendations using AI."""
        m_count = movies_count or settings.playlist_size
        s_count = shows_count or settings.playlist_size

        available_movies = [c for c in available_content if c["type"] == "movie"]
        available_shows = [c for c in available_content if c["type"] == "show"]

        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(
            watch_history, available_movies, available_shows,
            past_recommendations, m_count, s_count
        )

        request_body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.4, 
            "max_tokens": 8192,
            "response_format": {"type": "json_object"},
        }

        async with httpx.AsyncClient(timeout=180) as client: 
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

        choice = data.get("choices", [{}])[0]
        content = choice.get("message", {}).get("content")

        if content is None:
            content = "{}"

        result = self._parse_response(content, available_content)
        movies = [r for r in result if r.get("type") == "movie"][:m_count]
        shows = [r for r in result if r.get("type") == "show"][:s_count]
        return {"movies": movies, "shows": shows}

    def _build_system_prompt(self) -> str:
        return """You are an expert Content Curator for a personal Plex media server.
Your task is to analyze a user's watch history and group unwatched library items into highly tailored, dynamic themes.

CRITICAL RULES:
1. THEME CREATION: Group recommendations into broad conversational themes based on watch history. Format: "Since you liked [Title from History], you'll love this".
2. MANDATORY QUOTA: You MUST generate at least 4 different themes. Each theme MUST contain at least 5 to 10 items. Group similar genres/vibes together broadly to achieve this quota.
3. STRICT LIBRARY MATCH: Only recommend items from the AVAILABLE POOLS below. You must use the exact rating_key provided. Do not invent titles or IDs.
4. NEVER RECOMMEND WATCHED: Never recommend any item that appears in the USER WATCH HISTORY.
5. JSON FORMAT: You MUST respond with a valid JSON object matching the exact schema below.

EXPECTED JSON SCHEMA:
{
  "vibe_analysis": "2-3 sentences in English analyzing the user's taste.",
  "recommendations": [
    {
      "rating_key": "12345",
      "title": "EXACT title copied from the pool",
      "type": "movie",
      "playlist_title": "Since you liked GoldenEye, you'll love this",
      "reason": "Brief clinical reason explaining how this fits the specific theme."
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
TASK: Select movies and TV shows and organize them into dynamic 'Since you liked...' themes.
============================================================
SECTION 1 — USER WATCH HISTORY (Use these to build your theme titles!)
============================================================
{history_str}

============================================================
SECTION 2 — AVAILABLE MOVIES POOL
============================================================
{movies_str}

============================================================
SECTION 3 — AVAILABLE TV SHOWS POOL
============================================================
{shows_str}
"""

    def _format_items_for_prompt(self, items: list[dict]) -> str:
        lines = []
        for item in items:
            summary = item.get("summary", "")[:400] if item.get("summary") else "No summary available"
            line = f"ID:{item.get('rating_key')} | Title: {item.get('title')} ({item.get('year')}) | Summary: {summary}"
            lines.append(line)
        return "\n".join(lines)

    def _parse_response(self, content: str, available_content: list[dict] = None) -> list[dict]:
        content = content.strip()
        raw_content = content # Keep a copy for debugging
        
        # Using single quotes for the markdown checks to prevent copy-paste markdown parser errors
        if content.startswith('```'):
            lines = content.split('\n')
            if lines[0].startswith('```'):
                lines = lines[1:]
            if lines and lines[-1].startswith('```'):
                lines = lines[:-1]
            content = '\n'.join(lines)

        try:
            parsed_data = json.loads(content)
            recommendations = parsed_data.get("recommendations", []) if isinstance(parsed_data, dict) else []

            key_to_item = {}
            title_to_item = {}
            if available_content:
                for item in available_content:
                    key_to_item[str(item["rating_key"])] = item
                    title_to_item[_normalize_title(item["title"])] = item

            valid = []
            seen_keys = set()

            for rec in recommendations:
                if "rating_key" not in rec or "title" not in rec:
                    continue

                r_key = str(rec["rating_key"])
                ai_title = rec["title"].strip()
                theme = rec.get("playlist_title", "Recommended For You")

                if available_content:
                    matched_item = key_to_item.get(r_key)
                    if not matched_item:
                        matched_item = title_to_item.get(_normalize_title(ai_title))
                    
                    if matched_item:
                        r_key = str(matched_item["rating_key"])
                        if r_key not in seen_keys:
                            seen_keys.add(r_key)
                            valid.append({
                                "rating_key": r_key,
                                "title": matched_item["title"],
                                "type": matched_item.get("type", rec.get("type", "movie")),
                                "playlist_title": theme,
                                "reason": rec.get("reason", ""),
                            })
                else:
                    if r_key not in seen_keys:
                        seen_keys.add(r_key)
                        valid.append({
                            "rating_key": r_key,
                            "title": ai_title,
                            "type": rec.get("type", "movie"),
                            "playlist_title": theme,
                            "reason": rec.get("reason", ""),
                        })
            
            # THE FAILSAFE LOGGER: If all items were stripped, print the raw output so we can see why
            if not valid and recommendations:
                logger.warning(f"AI returned {len(recommendations)} recommendations, but ZERO matched your library. Raw AI Output: {raw_content}")
            elif not valid:
                logger.warning(f"AI returned completely empty JSON. Raw AI Output: {raw_content}")
                
            return valid
        except Exception as e:
            logger.error(f"Failed to parse AI JSON response: {e}. Raw AI Output: {raw_content}")
            return []


# Singleton
ai_service = AIService()

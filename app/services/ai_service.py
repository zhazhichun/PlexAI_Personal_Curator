import json
import logging
import re
import httpx

from app.config import get_settings

logger = logging.getLogger("plexai.ai")
settings = get_settings()

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"


def _normalize_title(title: str) -> str:
    title = title.lower()
    title = re.sub(r"\s*\(\d{4}\)\s*$", "", title)
    return title.strip()


class AIService:
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
        else:
            self.model = raw_model

    async def generate_recommendations(
        self,
        media_type: str,
        watch_history: list[dict],
        available_content: list[dict]
    ) -> list[dict]:
        
        system_prompt = self._build_system_prompt(media_type)
        user_prompt = self._build_user_prompt(
            watch_history, available_content
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

        # Parse and return directly without splitting
        result = self._parse_response(content, available_content, watch_history)
        return result

    def _build_system_prompt(self, media_type: str) -> str:
        # Dynamic label based on what library we are looking at
        media_label = "Movies" if media_type == "movie" else "TV Shows"
        
        return f"""You are an expert Content Curator for a personal Plex media server.
Your task is to analyze a user's {media_label} watch history and group {media_label} into highly tailored, dynamic themes.

CRITICAL RULES:
1. THEME CREATION (DISCOVERY): Group UNWATCHED {media_label} into broad conversational themes based on watch history. Format: "Since you liked [Title from History], you'll love this".
2. THE REWATCH THEME: You MUST create exactly ONE theme titled "Rewatch: Old Favorites". This specific theme must contain 5 to 8 items pulled EXCLUSIVELY from the USER WATCH HISTORY pool. 
3. STRICT OUTPUT QUOTA: You MUST output EXACTLY 3 to 5 themes total (including the Rewatch theme). Each theme MUST contain EXACTLY 5 to 10 items. Do not exceed this limit.
4. STRICT LIBRARY MATCH: Only recommend items from the provided pools. You must use the exact rating_key provided. Do not invent titles or IDs.
5. JSON FORMAT: You MUST respond with a valid JSON object matching the exact schema below.

EXPECTED JSON SCHEMA:
{{
  "vibe_analysis": "2-3 sentences in English analyzing the user's taste.",
  "recommendations": [
    {{
      "rating_key": "12345",
      "title": "EXACT title copied from the pool",
      "type": "{media_type}",
      "playlist_title": "Since you liked GoldenEye, you'll love this",
      "reason": "Brief clinical reason explaining how this fits the specific theme."
    }}
  ]
}}"""

    def _build_user_prompt(
        self,
        watch_history: list[dict],
        available_content: list[dict]
    ) -> str:
        history_str = self._format_items_for_prompt(watch_history)
        content_str = self._format_items_for_prompt(available_content)

        return f"""
TASK: Select items and organize them into dynamic themes.
============================================================
SECTION 1 — USER WATCH HISTORY (Use these to build discovery themes, AND to populate your ONE "Rewatch: Old Favorites" theme!)
============================================================
{history_str}

============================================================
SECTION 2 — AVAILABLE UNWATCHED POOL
============================================================
{content_str}
"""

    def _format_items_for_prompt(self, items: list[dict]) -> str:
        lines = []
        for item in items:
            # Summaries remain stripped to keep network payloads lightning fast
            line = f"ID:{item.get('rating_key')} | Title: {item.get('title')} ({item.get('year')})"
            lines.append(line)
        return "\n".join(lines)

    def _parse_response(self, content: str, available_content: list[dict] = None, watch_history: list[dict] = None) -> list[dict]:
        content = content.strip()
        raw_content = content 
        
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
            
            combined_pool = (available_content or []) + (watch_history or [])
            for item in combined_pool:
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

                if combined_pool:
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
            
            if not valid and recommendations:
                logger.warning(f"AI returned {len(recommendations)} recommendations, but ZERO matched your library. Raw AI Output: {raw_content}")
            elif not valid:
                logger.warning(f"AI returned completely empty JSON. Raw AI Output: {raw_content}")
                
            return valid
        except Exception as e:
            logger.error(f"Failed to parse AI JSON response: {e}. Raw AI Output: {raw_content}")
            return []

ai_service = AIService()

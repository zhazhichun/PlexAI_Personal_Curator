import logging
import httpx
from typing import Optional

from app.config import get_settings

logger = logging.getLogger("plexai.tautulli")
settings = get_settings()


class TautulliService:
    """Service for interacting with the Tautulli API."""

    def __init__(self, url: str = None, api_key: str = None):
        self.url = (url or settings.tautulli_url).rstrip("/")
        self.api_key = api_key or settings.tautulli_api_key

    async def _request(self, cmd: str, params: dict = None) -> dict:
        """Make a request to the Tautulli API."""
        request_params = {
            "apikey": self.api_key,
            "cmd": cmd,
            **(params or {}),
        }
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.url}/api/v2",
                params=request_params,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("response", {}).get("result") != "success":
                raise Exception(
                    f"Tautulli API error: {data.get('response', {}).get('message', 'Unknown')}"
                )
            return data["response"]["data"]

    async def get_user_watch_history(
        self, user_id: str, length: int = 100, media_type: Optional[str] = None
    ) -> list[dict]:
        """Get watch history for a specific user.

        Args:
            user_id: Tautulli user ID
            length: Number of records to retrieve
            media_type: Filter by type ('movie' or 'episode')
        """
        params = {
            "user_id": user_id,
            "length": str(length),
            "order_column": "date",
            "order_dir": "desc",
        }
        if media_type:
            params["media_type"] = media_type

        data = await self._request("get_history", params)
        history = []
        for item in data.get("data", []):
            history.append({
                "title": item.get("full_title", item.get("title", "")),
                "year": item.get("year"),
                "media_type": item.get("media_type", ""),
                "rating_key": str(item.get("rating_key", "")),
                "parent_rating_key": str(item.get("parent_rating_key", "")),
                "grandparent_rating_key": str(item.get("grandparent_rating_key", "")),
                "watched_date": item.get("date", ""),
                "duration": item.get("duration", 0),
                "percent_complete": item.get("percent_complete", 0),
            })
        logger.info(f"Retrieved {len(history)} history items for user {user_id}")
        return history

    async def get_user_watch_stats(self, user_id: str) -> dict:
        """Get watch statistics for a user."""
        data = await self._request("get_user_watch_time_stats", {"user_id": user_id})
        return data

    async def get_users(self) -> list[dict]:
        """Get all users known to Tautulli."""
        data = await self._request("get_users")
        users = []
        for user in data:
            users.append({
                "user_id": str(user.get("user_id", "")),
                "username": user.get("username", ""),
                "friendly_name": user.get("friendly_name", ""),
                "email": user.get("email", ""),
                "is_active": user.get("is_active", 0) == 1,
            })
        return users

    async def get_user_by_plex_id(self, plex_user_id: str) -> Optional[dict]:
        """Find a Tautulli user by their Plex user ID."""
        users = await self.get_users()
        for user in users:
            if user["user_id"] == plex_user_id:
                return user
        return None

    async def get_recently_watched_genres(self, user_id: str, limit: int = 100) -> dict:
        """Analyze recently watched content to extract genre preferences."""
        history = await self.get_user_watch_history(user_id, length=limit)
        genre_counts = {}
        for item in history:
            # Tautulli history doesn't include genres directly,
            # we'll rely on Plex API metadata for genre info
            pass
        return genre_counts


# Singleton
tautulli_service = TautulliService()

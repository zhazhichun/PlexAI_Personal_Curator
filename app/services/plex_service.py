import logging
import httpx
from typing import Optional

from app.config import get_settings

logger = logging.getLogger("plexai.plex")
settings = get_settings()

PLEX_AUTH_URL = "https://app.plex.tv"
PLEX_API_URL = "https://plex.tv/api/v2"
CLIENT_ID = "PlexAI-Personal-Curator"
CLIENT_NAME = "PlexAI Personal Curator"


class PlexService:
    """Service for interacting with the Plex API."""

    def __init__(self, server_url: str = None, admin_token: str = None):
        self.server_url = (server_url or settings.plex_server_url).rstrip("/")
        self.admin_token = admin_token or settings.plex_admin_token

    def _headers(self, token: str = None) -> dict:
        """Common headers for Plex API requests."""
        return {
            "Accept": "application/json",
            "X-Plex-Token": token or self.admin_token,
            "X-Plex-Client-Identifier": CLIENT_ID,
            "X-Plex-Product": CLIENT_NAME,
        }

    # === OAuth Flow ===

    async def get_pin(self) -> dict:
        """Request a new Plex PIN for OAuth."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{PLEX_API_URL}/pins",
                headers={
                    "Accept": "application/json",
                    "X-Plex-Client-Identifier": CLIENT_ID,
                    "X-Plex-Product": CLIENT_NAME,
                },
                data={"strong": "true"},
            )
            resp.raise_for_status()
            data = resp.json()
            return {"id": data["id"], "code": data["code"]}

    def get_auth_url(self, pin_code: str) -> str:
        """Build the Plex OAuth URL for the user to authorize."""
        return (
            f"{PLEX_AUTH_URL}/auth#?"
            f"clientID={CLIENT_ID}&"
            f"code={pin_code}&"
            f"context%5Bdevice%5D%5Bproduct%5D={CLIENT_NAME}"
        )

    async def check_pin(self, pin_id: int) -> Optional[str]:
        """Check if a PIN has been authorized and return the auth token."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{PLEX_API_URL}/pins/{pin_id}",
                headers={
                    "Accept": "application/json",
                    "X-Plex-Client-Identifier": CLIENT_ID,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("authToken")

    async def get_user_info(self, token: str) -> dict:
        """Get user info from Plex using their token."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{PLEX_API_URL}/user",
                headers=self._headers(token),
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "id": str(data["id"]),
                "username": data.get("username", data.get("title", "Unknown")),
                "email": data.get("email", ""),
            }

    # === Library Operations ===

    async def get_libraries(self, token: str = None) -> list[dict]:
        """Get all libraries from the Plex server."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.server_url}/library/sections",
                headers=self._headers(token),
            )
            resp.raise_for_status()
            data = resp.json()
            libraries = []
            for lib in data.get("MediaContainer", {}).get("Directory", []):
                if lib["type"] in ("movie", "show"):
                    libraries.append({
                        "key": lib["key"],
                        "title": lib["title"],
                        "type": lib["type"],
                    })
            return libraries

    async def get_library_content(self, library_key: str, token: str = None) -> list[dict]:
        """Get all items from a specific library."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.server_url}/library/sections/{library_key}/all",
                headers=self._headers(token),
            )
            resp.raise_for_status()
            data = resp.json()
            items = []
            for item in data.get("MediaContainer", {}).get("Metadata", []):
                items.append(self._parse_media_item(item))
            return items

    async def get_all_content(self, token: str = None) -> list[dict]:
        """Get all movies and shows from all libraries."""
        libraries = await self.get_libraries(token)
        all_content = []
        for lib in libraries:
            content = await self.get_library_content(lib["key"], token)
            all_content.extend(content)
        logger.info(f"Fetched {len(all_content)} items from {len(libraries)} libraries")
        return all_content

    async def get_watched_items(self, token: str) -> set[str]:
        """Get rating keys of all watched items for a user."""
        watched = set()
        libraries = await self.get_libraries(token)
        async with httpx.AsyncClient() as client:
            for lib in libraries:
                resp = await client.get(
                    f"{self.server_url}/library/sections/{lib['key']}/all",
                    headers=self._headers(token),
                    params={"unwatched": "0"},
                )
                resp.raise_for_status()
                data = resp.json()
                for item in data.get("MediaContainer", {}).get("Metadata", []):
                    if item.get("viewCount", 0) > 0:
                        watched.add(str(item["ratingKey"]))
        logger.info(f"Found {len(watched)} watched items")
        return watched

    # === Playlist Operations ===

    async def get_user_playlists(self, token: str) -> list[dict]:
        """Get all playlists for a user."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.server_url}/playlists",
                headers=self._headers(token),
            )
            resp.raise_for_status()
            data = resp.json()
            playlists = []
            for pl in data.get("MediaContainer", {}).get("Metadata", []):
                playlists.append({
                    "ratingKey": pl["ratingKey"],
                    "title": pl["title"],
                    "type": pl.get("playlistType", ""),
                })
            return playlists

    async def delete_playlist(self, playlist_key: str, token: str):
        """Delete a playlist by its rating key."""
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"{self.server_url}/playlists/{playlist_key}",
                headers=self._headers(token),
            )
            resp.raise_for_status()
            logger.info(f"Deleted playlist {playlist_key}")

    async def create_playlist(
        self, title: str, rating_keys: list[str], token: str, playlist_type: str = "video"
    ) -> dict:
        """Create a new playlist with the given items."""
        # Build the machine ID
        async with httpx.AsyncClient() as client:
            # Get server machine identifier
            resp = await client.get(
                f"{self.server_url}/",
                headers=self._headers(token),
            )
            resp.raise_for_status()
            machine_id = resp.json()["MediaContainer"]["machineIdentifier"]

            # Build URI list
            uri_list = ",".join(
                [f"server://{machine_id}/com.plexapp.plugins.library/library/metadata/{key}"
                 for key in rating_keys]
            )

            resp = await client.post(
                f"{self.server_url}/playlists",
                headers=self._headers(token),
                params={
                    "type": playlist_type,
                    "title": title,
                    "smart": "0",
                    "uri": uri_list,
                },
            )
            resp.raise_for_status()
            logger.info(f"Created playlist '{title}' with {len(rating_keys)} items")
            data = resp.json()
            return data.get("MediaContainer", {}).get("Metadata", [{}])[0]

    # === Helpers ===

    def _parse_media_item(self, item: dict) -> dict:
        """Parse a Plex media item into a standardized dict."""
        genres = [g.get("tag", "") for g in item.get("Genre", [])]
        directors = [d.get("tag", "") for d in item.get("Director", [])]
        actors = [r.get("tag", "") for r in item.get("Role", [])][:5]  # Top 5 actors

        return {
            "rating_key": str(item["ratingKey"]),
            "title": item.get("title", "Unknown"),
            "year": item.get("year"),
            "type": item.get("type", "unknown"),
            "summary": (item.get("summary", ""))[:200],  # Truncate long summaries
            "genres": genres,
            "directors": directors,
            "actors": actors,
            "rating": item.get("audienceRating") or item.get("rating"),
            "content_rating": item.get("contentRating", ""),
            "duration_minutes": round(item.get("duration", 0) / 60000) if item.get("duration") else None,
            "view_count": item.get("viewCount", 0),
        }


# Singleton
plex_service = PlexService()

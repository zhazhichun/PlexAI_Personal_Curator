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

    async def get_server_access_token(self, plex_tv_token: str) -> str | None:
        """Exchange a plex.tv OAuth token for a server-specific access token.

        The plex.tv token alone can't access the server API directly.
        We need the server-specific accessToken from the resources endpoint.
        Matches the server by its machine identifier.

        Args:
            plex_tv_token: The token from Plex OAuth

        Returns:
            Server-specific access token, or None if not found
        """
        # First, get the machine identifier of our configured server
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.server_url}/",
                    headers=self._headers(),  # Use admin token
                )
                resp.raise_for_status()
                our_machine_id = resp.json()["MediaContainer"]["machineIdentifier"]
                logger.info(f"Our server machine ID: {our_machine_id}")
        except Exception as e:
            logger.error(f"Failed to get server machine ID: {e}")
            return None

        # Now find the matching server in user's resources
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{PLEX_API_URL}/resources",
                headers=self._headers(plex_tv_token),
                params={"includeHttps": 1, "includeRelay": 1},
            )
            resp.raise_for_status()
            resources = resp.json()

            for resource in resources:
                if resource.get("provides") != "server":
                    continue
                if resource.get("clientIdentifier") == our_machine_id:
                    access_token = resource.get("accessToken")
                    if access_token:
                        logger.info(f"Found server access token for: {resource['name']}")
                        return access_token

        logger.warning("Could not find server access token in user's resources")
        return None

    # === Library Operations ===

    async def get_libraries(self, token: str = None) -> list[dict]:
        """Hard-coded override to force library filtering."""
        allowed_ids = {"1", "2", "5", "6"}
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.server_url}/library/sections",
                headers=self._headers(token),
            )
            resp.raise_for_status()
            data = resp.json()
            libraries = []
            for lib in data.get("MediaContainer", {}).get("Directory", []):
                if str(lib.get("key")) in allowed_ids:
                    if lib["type"] in ("movie", "show"):
                        libraries.append({
                            "key": lib["key"],
                            "title": lib["title"],
                            "type": lib["type"],
                        })
            return libraries

    async def get_library_content(self, library_key: str, token: str = None, library_title: str = "") -> list[dict]:
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
                items.append(self._parse_media_item(item, library_title, library_key))
            return items

    async def get_all_content(self, token: str = None) -> list[dict]:
        """Get all movies and shows from all libraries."""
        libraries = await self.get_libraries(token)
        all_content = []
        for lib in libraries:
            content = await self.get_library_content(lib["key"], token, lib["title"])
            all_content.extend(content)
        logger.info(f"Fetched {len(all_content)} items from {len(libraries)} libraries")
        return all_content

    async def get_watched_items(self, token: str = None) -> set[str]:
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

    async def get_user_playlists(self, token: str = None) -> list[dict]:
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

    async def delete_playlist(self, playlist_key: str, token: str = None):
        """Delete a playlist by its rating key."""
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"{self.server_url}/playlists/{playlist_key}",
                headers=self._headers(token),
            )
            resp.raise_for_status()
            logger.info(f"Deleted playlist {playlist_key}")

    async def create_playlist(
        self, title: str, rating_keys: list[str], token: str = None, playlist_type: str = "video"
    ) -> dict:
        """Create a new playlist with the given items.

        Creates the playlist with the first item, then adds remaining items
        one by one via PUT requests (multi-item URI in POST doesn't always work).
        """
        if not rating_keys:
            return {}

        async with httpx.AsyncClient(timeout=60) as client:
            # Get server machine identifier
            resp = await client.get(
                f"{self.server_url}/",
                headers=self._headers(token),
            )
            resp.raise_for_status()
            machine_id = resp.json()["MediaContainer"]["machineIdentifier"]

            def _build_uri(key: str) -> str:
                return f"server://{machine_id}/com.plexapp.plugins.library/library/metadata/{key}"

            # Step 1: Create playlist with first item
            resp = await client.post(
                f"{self.server_url}/playlists",
                headers=self._headers(token),
                params={
                    "type": playlist_type,
                    "title": title,
                    "smart": "0",
                    "uri": _build_uri(rating_keys[0]),
                },
            )
            resp.raise_for_status()
            data = resp.json()
            playlist_data = data.get("MediaContainer", {}).get("Metadata", [{}])[0]
            playlist_key = playlist_data.get("ratingKey")

            if not playlist_key:
                logger.error("Failed to get playlist key after creation")
                return playlist_data

            # Step 2: Add remaining items one by one
            added = 1
            for key in rating_keys[1:]:
                try:
                    resp = await client.put(
                        f"{self.server_url}/playlists/{playlist_key}/items",
                        headers=self._headers(token),
                        params={"uri": _build_uri(key)},
                    )
                    resp.raise_for_status()
                    added += 1
                except Exception as e:
                    logger.warning(f"Failed to add item {key} to playlist: {e}")

            logger.info(f"Created playlist '{title}' with {added}/{len(rating_keys)} items")
            return playlist_data

    async def get_first_episode(self, show_rating_key: str, token: str = None) -> str | None:
        """Get the rating_key of the first episode (S01E01) of a show.

        This is needed because Plex playlists expand shows into individual episodes.
        By adding only S01E01, the show appears as one item in the playlist.

        Args:
            show_rating_key: The rating key of the show
            token: Plex auth token

        Returns:
            Rating key of the first episode, or None if not found
        """
        async with httpx.AsyncClient() as client:
            # Get seasons of the show
            try:
                resp = await client.get(
                    f"{self.server_url}/library/metadata/{show_rating_key}/children",
                    headers=self._headers(token),
                )
                resp.raise_for_status()
                seasons = resp.json().get("MediaContainer", {}).get("Metadata", [])
            except httpx.HTTPStatusError as e:
                # If 400 Bad Request, it's likely not a show (e.g. it's an episode or movie)
                if e.response.status_code == 400:
                    logger.warning(f"Item {show_rating_key} is not a valid show (400 Bad Request)")
                    return None
                raise e
            except Exception as e:
                logger.error(f"Failed to get children for {show_rating_key}: {e}")
                return None

            if not seasons:
                return None

            # Get first season (skip specials - season 0)
            first_season = None
            for season in seasons:
                if season.get("index", 0) >= 1:
                    first_season = season
                    break
            if not first_season:
                first_season = seasons[0]

            # Get episodes of the first season
            resp = await client.get(
                f"{self.server_url}/library/metadata/{first_season['ratingKey']}/children",
                headers=self._headers(token),
            )
            resp.raise_for_status()
            episodes = resp.json().get("MediaContainer", {}).get("Metadata", [])

            if episodes:
                return str(episodes[0]["ratingKey"])

            return None

    # === Helpers ===

    def _parse_media_item(self, item: dict, library_title: str = "", library_id: str = "") -> dict:
        """Parse a Plex media item into a standardized dict."""
        genres = [g.get("tag", "") for g in item.get("Genre", [])]
        directors = [d.get("tag", "") for d in item.get("Director", [])]
        actors = [r.get("tag", "") for r in item.get("Role", [])][:5]  # Top 5 actors

        return {
            "rating_key": str(item["ratingKey"]),
            "title": item.get("title", "Unknown"),
            "year": item.get("year"),
            "type": item.get("type", "unknown"),
            "library": library_title,
            "library_id": str(item.get("librarySectionID", library_id)),
            "summary": (item.get("summary", ""))[:200],  # Truncate long summaries
            "genres": genres,
            "directors": directors,
            "actors": actors,
            "rating": item.get("audienceRating") or item.get("rating"),
            "content_rating": item.get("contentRating", ""),
            "duration_minutes": round(item.get("duration", 0) / 60000) if item.get("duration") else None,
            "view_count": item.get("viewCount", 0),
            "viewed_leaf_count": item.get("viewedLeafCount", 0),
        }


# Singleton
plex_service = PlexService()

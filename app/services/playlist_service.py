import logging

from app.services.plex_service import plex_service

logger = logging.getLogger("plexai.playlist")

PLAYLIST_NAME = "🤖 AI: המומלצים שלך"


class PlaylistService:
    """Service for managing AI recommendation playlists in Plex."""

    async def update_user_playlist(
        self, user_token: str, recommendations: list[dict]
    ) -> dict:
        """Delete old AI playlist and create a new one with fresh recommendations.

        Args:
            user_token: Plex auth token for the user
            recommendations: List of recommended items with rating_keys

        Returns:
            Created playlist info
        """
        # Step 1: Find and delete existing AI playlist
        await self._remove_old_playlist(user_token)

        # Step 2: Extract rating keys from recommendations
        rating_keys = [rec["rating_key"] for rec in recommendations]

        if not rating_keys:
            logger.warning("No rating keys to create playlist with")
            return {}

        # Step 3: Create new playlist
        playlist = await plex_service.create_playlist(
            title=PLAYLIST_NAME,
            rating_keys=rating_keys,
            token=user_token,
        )

        logger.info(
            f"Updated AI playlist with {len(rating_keys)} items"
        )
        return playlist

    async def _remove_old_playlist(self, user_token: str):
        """Find and delete the old AI recommendation playlist."""
        playlists = await plex_service.get_user_playlists(user_token)
        for pl in playlists:
            if pl["title"] == PLAYLIST_NAME:
                await plex_service.delete_playlist(pl["ratingKey"], user_token)
                logger.info(f"Removed old AI playlist: {pl['ratingKey']}")
                return
        logger.info("No old AI playlist found to remove")

    async def get_current_playlist_items(self, user_token: str) -> list[str]:
        """Get the rating keys of items currently in the AI playlist."""
        playlists = await plex_service.get_user_playlists(user_token)
        for pl in playlists:
            if pl["title"] == PLAYLIST_NAME:
                # TODO: Fetch playlist items to compare
                return []
        return []


# Singleton
playlist_service = PlaylistService()

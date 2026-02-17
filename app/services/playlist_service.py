import logging

from app.services.plex_service import plex_service

logger = logging.getLogger("plexai.playlist")

MOVIES_PLAYLIST_NAME = "🤖 AI: סרטים מומלצים"
SHOWS_PLAYLIST_NAME = "🤖 AI: סדרות מומלצות"


class PlaylistService:
    """Service for managing AI recommendation playlists in Plex."""

    async def update_user_playlists(
        self,
        user_token: str,
        movie_recommendations: list[dict],
        show_recommendations: list[dict],
        username: str = "",
    ) -> dict:
        """Create separate playlists for movies and shows.

        Uses the user's own Plex token so playlists belong to them.
        For shows, adds only the first episode (S01E01) so each show
        appears as a single item instead of expanding to all episodes.

        Args:
            user_token: Plex auth token for the user
            movie_recommendations: List of recommended movies with rating_keys
            show_recommendations: List of recommended shows with rating_keys
            username: Username for logging purposes

        Returns:
            Dict with created playlist info
        """
        result = {}

        # Movies playlist
        if movie_recommendations:
            movie_keys = [rec["rating_key"] for rec in movie_recommendations]
            await self._remove_old_playlist(user_token, MOVIES_PLAYLIST_NAME)
            playlist = await plex_service.create_playlist(
                title=MOVIES_PLAYLIST_NAME,
                rating_keys=movie_keys,
                token=user_token,
            )
            result["movies"] = playlist
            logger.info(f"Created movies playlist for {username} with {len(movie_keys)} items")

        # Shows playlist - use first episode of each show
        if show_recommendations:
            episode_keys = []
            for rec in show_recommendations:
                first_ep = await plex_service.get_first_episode(
                    rec["rating_key"], token=user_token
                )
                if first_ep:
                    episode_keys.append(first_ep)
                    logger.info(f"Show '{rec['title']}' -> first episode key: {first_ep}")
                else:
                    logger.warning(f"Could not find first episode for show '{rec['title']}'")

            if episode_keys:
                await self._remove_old_playlist(user_token, SHOWS_PLAYLIST_NAME)
                playlist = await plex_service.create_playlist(
                    title=SHOWS_PLAYLIST_NAME,
                    rating_keys=episode_keys,
                    token=user_token,
                )
                result["shows"] = playlist
                logger.info(f"Created shows playlist for {username} with {len(episode_keys)} items")

        return result

    async def _remove_old_playlist(self, user_token: str, playlist_name: str):
        """Find and delete an old playlist by name."""
        playlists = await plex_service.get_user_playlists(user_token)
        for pl in playlists:
            if pl["title"] == playlist_name:
                await plex_service.delete_playlist(pl["ratingKey"], user_token)
                logger.info(f"Removed old playlist: {pl['title']} ({pl['ratingKey']})")
                return
        logger.info(f"No old playlist '{playlist_name}' found to remove")


# Singleton
playlist_service = PlaylistService()

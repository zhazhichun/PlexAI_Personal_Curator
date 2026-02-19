import logging

from app.services.plex_service import plex_service

logger = logging.getLogger("plexai.playlist")

MOVIES_PLAYLIST_NAME = "🤖 AI: Recommended Movies"
SHOWS_PLAYLIST_NAME = "🤖 AI: Recommended Shows"


class PlaylistService:
    """Service for managing AI recommendation playlists in Plex."""

    async def update_user_playlists(
        self,
        user_token: str,
        movie_recommendations: list[dict],
        show_recommendations: list[dict],
        username: str = "",
    ) -> dict:
        """Create separate playlists for each library source.

        Args:
            user_token: Plex auth token for the user
            movie_recommendations: List of recommended movies
            show_recommendations: List of recommended shows
            username: Username for logging purposes

        Returns:
            Dict with created playlist info
        """
        result = {}
        
        # Combine and group by library
        all_recs = movie_recommendations + show_recommendations
        library_map = {}
        
        for rec in all_recs:
            # Fallback for legacy calls or missing data
            lib = rec.get("library") or ("Movies" if rec["type"] == "movie" else "Shows")
            if lib not in library_map:
                library_map[lib] = []
            library_map[lib].append(rec)

        # Process each library group
        for lib_name, items in library_map.items():
            playlist_title = f"🤖 AI: {lib_name}"
            rating_keys = []
            
            for item in items:
                if item["type"] == "show":
                    # Get first episode for shows
                    first_ep = await plex_service.get_first_episode(
                        item["rating_key"], token=user_token
                    )
                    if first_ep:
                        rating_keys.append(first_ep)
                        logger.info(f"Show '{item['title']}' -> first episode key: {first_ep}")
                    else:
                        logger.warning(f"Could not find first episode for show '{item['title']}'")
                else:
                    rating_keys.append(item["rating_key"])

            if rating_keys:
                # Remove old playlist with exact same name if exists
                await self._remove_old_playlist(user_token, playlist_title)
                
                # Create new playlist
                playlist = await plex_service.create_playlist(
                    title=playlist_title,
                    rating_keys=rating_keys,
                    token=user_token,
                )
                result[lib_name] = playlist
                logger.info(f"Created playlist '{playlist_title}' for {username} with {len(rating_keys)} items")

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

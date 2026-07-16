import logging
from plexapi.server import PlexServer
from app.config import get_settings

logger = logging.getLogger("plexai.playlists")
settings = get_settings()

class PlaylistService:
    """Handles creation and management of thematic Plex playlists for users."""

    def __init__(self):
        # We use a hidden summary tag instead of a title prefix to track automated playlists
        self.tracking_tag = "[PlexAI-Managed]"

    def sync_thematic_playlists(self, plex_server: PlexServer, user_token: str, recommendations: list[dict]):
        """
        Connects to Plex using the specific user's session token, clears out
        their previous daily AI playlists, and builds the new theme-based playlists.
        """
        user_plex = PlexServer(plex_server._baseurl, user_token)
        
        # 1. Clear previous day's AI playlists for this specific user
        self._clear_old_playlists(user_plex)

        # 2. Group recommendations by their generated theme title
        themes = {}
        for rec in recommendations:
            theme_title = rec.get("playlist_title", "Recommended For You")
            if theme_title not in themes:
                themes[theme_title] = []
            themes[theme_title].append(rec)

        logger.info(f"Grouped recommendations into {len(themes)} total themes.")

        # 3. Process and filter themes
        sorted_themes = sorted(themes.items(), key=lambda x: len(x[1]), reverse=True)
        target_themes = sorted_themes[:10]  # Cap at a maximum of 10 playlists

        playlists_created = 0

        for theme_name, items in target_themes:
            if len(items) < 20:
                logger.warning(f"Skipping theme '{theme_name}' — only contains {len(items)} items (Minimum: 20)")
                continue

            plex_items = []
            for item in items:
                try:
                    plex_obj = user_plex.library.fetchItem(int(item["rating_key"]))
                    plex_items.append(plex_obj)
                except Exception:
                    continue

            if not plex_items:
                continue

            try:
                # Create the playlist with the clean, natural title
                new_playlist = user_plex.createPlaylist(theme_name, items=plex_items)
                
                # Immediately update the summary to include the hidden tracking tag
                new_playlist.edit(**{"summary": f"{self.tracking_tag} Automated daily curation."})
                
                logger.info(f"Successfully created playlist: '{theme_name}' with {len(plex_items)} items.")
                playlists_created += 1
            except Exception as e:
                logger.error(f"Failed to create playlist '{theme_name}': {e}")

        logger.info(f"Completed playlist synchronization. Created {playlists_created} playlists for user.")

    def _clear_old_playlists(self, user_plex: PlexServer):
        """Finds previous automated playlists using the hidden summary tag."""
        try:
            all_playlists = user_plex.playlists()
            for playlist in all_playlists:
                # Safely check if the summary exists and contains our hidden tracking tag
                summary = getattr(playlist, 'summary', '') or ""
                if self.tracking_tag in summary:
                    logger.info(f"Clearing old recommendation playlist: '{playlist.title}'")
                    playlist.delete()
        except Exception as e:
            logger.error(f"Error while clearing old AI playlists: {e}")

playlist_service = PlaylistService()

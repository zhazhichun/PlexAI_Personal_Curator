import logging
import os
import random
import textwrap
from plexapi.server import PlexServer
from app.config import get_settings

# Import Pillow for dynamic poster generation
try:
    from PIL import Image, ImageDraw, ImageFont
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False

logger = logging.getLogger("plexai.playlists")
settings = get_settings()

class PlaylistService:
    """Handles creation and management of thematic Plex playlists for users."""

    def __init__(self):
        self.tracking_tag = "[PlexAI-Managed]"

    def generate_poster(self, title: str, filepath: str):
        """Generates a gradient frosted-glass poster with the playlist title."""
        if not PILLOW_AVAILABLE:
            logger.warning("Pillow library not found. Skipping poster generation.")
            return False
            
        width, height = 800, 800
        
        # 1. Random Gradient Background (Darker cinematic colors)
        color1 = (random.randint(20, 80), random.randint(20, 80), random.randint(50, 120))
        color2 = (random.randint(50, 120), random.randint(20, 80), random.randint(20, 80))
        
        base = Image.new('RGB', (width, height))
        draw = ImageDraw.Draw(base)
        for y in range(height):
            r = int(color1[0] + (color2[0] - color1[0]) * y / height)
            g = int(color1[1] + (color2[1] - color1[1]) * y / height)
            b = int(color1[2] + (color2[2] - color1[2]) * y / height)
            draw.line([(0, y), (width, y)], fill=(r, g, b))
            
        # 2. Frosted Glass Box
        glass_margin = 60
        glass_rect = [glass_margin, glass_margin, width - glass_margin, height - glass_margin]
        
        # Create a transparent overlay for the glass effect
        overlay = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        # Semi-transparent white fill with a slightly opaque white border
        overlay_draw.rounded_rectangle(glass_rect, radius=40, fill=(255, 255, 255, 40), outline=(255, 255, 255, 100), width=4)
        
        final_img = Image.alpha_composite(base.convert('RGBA'), overlay)
        draw_final = ImageDraw.Draw(final_img)
        
        # 3. Typography
        try:
            # Pillow 10+ allows dynamic sizing on the default font
            font = ImageFont.load_default(size=54)
        except TypeError:
            font = ImageFont.load_default()

        # Word wrap the theme title so it fits nicely inside the glass box
        wrapper = textwrap.TextWrapper(width=18)
        wrapped_text = wrapper.fill(text=title)
        
        try:
            bbox = draw_final.textbbox((0, 0), wrapped_text, font=font, align="center")
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]
        except AttributeError:
            w, h = draw_final.textsize(wrapped_text, font=font)
            
        x = (width - w) / 2
        y = (height - h) / 2
        
        # Draw the text centered
        draw_final.multiline_text((x, y), wrapped_text, font=font, fill=(255, 255, 255, 255), align="center")
        
        # Save to the temporary filepath
        final_img.convert('RGB').save(filepath, 'JPEG', quality=90)
        return True

    def sync_thematic_playlists(self, plex_server: PlexServer, user_token: str, recommendations: list[dict]):
        user_plex = PlexServer(plex_server._baseurl, user_token)
        
        self._clear_old_playlists(user_plex)

        themes = {}
        for rec in recommendations:
            theme_title = rec.get("playlist_title", "Recommended For You")
            if theme_title not in themes:
                themes[theme_title] = []
            themes[theme_title].append(rec)

        logger.info(f"Grouped recommendations into {len(themes)} total themes.")

        sorted_themes = sorted(themes.items(), key=lambda x: len(x[1]), reverse=True)
        target_themes = sorted_themes[:10] 

        playlists_created = 0

        for theme_name, items in target_themes:
            if len(items) < 4:
                logger.warning(f"Skipping theme '{theme_name}' — only contains {len(items)} items (Minimum: 4)")
                continue

            plex_items = []
            for item in items:
                try:
                    plex_obj = user_plex.library.fetchItem(int(item["rating_key"]))
                    
                    # THE GATEWAY EPISODE INTERCEPTOR
                    if getattr(plex_obj, 'type', '') == 'show':
                        episodes = plex_obj.episodes()
                        if episodes:
                            plex_items.append(episodes[0]) # Grabs Season 1, Episode 1
                    else:
                        plex_items.append(plex_obj)
                        
                except Exception as e:
                    logger.error(f"Error fetching item {item.get('rating_key')}: {e}")
                    continue

            if not plex_items:
                continue

            try:
                # Create the playlist
                new_playlist = user_plex.createPlaylist(theme_name, items=plex_items)
                new_playlist.edit(**{"summary": f"{self.tracking_tag} Automated daily curation."})
                
                # GENERATE AND UPLOAD POSTER
                poster_path = f"/tmp/plexai_poster_{playlists_created}.jpg"
                if self.generate_poster(theme_name, poster_path):
                    new_playlist.uploadPoster(filepath=poster_path)
                    try:
                        os.remove(poster_path) # Clean up the temp image off the drive
                    except OSError:
                        pass
                
                logger.info(f"Successfully created playlist: '{theme_name}' with {len(plex_items)} items.")
                playlists_created += 1
            except Exception as e:
                logger.error(f"Failed to create playlist '{theme_name}': {e}")

        logger.info(f"Completed playlist synchronization. Created {playlists_created} playlists for user.")

    def _clear_old_playlists(self, user_plex: PlexServer):
        try:
            all_playlists = user_plex.playlists()
            for playlist in all_playlists:
                summary = getattr(playlist, 'summary', '') or ""
                if self.tracking_tag in summary:
                    logger.info(f"Clearing old recommendation playlist: '{playlist.title}'")
                    playlist.delete()
        except Exception as e:
            logger.error(f"Error while clearing old AI playlists: {e}")


playlist_service = PlaylistService()

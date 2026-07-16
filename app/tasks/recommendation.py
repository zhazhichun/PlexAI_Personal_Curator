import logging
import asyncio
from plexapi.server import PlexServer
from app.config import get_settings
from app.services.ai_service import ai_service
from app.services.playlist_service import playlist_service

logger = logging.getLogger("plexai.recommendation")
settings = get_settings()

# Define your static server details here to bypass the attribute error
PLEX_URL = "http://127.0.0.1:32400"  # Ensure this matches your local Plex container IP/Port
PLEX_TOKEN = getattr(settings, "plex_admin_token", getattr(settings, "plex_token", ""))

async def run_recommendation_for_user(user_obj=None):
    """
    Runs the pipeline for a single user.
    """
    username = getattr(user_obj, "username", "Admin") if user_obj else "Admin"
    token = getattr(user_obj, "plex_token", PLEX_TOKEN)
    
    logger.info(f"Starting dynamic theme pipeline for: {username}")

    try:
        admin_plex = PlexServer(PLEX_URL, PLEX_TOKEN)
        user_plex = PlexServer(PLEX_URL, token)
        
        logger.info("Step 1/3: Collecting user data...")
        watch_history = []
        for item in user_plex.library.recentlyWatched():
            watch_history.append({"rating_key": item.ratingKey, "title": item.title, "year": item.year, "type": item.type})

        available_content = []
        for section in user_plex.library.sections():
            for item in section.unwatched():
                available_content.append({"rating_key": item.ratingKey, "title": item.title, "year": item.year, "type": item.type, "summary": getattr(item, "summary", "")})

        if not watch_history or not available_content:
            return False

        logger.info("Step 2/3: Generating dynamic AI themes...")
        ai_payload = await ai_service.generate_recommendations(
            watch_history=watch_history,
            available_content=available_content,
            movies_count=120,
            shows_count=120
        )

        combined_recs = ai_payload.get("movies", []) + ai_payload.get("shows", [])

        if not combined_recs:
            return False

        logger.info("Step 3/3: Updating Plex thematic playlists...")
        playlist_service.sync_thematic_playlists(admin_plex, token, combined_recs)

        logger.info(f"✅ Pipeline completed for {username}!")
        return True

    except Exception as e:
        logger.error(f"❌ Pipeline failed for {username}: {e}")
        return False

async def run_recommendations_for_all():
    """Triggered by the API."""
    logger.info("Running thematic recommendations...")
    
    active_users = []
    try:
        admin_plex = PlexServer(PLEX_URL, PLEX_TOKEN)
        account = admin_plex.myPlexAccount()
        machine_id = admin_plex.machineIdentifier
        
        active_users.append({"username": account.username or "Admin", "plex_token": PLEX_TOKEN})
        for user in account.users():
            try:
                user_token = user.get_token(machine_id)
                if user_token:
                    active_users.append({"username": user.title or user.username, "plex_token": user_token})
            except: pass
    except Exception as e:
        logger.error(f"Failed to fetch users: {e}")
        active_users = [{"username": "Admin", "plex_token": PLEX_TOKEN}]

    for user_obj in active_users:
        await run_recommendation_for_user(user_obj=user_obj)

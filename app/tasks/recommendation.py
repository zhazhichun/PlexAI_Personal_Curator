import logging
import asyncio
from plexapi.server import PlexServer
from app.config import get_settings
from app.services.ai_service import ai_service
from app.services.playlist_service import playlist_service

logger = logging.getLogger("plexai.recommendation")
settings = get_settings()

async def run_recommendation_for_user(user_id: int = None, user_obj = None):
    """
    Runs the pipeline for a single user, generating 8-10 thematic playlists of 20+ items.
    Accepts either a user_id to query from the DB, or a direct user dictionary/object.
    """
    # Safely extract username and token based on how your DB passes the user object
    username = getattr(user_obj, "username", "Admin") if user_obj else "Unknown"
    token = getattr(user_obj, "plex_token", settings.plex_token) if user_obj else settings.plex_token
    
    logger.info("=" * 50)
    logger.info(f"Starting dynamic theme pipeline for: {username}")
    logger.info("=" * 50)

    try:
        admin_plex = PlexServer(settings.plex_url, settings.plex_token)
        user_plex = PlexServer(settings.plex_url, token)
        
        logger.info("Step 1/3: Collecting user data...")
        watch_history = []
        for item in user_plex.library.recentlyWatched():
            watch_history.append({
                "rating_key": item.ratingKey,
                "title": item.title,
                "year": item.year,
                "type": item.type
            })

        available_content = []
        for section in user_plex.library.sections():
            for item in section.unwatched():
                available_content.append({
                    "rating_key": item.ratingKey,
                    "title": item.title,
                    "year": item.year,
                    "type": item.type,
                    "summary": getattr(item, "summary", "")
                })

        if not watch_history or not available_content:
            logger.warning(f"Skipping {username}: Insufficient watch history or library content.")
            return False

        logger.info("Step 2/3: Generating dynamic AI themes...")
        # Requesting 120 movies and 120 shows to ensure we meet the 8-10 playlists of 20 items requirement
        ai_payload = await ai_service.generate_recommendations(
            watch_history=watch_history,
            available_content=available_content,
            movies_count=120,
            shows_count=120
        )

        combined_recs = ai_payload.get("movies", []) + ai_payload.get("shows", [])

        if not combined_recs:
            logger.error(f"❌ AI returned no recommendations for {username}")
            return False

        logger.info("Step 3/3: Updating Plex thematic playlists...")
        playlist_service.sync_thematic_playlists(
            plex_server=admin_plex,
            user_token=token,
            recommendations=combined_recs
        )

        logger.info(f"✅ Pipeline completed for {username}!")
        return True

    except Exception as e:
        logger.error(f"❌ Pipeline failed for {username}: {e}")
        return False

async def run_recommendations_for_all():
    """Triggered by the scheduler and the API trigger-all route. Runs for all active users."""
    logger.info("Running thematic recommendations for active users")
    
    active_users = []
    
    try:
        # Dynamically import the database dependencies to avoid circular imports
        from app.db.database import SessionLocal
        from app.db import crud
        
        db = SessionLocal()
        all_users = crud.get_users(db)
        active_users = [u for u in all_users if getattr(u, 'is_active', False)]
        db.close()
    except Exception as e:
        logger.warning(f"Failed to query active users from DB: {e}. Falling back to admin token only.")
        # Fallback to process the admin account if DB fails
        active_users = [{"username": "Admin", "plex_token": settings.plex_token}]

    success_count = 0
    for user in active_users:
        result = await run_recommendation_for_user(user_obj=user)
        if result:
            success_count += 1

    logger.info(f"Completed recommendations for {success_count}/{len(active_users)} users")

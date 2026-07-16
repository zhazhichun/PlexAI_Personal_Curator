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
    """
    admin_token = getattr(settings, "plex_admin_token", getattr(settings, "plex_token", ""))
    
    if isinstance(user_obj, dict):
        username = user_obj.get("username", "Admin")
        token = user_obj.get("plex_token", admin_token)
    else:
        username = getattr(user_obj, "username", "Admin") if user_obj else "Admin"
        token = getattr(user_obj, "plex_token", admin_token) if user_obj else admin_token
    
    logger.info("=" * 50)
    logger.info(f"Starting dynamic theme pipeline for: {username}")
    logger.info("=" * 50)

    try:
        admin_plex = PlexServer(settings.plex_url, admin_token)
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
    """Triggered by the scheduler and the API trigger-all route."""
    logger.info("Running thematic recommendations for Plex Home users via native API...")
    
    admin_token = getattr(settings, "plex_admin_token", getattr(settings, "plex_token", ""))
    active_users = []
    
    try:
        admin_plex = PlexServer(settings.plex_url, admin_token)
        account = admin_plex.myPlexAccount()
        machine_id = admin_plex.machineIdentifier
        
        # 1. Append the main admin user
        admin_name = account.username or "Admin"
        active_users.append({"username": admin_name, "plex_token": admin_token})
        logger.info(f"Found admin account: {admin_name}")
        
        # 2. Append all managed/shared home users
        for user in account.users():
            try:
                user_token = user.get_token(machine_id)
                if user_token:
                    display_name = user.title or user.username or "Unknown User"
                    active_users.append({"username": display_name, "plex_token": user_token})
                    logger.info(f"Found home user: {display_name}")
            except Exception as user_err:
                logger.warning(f"Could not retrieve token for a home user: {user_err}")
                
    except Exception as e:
        logger.error(f"Failed to connect to Plex API to fetch users: {e}. Falling back to admin only.")
        active_users = [{"username": "Admin", "plex_token": admin_token}]

    success_count = 0
    for user_obj in active_users:
        result = await run_recommendation_for_user(user_obj=user_obj)
        if result:
            success_count += 1

    logger.info(f"Completed recommendations for {success_count}/{len(active_users)} total users")

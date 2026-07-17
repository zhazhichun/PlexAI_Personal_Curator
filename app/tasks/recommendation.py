import logging
import asyncio
from plexapi.server import PlexServer
from app.config import get_settings
from app.services.ai_service import ai_service
from app.services.playlist_service import playlist_service

logger = logging.getLogger("plexai.recommendation")
settings = get_settings()

# Define your static server details here to bypass the attribute error
PLEX_URL = "http://127.0.0.1:32400"  
PLEX_TOKEN = getattr(settings, "plex_admin_token", getattr(settings, "plex_token", ""))

async def run_recommendation_for_user(user_obj=None):
    """
    Runs the pipeline for a single user.
    """
    # Safely extract the username and token from the dictionary
    username = user_obj.get("username", "Admin") if isinstance(user_obj, dict) else "Admin"
    token = user_obj.get("plex_token", PLEX_TOKEN) if isinstance(user_obj, dict) else PLEX_TOKEN
    
    logger.info("=" * 50)
    logger.info(f"Starting dynamic theme pipeline for: {username}")
    logger.info("=" * 50)

    try:
        admin_plex = PlexServer(PLEX_URL, PLEX_TOKEN)
        user_plex = PlexServer(PLEX_URL, token)
        
        logger.info("Step 1/3: Collecting user data...")
        watch_history = []
        available_content = []
        
        # Iterate through libraries safely instead of using deprecated server-wide methods
        for section in user_plex.library.sections():
            if section.type not in ['movie', 'show']:
                continue
            
            # 1. Grab up to 50 of the most recently watched items per library for context
            watched_items = section.search(unwatched=False, sort='lastViewedAt:desc')[:50]
            for item in watched_items:
                watch_history.append({
                    "rating_key": item.ratingKey, 
                    "title": item.title, 
                    "year": getattr(item, 'year', ''), 
                    "type": item.type
                })
            
            # 2. Grab all unwatched items to build the recommendations pool
            for item in section.search(unwatched=True):
                available_content.append({
                    "rating_key": item.ratingKey, 
                    "title": item.title, 
                    "year": getattr(item, 'year', ''), 
                    "type": item.type, 
                    "summary": getattr(item, "summary", "")
                })

        if not watch_history or not available_content:
            logger.warning(f"Skipping {username}: Insufficient watch history or library content.")
            return False

        logger.info("Step 2/3: Generating dynamic AI themes...")
        
        # REDUCED COUNT: Lowered to 40/40 to prevent the AI from hitting the hard output token limit
        ai_payload = await ai_service.generate_recommendations(
            watch_history=watch_history,
            available_content=available_content,
            movies_count=40,
            shows_count=40
        )

        combined_recs = ai_payload.get("movies", []) + ai_payload.get("shows", [])

        if not combined_recs:
            logger.error(f"❌ AI returned no recommendations for {username}")
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
            except Exception: 
                pass
    except Exception as e:
        logger.error(f"Failed to fetch users: {e}")
        active_users = [{"username": "Admin", "plex_token": PLEX_TOKEN}]

    for user_obj in active_users:
        await run_recommendation_for_user(user_obj=user_obj)

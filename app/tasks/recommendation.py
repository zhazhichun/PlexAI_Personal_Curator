import logging
import asyncio
import random
from plexapi.server import PlexServer
from app.config import get_settings
from app.services.ai_service import ai_service
from app.services.playlist_service import playlist_service

logger = logging.getLogger("plexai.recommendation")
settings = get_settings()

PLEX_URL = "http://127.0.0.1:32400"  
PLEX_TOKEN = getattr(settings, "plex_admin_token", getattr(settings, "plex_token", ""))

async def run_recommendation_for_user(user_obj=None):
    username = user_obj.get("username", "Admin") if isinstance(user_obj, dict) else "Admin"
    token = user_obj.get("plex_token", PLEX_TOKEN) if isinstance(user_obj, dict) else PLEX_TOKEN
    
    logger.info("=" * 50)
    logger.info(f"Starting isolated library pipeline for: {username}")
    logger.info("=" * 50)

    try:
        admin_plex = PlexServer(PLEX_URL, PLEX_TOKEN)
        user_plex = PlexServer(PLEX_URL, token)
        
        combined_recs = []

        # Iterate through each library independently (Movies, TV Shows, etc.)
        for section in user_plex.library.sections():
            if section.type not in ['movie', 'show']:
                continue
                
            logger.info(f"--- Processing Library: {section.title} ({section.type}) ---")
            
            watch_history = []
            available_content = []
            
            # FIX: Use libtype=section.type to force Plex to return SHOWS, not individual episodes
            watched_items = section.search(unwatched=False, sort='lastViewedAt:desc', libtype=section.type)[:50]
            for item in watched_items:
                watch_history.append({
                    "rating_key": item.ratingKey, 
                    "title": item.title, 
                    "year": getattr(item, 'year', ''), 
                    "type": section.type
                })
            
            # Fetch unwatched, bounded to 800 per library to ensure lightning-fast AI reading
            unwatched_items = section.search(unwatched=True, libtype=section.type)
            if len(unwatched_items) > 800:
                unwatched_items = random.sample(unwatched_items, 800)
                
            for item in unwatched_items:
                available_content.append({
                    "rating_key": item.ratingKey, 
                    "title": item.title, 
                    "year": getattr(item, 'year', ''), 
                    "type": section.type
                })

            if not watch_history or not available_content:
                logger.warning(f"Skipping {section.title}: Insufficient history or unwatched content.")
                continue

            logger.info(f"Requesting AI curation for {section.title}...")
            
            # Request AI recommendations isolated to this specific media type
            ai_payload = await ai_service.generate_recommendations(
                media_type=section.type,
                watch_history=watch_history,
                available_content=available_content
            )

            if ai_payload:
                combined_recs.extend(ai_payload)

        if not combined_recs:
            logger.error(f"❌ AI returned no recommendations across any libraries for {username}")
            return False

        logger.info("Step 3/3: Updating Plex thematic playlists...")
        # Sync all generated playlists at once
        playlist_service.sync_thematic_playlists(admin_plex, token, combined_recs)

        logger.info(f"✅ Pipeline completed for {username}!")
        return True

    except Exception as e:
        logger.error(f"❌ Pipeline failed for {username}: {e}")
        return False

async def run_recommendations_for_all():
    logger.info("Running isolated library recommendations...")
    
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

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

    logger.info(f"Completed recommendations for {success_count}/{len(active_users)} users")                        f"Filtered recommendation pool by libraries {allowed_ids}: "
                        f"{len(all_content)} -> {len(recommendation_content)}"
                    )

            # content_map used for available_content filtering (recommendations only)
            content_map = {item["rating_key"]: item for item in recommendation_content}

            # === BUILD WATCH HISTORY from allowed libraries (Plex data, no limit) ===
            # We build watch history directly from recommendation_content using Plex's
            # view_count / viewed_leaf_count fields — this way:
            #   - Only allowed libraries are included
            #   - Shows appear once at show-level (with show summary), not per-episode
            #   - No artificial limit on number of items
            # Tautulli is still used below to build watched_keys for available_content filtering.
            watch_history = []
            for item in recommendation_content:
                if item["type"] == "movie" and item.get("view_count", 0) > 0:
                    watch_history.append(item)
                elif item["type"] == "show" and item.get("viewed_leaf_count", 0) > 0:
                    watch_history.append(item)

            logger.info(
                f"Built watch history from Plex data: "
                f"{sum(1 for i in watch_history if i['type'] == 'movie')} movies + "
                f"{sum(1 for i in watch_history if i['type'] == 'show')} shows"
            )

            # Get watched_keys via Tautulli — used only for available_content filtering
            watched_keys = set()
            tautulli_user = await tautulli_service.get_user_by_plex_id(user.plex_user_id)
            if tautulli_user:
                raw_history = await tautulli_service.get_user_watch_history(
                    tautulli_user["user_id"], length=5000
                )
                for h in raw_history:
                    key = h.get("grandparent_rating_key") or h.get("rating_key")
                    if key:
                        watched_keys.add(key)
            else:
                logger.warning(f"User {user.plex_username} not found in Tautulli, "
                              "trying Plex API directly")
                watched_keys = await plex_service.get_watched_items(user.plex_token)

            # Filter available content (not watched) — from allowed libraries only
            available_content = []
            excluded_count = 0
            for item in recommendation_content:
                key = item["rating_key"]
                is_watched = False

                # Check Tautulli history
                if key in watched_keys:
                    is_watched = True

                # Check Plex watched status (view_count > 0 means watched)
                elif item.get("view_count", 0) > 0:
                    is_watched = True

                # Check partial watched status for shows (viewed_leaf_count > 0 means at least one episode watched)
                elif item["type"] == "show" and item.get("viewed_leaf_count", 0) > 0:
                    is_watched = True

                if not is_watched:
                    available_content.append(item)
                else:
                    excluded_count += 1

            logger.info(f"Excluded {excluded_count} items (watched or partially watched)")

            logger.info(
                f"Data: {len(watch_history)} watched, "
                f"{len(available_content)} available, "
                f"{len(recommendation_content)} total in allowed libraries"
            )


            # === STEP 2: FEEDBACK ANALYSIS ===
            logger.info("Step 2/5: Analyzing feedback...")

            # Update watched status for past recommendations
            await feedback_service.update_watched_status(db, user.id, watched_keys)

            # Get past recommendations for context
            past_recs = await feedback_service.get_past_recommendations(db, user.id)

            # === STEP 3: AI BRAIN ===
            logger.info("Step 3/5: Generating AI recommendations...")

            result = await ai_service.generate_recommendations(
                watch_history=watch_history,
                available_content=available_content,
                past_recommendations=past_recs,
            )

            movie_recs = result.get("movies", [])
            show_recs = result.get("shows", [])
            all_recs = movie_recs + show_recs

            if not all_recs:
                raise Exception("AI returned no recommendations")

            # Enrich recommendations with library info
            for rec in all_recs:
                original_item = content_map.get(rec["rating_key"])
                if original_item:
                    rec["library"] = original_item.get("library", "Unknown")
                else:
                    logger.warning(f"Recommended item {rec['rating_key']} not found in content map")

            logger.info(f"AI recommended {len(movie_recs)} movies + {len(show_recs)} shows:")
            for i, rec in enumerate(movie_recs, 1):
                logger.info(f"  🎬 {i}. {rec['title']} (Library: {rec.get('library')}) - {rec.get('reason', '')}")
            for i, rec in enumerate(show_recs, 1):
                logger.info(f"  📺 {i}. {rec['title']} (Library: {rec.get('library')}) - {rec.get('reason', '')}")

            # === STEP 4: EXECUTOR ===
            logger.info("Step 4/5: Updating playlists...")

            await playlist_service.update_user_playlists(
                user_token=user.plex_token,
                movie_recommendations=movie_recs,
                show_recommendations=show_recs,
                username=user.plex_username,
            )

            # === STEP 5: SAVE & LOG ===
            logger.info("Step 5/5: Saving results...")

            await feedback_service.save_recommendations(db, user.id, all_recs)

            # Update run record
            run.status = RunStatus.SUCCESS
            run.items_count = len(all_recs)
            run.completed_at = datetime.datetime.utcnow()
            await db.commit()

            logger.info(f"✅ Pipeline completed for {user.plex_username}!")

        except Exception as e:
            logger.error(f"❌ Pipeline failed for {user.plex_username}: {e}")
            run.status = RunStatus.FAILED
            run.error_message = str(e)
            run.completed_at = datetime.datetime.utcnow()
            await db.commit()
            raise


async def run_recommendations_for_all() -> int:
    """Run recommendation pipeline for all active users.

    Returns the number of users processed.
    """
    async with async_session() as db:
        stmt = select(User).where(User.is_active == True)  # noqa: E712
        result = await db.execute(stmt)
        users = result.scalars().all()

    if not users:
        logger.info("No active users found")
        return 0

    logger.info(f"Running recommendations for {len(users)} active users")

    count = 0
    for user in users:
        try:
            await run_recommendation_for_user(user.id)
            count += 1
            # Small delay between users to avoid API rate limits
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Failed for user {user.plex_username}: {e}")
            continue

    logger.info(f"Completed recommendations for {count}/{len(users)} users")
    return count

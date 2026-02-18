import asyncio
import datetime
import logging
from sqlalchemy import select

from app.database import async_session
from app.models import User, RecommendationRun, RunStatus
from app.services.plex_service import plex_service
from app.services.tautulli_service import tautulli_service
from app.services.ai_service import ai_service
from app.services.playlist_service import playlist_service
from app.services.feedback_service import feedback_service

logger = logging.getLogger("plexai.recommendation")


async def run_recommendation_for_user(user_id: int):
    """Run the full recommendation pipeline for a single user.

    Pipeline steps:
    1. DATA MINING - Collect watch history + available content
    2. FEEDBACK - Analyze past recommendations
    3. AI BRAIN - Generate new recommendations
    4. EXECUTOR - Update the user's playlist
    5. LOGGING - Save everything to DB
    """
    async with async_session() as db:
        # Load user
        stmt = select(User).where(User.id == user_id, User.is_active == True)  # noqa: E712
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            logger.warning(f"User {user_id} not found or inactive, skipping")
            return

        # Create run record
        run = RecommendationRun(user_id=user.id)
        db.add(run)
        await db.flush()

        try:
            logger.info(f"{'='*50}")
            logger.info(f"Starting recommendation pipeline for: {user.plex_username}")
            logger.info(f"{'='*50}")

            # === STEP 1: DATA MINING ===
            logger.info("Step 1/5: Collecting data...")

            # Get all content from libraries shared with this user
            # Using user's token ensures we only see libraries they have access to
            all_content = await plex_service.get_all_content(user.plex_token)

            # full_content_map includes ALL libraries — used to build watch history
            # so the AI understands the user's full taste, even from libraries we
            # don't recommend from (e.g. Flight, Anime, etc.)
            full_content_map = {item["rating_key"]: item for item in all_content}

            # Filter by ALLOWED_LIBRARIES if configured — only for recommendations
            from app.config import get_settings
            settings = get_settings()

            recommendation_content = all_content
            if settings.allowed_libraries:
                allowed_ids = [lid.strip() for lid in settings.allowed_libraries.split(",") if lid.strip()]
                if allowed_ids:
                    recommendation_content = [
                        item for item in all_content
                        if item.get("library_id") in allowed_ids
                    ]
                    logger.info(
                        f"Filtered recommendation pool by libraries {allowed_ids}: "
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

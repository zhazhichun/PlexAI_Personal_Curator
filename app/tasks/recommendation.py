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

            # Get all content from the Plex server (using admin token)
            all_content = await plex_service.get_all_content()
            content_map = {item["rating_key"]: item for item in all_content}

            # Get watched items via Tautulli (primary source)
            watched_keys = set()
            watch_history = []
            tautulli_user = await tautulli_service.get_user_by_plex_id(user.plex_user_id)
            if tautulli_user:
                raw_history = await tautulli_service.get_user_watch_history(
                    tautulli_user["user_id"], length=200
                )
                for h in raw_history:
                    # For episodes, use the show's rating key
                    key = h.get("grandparent_rating_key") or h.get("rating_key")
                    if key:
                        watched_keys.add(key)
                    if key in content_map:
                        watch_history.append(content_map[key])
            else:
                logger.warning(f"User {user.plex_username} not found in Tautulli, "
                              "trying Plex API directly with admin token")
                # Fallback: use admin token to check watched status
                watched_keys = await plex_service.get_watched_items()

            # Deduplicate watch history
            seen = set()
            unique_history = []
            for item in watch_history:
                if item["rating_key"] not in seen:
                    seen.add(item["rating_key"])
                    unique_history.append(item)
            watch_history = unique_history

            # Filter available content (not watched)
            available_content = [
                item for item in all_content
                if item["rating_key"] not in watched_keys
            ]

            logger.info(
                f"Data: {len(watch_history)} watched, "
                f"{len(available_content)} available, "
                f"{len(all_content)} total"
            )

            # === STEP 2: FEEDBACK ANALYSIS ===
            logger.info("Step 2/5: Analyzing feedback...")

            # Update watched status for past recommendations
            await feedback_service.update_watched_status(db, user.id, watched_keys)

            # Get past recommendations for context
            past_recs = await feedback_service.get_past_recommendations(db, user.id)

            # === STEP 3: AI BRAIN ===
            logger.info("Step 3/5: Generating AI recommendations...")

            recommendations = await ai_service.generate_recommendations(
                watch_history=watch_history,
                available_content=available_content,
                past_recommendations=past_recs,
            )

            if not recommendations:
                raise Exception("AI returned no recommendations")

            logger.info(f"AI recommended {len(recommendations)} items:")
            for i, rec in enumerate(recommendations, 1):
                logger.info(f"  {i}. {rec['title']} ({rec['type']}) - {rec.get('reason', '')}")

            # === STEP 4: EXECUTOR ===
            logger.info("Step 4/5: Updating playlist...")

            await playlist_service.update_user_playlist(
                user_token=user.plex_token,
                recommendations=recommendations,
            )

            # === STEP 5: SAVE & LOG ===
            logger.info("Step 5/5: Saving results...")

            await feedback_service.save_recommendations(db, user.id, recommendations)

            # Update run record
            run.status = RunStatus.SUCCESS
            run.items_count = len(recommendations)
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

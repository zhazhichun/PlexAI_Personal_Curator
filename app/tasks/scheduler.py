import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings

logger = logging.getLogger("plexai.scheduler")
settings = get_settings()

scheduler = AsyncIOScheduler()


async def scheduled_recommendations_job():
    """Job that runs periodically to update all users' playlists."""
    from app.tasks.recommendation import run_recommendations_for_all

    logger.info("🕐 Scheduled recommendation job started")
    try:
        count = await run_recommendations_for_all()
        logger.info(f"🕐 Scheduled job completed: {count} users updated")
    except Exception as e:
        logger.error(f"🕐 Scheduled job failed: {e}")


def start_scheduler():
    """Start the APScheduler with the configured cron schedule."""
    if not settings.enable_scheduler:
        logger.info("🚫 Scheduler disabled by configuration")
        return

    scheduler.add_job(
        scheduled_recommendations_job,
        trigger=CronTrigger(
            hour=settings.recommendation_hour,
            minute=settings.recommendation_minute,
        ),
        id="daily_recommendations",
        name="Daily AI Recommendations",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        f"Scheduler started - recommendations will run daily at "
        f"{settings.recommendation_hour:02d}:{settings.recommendation_minute:02d}"
    )


def shutdown_scheduler():
    """Gracefully shut down the scheduler."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler shut down")

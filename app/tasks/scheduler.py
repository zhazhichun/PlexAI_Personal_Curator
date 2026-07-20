import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings

logger = logging.getLogger("plexai.scheduler")
settings = get_settings()

scheduler = AsyncIOScheduler()

async def scheduled_recommendations_job():
    from app.tasks.recommendation import run_recommendations_for_all

    logger.info("🕐 Scheduled recommendation job started")
    try:
        await run_recommendations_for_all()
        logger.info("🕐 Scheduled job completed")
    except Exception as e:
        logger.error(f"🕐 Scheduled job failed: {e}")

def start_scheduler():
    if not settings.enable_scheduler:
        logger.info("🚫 Scheduler disabled by configuration")
        return

    scheduler.add_job(
        scheduled_recommendations_job,
        trigger=CronTrigger(
            day_of_week="sun,wed",
            hour=settings.recommendation_hour,
            minute=settings.recommendation_minute,
        ),
        id="biweekly_recommendations",
        name="Biweekly AI Recommendations",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        f"Scheduler started - recommendations will run Wed and Sun at "
        f"{settings.recommendation_hour:02d}:{settings.recommendation_minute:02d}"
    )

def shutdown_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler shut down")

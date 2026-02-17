import logging
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Recommendation

logger = logging.getLogger("plexai.feedback")


class FeedbackService:
    """Service for analyzing user feedback on recommendations.

    Feedback is derived from:
    - Items that were recommended but NOT watched → negative signal
    - Items that were recommended and THEN watched → positive signal
    - Items that were removed from the playlist → strong negative signal
    """

    async def get_past_recommendations(
        self, db: AsyncSession, user_id: int, limit: int = 50
    ) -> list[dict]:
        """Get past recommendations for analysis."""
        stmt = (
            select(Recommendation)
            .where(Recommendation.user_id == user_id)
            .order_by(Recommendation.recommended_at.desc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        recs = result.scalars().all()

        return [
            {
                "rating_key": rec.plex_rating_key,
                "title": rec.title,
                "media_type": rec.media_type.value,
                "was_watched": rec.was_watched,
                "was_removed": rec.was_removed,
            }
            for rec in recs
        ]

    async def save_recommendations(
        self, db: AsyncSession, user_id: int, recommendations: list[dict]
    ):
        """Save new recommendations to the database for future feedback analysis."""
        from app.models import MediaType

        for rec in recommendations:
            media_type = MediaType.SHOW if rec.get("type") == "show" else MediaType.MOVIE
            db_rec = Recommendation(
                user_id=user_id,
                media_type=media_type,
                plex_rating_key=rec["rating_key"],
                title=rec["title"],
            )
            db.add(db_rec)
        await db.flush()
        logger.info(f"Saved {len(recommendations)} recommendations for user {user_id}")

    async def update_watched_status(
        self, db: AsyncSession, user_id: int, watched_keys: set[str]
    ):
        """Mark recommendations as watched based on the user's current watch status."""
        stmt = select(Recommendation).where(
            and_(
                Recommendation.user_id == user_id,
                Recommendation.was_watched == False,  # noqa: E712
            )
        )
        result = await db.execute(stmt)
        recs = result.scalars().all()

        updated = 0
        for rec in recs:
            if rec.plex_rating_key in watched_keys:
                rec.was_watched = True
                updated += 1

        if updated > 0:
            await db.flush()
            logger.info(f"Marked {updated} recommendations as watched for user {user_id}")

    async def get_negative_signals(self, db: AsyncSession, user_id: int) -> list[str]:
        """Get rating keys of items that were recommended but rejected/ignored."""
        stmt = select(Recommendation.plex_rating_key).where(
            and_(
                Recommendation.user_id == user_id,
                Recommendation.was_watched == False,  # noqa: E712
                Recommendation.was_removed == False,  # noqa: E712
            )
        )
        result = await db.execute(stmt)
        return [row[0] for row in result.all()]


# Singleton
feedback_service = FeedbackService()

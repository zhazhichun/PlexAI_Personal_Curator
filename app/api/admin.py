import logging
from fastapi import APIRouter, HTTPException, Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import User, RecommendationRun
from app.tasks.recommendation import run_recommendation_for_user, run_recommendations_for_all

logger = logging.getLogger("plexai.admin")
router = APIRouter()
settings = get_settings()


async def verify_admin(x_admin_password: str = Header(None)):
    """Verify admin password from request header."""
    if x_admin_password != settings.admin_password:
        raise HTTPException(status_code=401, detail="Invalid admin password")
    return True


@router.get("/users")
async def get_users(
    db: AsyncSession = Depends(get_db),
    _admin: bool = Depends(verify_admin),
):
    """Get all registered users with their status."""
    stmt = select(User).order_by(User.created_at.desc())
    result = await db.execute(stmt)
    users = result.scalars().all()

    user_list = []
    for user in users:
        # Get latest run for this user
        run_stmt = (
            select(RecommendationRun)
            .where(RecommendationRun.user_id == user.id)
            .order_by(RecommendationRun.started_at.desc())
            .limit(1)
        )
        run_result = await db.execute(run_stmt)
        latest_run = run_result.scalar_one_or_none()

        user_list.append({
            "id": user.id,
            "username": user.plex_username,
            "email": user.plex_email,
            "is_active": user.is_active,
            "enable_recommendations": user.enable_recommendations,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "latest_run": {
                "status": latest_run.status.value if latest_run else None,
                "started_at": latest_run.started_at.isoformat() if latest_run else None,
                "completed_at": latest_run.completed_at.isoformat() if latest_run and latest_run.completed_at else None,
                "items_count": latest_run.items_count if latest_run else 0,
                "error": latest_run.error_message if latest_run else None,
            } if latest_run else None,
        })

    return {"users": user_list, "total": len(user_list)}


@router.get("/runs")
async def get_runs(
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    _admin: bool = Depends(verify_admin),
):
    """Get recent recommendation runs."""
    stmt = (
        select(RecommendationRun)
        .order_by(RecommendationRun.started_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    runs = result.scalars().all()

    # Get usernames
    user_ids = {run.user_id for run in runs}
    user_stmt = select(User).where(User.id.in_(user_ids))
    user_result = await db.execute(user_stmt)
    users_map = {u.id: u.plex_username for u in user_result.scalars().all()}

    run_list = []
    for run in runs:
        run_list.append({
            "id": run.id,
            "user_id": run.user_id,
            "username": users_map.get(run.user_id, "Unknown"),
            "status": run.status.value,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "items_count": run.items_count,
            "error": run.error_message,
        })

    return {"runs": run_list}


@router.post("/trigger/{user_id}")
async def trigger_user_recommendation(
    user_id: int,
    _admin: bool = Depends(verify_admin),
):
    """Trigger recommendation pipeline for a specific user."""
    try:
        await run_recommendation_for_user(user_id)
        return {"status": "success", "message": f"Recommendation triggered for user {user_id}"}
    except Exception as e:
        logger.error(f"Failed to trigger recommendation for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/trigger-all")
async def trigger_all_recommendations(
    _admin: bool = Depends(verify_admin),
):
    """Trigger recommendation pipeline for all active users."""
    try:
        count = await run_recommendations_for_all()
        return {"status": "success", "message": f"Recommendations triggered for {count} users"}
    except Exception as e:
        logger.error(f"Failed to trigger recommendations: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    _admin: bool = Depends(verify_admin),
):
    """Deactivate a user (soft delete)."""
    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_active = False
    await db.flush()
    logger.info(f"Deactivated user: {user.plex_username}")
    return {"status": "success", "message": f"User {user.plex_username} deactivated"}


@router.patch("/users/{user_id}/toggle-recommendations")
async def toggle_recommendations(
    user_id: int,
    enable: bool,
    db: AsyncSession = Depends(get_db),
    _admin: bool = Depends(verify_admin),
):
    """Enable or disable AI recommendations for a user."""
    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.enable_recommendations = enable
    await db.commit()
    
    status = "enabled" if enable else "disabled"
    logger.info(f"Recommendations {status} for user: {user.plex_username}")
    return {"status": "success", "message": f"Recommendations {status} for {user.plex_username}"}

import logging
from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import User

logger = logging.getLogger("plexai.users")
router = APIRouter()


@router.get("/count")
async def get_user_count(db: AsyncSession = Depends(get_db)):
    """Get total registered user count (public endpoint for dashboard)."""
    stmt = select(func.count(User.id)).where(User.is_active == True)  # noqa: E712
    result = await db.execute(stmt)
    count = result.scalar()
    return {"total_users": count}

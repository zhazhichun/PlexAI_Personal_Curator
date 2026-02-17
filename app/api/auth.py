import logging
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.services.plex_service import plex_service
from app.database import async_session
from app.models import User
from sqlalchemy import select

logger = logging.getLogger("plexai.auth")
router = APIRouter()

# In-memory store for pending PINs (short-lived)
pending_pins: dict[int, str] = {}


@router.get("/login")
async def start_login():
    """Start the Plex OAuth login flow. Returns the auth URL for the user."""
    try:
        pin_data = await plex_service.get_pin()
        pin_id = pin_data["id"]
        pin_code = pin_data["code"]

        # Store the pin for later verification
        pending_pins[pin_id] = pin_code

        auth_url = plex_service.get_auth_url(pin_code)
        return {
            "pin_id": pin_id,
            "auth_url": auth_url,
        }
    except Exception as e:
        logger.error(f"Failed to start login: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/callback/{pin_id}")
async def check_auth(pin_id: int):
    """Check if the user has authorized the app via Plex OAuth."""
    try:
        token = await plex_service.check_pin(pin_id)

        if not token:
            return {"status": "pending", "message": "Waiting for user to authorize..."}

        # Get user info from Plex
        user_info = await plex_service.get_user_info(token)

        # Save or update user in database
        async with async_session() as db:
            stmt = select(User).where(User.plex_user_id == user_info["id"])
            result = await db.execute(stmt)
            user = result.scalar_one_or_none()

            if user:
                # Update existing user's token
                user.plex_token = token
                user.plex_username = user_info["username"]
                user.plex_email = user_info["email"]
                user.is_active = True
                logger.info(f"Updated existing user: {user_info['username']}")
            else:
                # Create new user
                user = User(
                    plex_username=user_info["username"],
                    plex_email=user_info["email"],
                    plex_token=token,
                    plex_user_id=user_info["id"],
                )
                db.add(user)
                logger.info(f"Created new user: {user_info['username']}")

            await db.commit()

        # Clean up pending pin
        pending_pins.pop(pin_id, None)

        return {
            "status": "success",
            "username": user_info["username"],
            "message": f"Welcome, {user_info['username']}! Your account is now connected.",
        }

    except Exception as e:
        logger.error(f"Auth callback error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def auth_status():
    """Get general auth service status."""
    return {
        "status": "ready",
        "pending_logins": len(pending_pins),
    }

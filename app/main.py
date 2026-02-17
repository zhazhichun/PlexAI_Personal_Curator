import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.database import init_db
from app.api import auth, admin, users
from app.tasks.scheduler import start_scheduler, shutdown_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("plexai")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("🚀 PlexAI Personal Curator starting up...")
    await init_db()
    logger.info("✅ Database initialized")
    start_scheduler()
    logger.info("✅ Scheduler started")
    yield
    shutdown_scheduler()
    logger.info("👋 PlexAI Personal Curator shutting down...")


app = FastAPI(
    title="PlexAI Personal Curator",
    description="AI-powered personal playlist recommendations for Plex",
    version="1.0.0",
    lifespan=lifespan,
)

# API Routes
app.include_router(auth.router, prefix="/auth", tags=["Authentication"])
app.include_router(admin.router, prefix="/api/admin", tags=["Admin"])
app.include_router(users.router, prefix="/api/users", tags=["Users"])

# Static files (frontend)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
# Serve admin dashboard
app.mount("/admin", StaticFiles(directory="app/static/admin", html=True), name="admin")


@app.get("/")
async def root():
    """Redirect to the login page."""
    from fastapi.responses import FileResponse
    return FileResponse("app/static/index.html")


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "PlexAI Personal Curator"}

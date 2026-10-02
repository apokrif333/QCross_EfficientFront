from fastapi import APIRouter

from app.api.routes import frontier, health, instruments, returns

router = APIRouter()
router.include_router(health.router)
router.include_router(instruments.router)
router.include_router(returns.router)
router.include_router(frontier.router)

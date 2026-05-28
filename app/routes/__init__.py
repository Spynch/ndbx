from fastapi import APIRouter

from app.routes.auth import router as auth_router
from app.routes.events import router as events_router
from app.routes.health_session import router as health_session_router
from app.routes.recommendations import router as recommendations_router
from app.routes.reviews import router as reviews_router
from app.routes.users import router as users_router

router = APIRouter()
router.include_router(health_session_router)
router.include_router(auth_router)
router.include_router(events_router)
router.include_router(recommendations_router)
router.include_router(reviews_router)
router.include_router(users_router)

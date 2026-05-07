"""
FastAPI app entry. Mounts auth + future feature routers and configures CORS.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.auth.routes import router as auth_router
from app.venues.routes import router as venues_router


app = FastAPI(
    title="Happy Hour API",
    version="0.1.0",
    description="Backend for the Happy Hour iOS app.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}


app.include_router(auth_router)
app.include_router(venues_router)

# Routers added in subsequent halves of week 2:
#   /outings       -> app.outings.routes
#   /friends       -> app.friends.routes
#   /devices       -> app.notifications.routes

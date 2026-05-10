from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import os

import api as api_module
from database import init_db
from news_service import ensure_sources_seeded, scheduler

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"


def create_app() -> FastAPI:
    app = FastAPI(title="思源 API")

    @app.on_event("startup")
    async def _startup_init_db():
        init_db()
        ensure_sources_seeded()
        scheduler.start()

        # Reset stale ingestion jobs from a previous server session
        from database import SessionLocal
        from models import IngestionJob
        from news_service import now_utc
        db = SessionLocal()
        try:
            stale = db.query(IngestionJob).filter(IngestionJob.status == "running").all()
            for job in stale:
                job.status = "failed"
                job.error_message = "服务重启，任务中断"
                job.finished_at = now_utc()
            if stale:
                db.commit()
        finally:
            db.close()

    @app.on_event("shutdown")
    async def _shutdown_scheduler():
        await scheduler.stop()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # No-cache middleware for development
    @app.middleware("http")
    async def _no_cache(request, call_next):
        response = await call_next(request)
        path = request.url.path or ""
        if path == "/" or path.endswith((".html", ".js", ".css")):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    app.include_router(api_module.router)

    # serve frontend static files at root
    if FRONTEND_DIR.exists():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="static")

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", 8000)))

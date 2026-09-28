"""Background worker: polls sources, classifies items, refines preference profiles."""

import asyncio
import logging
import signal
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import get_settings
from app.db import SessionLocal, engine
from app.llm.ollama_pull import ensure_models
from app.pipeline.classify import classify_all
from app.pipeline.ingest import Fetchers, poll_due_sources, refresh_comments
from app.pipeline.profile import refine_due

log = logging.getLogger("feedler.worker")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    settings = get_settings()
    fetchers = Fetchers.create(settings)
    if settings.provider_configured("ollama"):
        await ensure_models(settings.ollama_base_url, {settings.ollama_model, settings.ollama_profile_model})
    if not settings.reddit_oauth:
        log.warning("No Reddit API credentials: using public endpoints, comments disabled")

    # Classification runs right after each poll (one job, so batches are never scored twice)
    async def ingest_then_classify():
        await poll_due_sources(SessionLocal, fetchers, settings)
        await classify_all(SessionLocal, settings)

    scheduler = AsyncIOScheduler(job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300})
    scheduler.add_job(ingest_then_classify, "interval", minutes=1, id="ingest", next_run_time=datetime.now(UTC))
    scheduler.add_job(refresh_comments, "interval", minutes=20, args=[SessionLocal, fetchers, settings], id="comments")
    scheduler.add_job(refine_due, "interval", minutes=10, args=[SessionLocal, settings], id="refine")
    # Nightly pass for anyone with at least one unprocessed vote
    scheduler.add_job(refine_due, "cron", hour=3, args=[SessionLocal, settings, 1], id="refine_nightly")
    scheduler.start()
    log.info("worker started")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()

    scheduler.shutdown(wait=False)
    await fetchers.aclose()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

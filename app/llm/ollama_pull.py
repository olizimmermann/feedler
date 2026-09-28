"""Download Ollama models on demand, so a configured model 'just works'."""

import asyncio
import logging

import httpx

log = logging.getLogger(__name__)

_pulls: dict[str, asyncio.Task] = {}


def native_base(base_url: str) -> str:
    """Ollama's native API lives next to the OpenAI-compatible /v1 one."""
    return base_url.rstrip("/").removesuffix("/v1")


async def is_installed(base_url: str, model: str) -> bool:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(f"{native_base(base_url)}/api/show", json={"model": model})
        return resp.status_code == 200


async def pull_model(base_url: str, model: str) -> None:
    log.info("downloading Ollama model %s (this can take a while)", model)
    async with httpx.AsyncClient(timeout=httpx.Timeout(10, read=None)) as client:
        resp = await client.post(f"{native_base(base_url)}/api/pull", json={"model": model, "stream": False})
        if resp.status_code != 200:
            raise RuntimeError(f"pull of {model} failed: {resp.status_code} {resp.text[:300]}")
    log.info("Ollama model %s is ready", model)


def pull_in_background(base_url: str, model: str) -> bool:
    """Start a download unless one is already running. Returns True if a new one started."""
    task = _pulls.get(model)
    if task and not task.done():
        return False

    def _done(t: asyncio.Task) -> None:
        if not t.cancelled() and t.exception():
            log.error("Ollama model download failed: %s", t.exception())

    task = asyncio.get_running_loop().create_task(pull_model(base_url, model))
    task.add_done_callback(_done)
    _pulls[model] = task
    return True


async def ensure_models(base_url: str, models: set[str]) -> None:
    """Called at worker start: download any configured model that isn't installed yet."""
    for model in sorted(m for m in models if m):
        try:
            if not await is_installed(base_url, model):
                pull_in_background(base_url, model)
        except httpx.HTTPError as e:
            log.warning("Ollama at %s not reachable (%s); is the ollama profile running?", base_url, e)
            return

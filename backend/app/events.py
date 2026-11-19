"""Server-Sent Events: job updates pushed to the UI instead of polling.

Stream format (text/event-stream):
    event: snapshot   data: [Job, ...]     first message (all jobs, or the one job)
    event: job        data: Job            a job changed (latest state; rapid changes are coalesced)
    event: deleted    data: {"id": ...}    a job was deleted
    : ping                                 keep-alive comment every HEARTBEAT seconds
"""
import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable

from .jobs import JobStore
from .models import Job

HEARTBEAT = 15.0  # seconds between keep-alives; also how soon a closed tab frees its waiting thread
MIN_GAP = 0.2  # coalesce progress ticks: at most ~5 messages/s per stream
RETRY_MS = 2000  # browser reconnect delay after the API restarts


def _msg(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def _dump(job: Job) -> dict:
    return job.model_dump(mode="json")


async def job_events(store: JobStore, disconnected: Callable[[], Awaitable[bool]],
                     job_id: str | None = None) -> AsyncIterator[str]:
    """Yields SSE messages until the client goes away. job_id=None streams every job."""
    def wanted(i: str) -> bool:
        return job_id is None or i == job_id

    version = store.version  # read before the snapshot: a change in between is sent again, never lost
    jobs = store.list() if job_id is None else [j for j in [store.get(job_id)] if j]
    yield f"retry: {RETRY_MS}\n" + _msg("snapshot", [_dump(j) for j in jobs])
    while not await disconnected():
        # wait in a worker thread: the store is updated from the (non-async) pipeline thread
        new, changed, gone = await asyncio.to_thread(store.changes, version, HEARTBEAT)
        if new == version:
            yield ": ping\n\n"
            continue
        version = new
        for j in changed:
            if wanted(j.id):
                yield _msg("job", _dump(j))
        for i in gone:
            if wanted(i):
                yield _msg("deleted", {"id": i})
        await asyncio.sleep(MIN_GAP)

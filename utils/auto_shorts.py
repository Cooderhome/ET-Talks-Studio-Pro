"""
utils/auto_shorts.py - Batch render manager for auto-generated shorts.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Generator

from utils.helpers import new_job_id
from utils.renderer import RenderJob, Renderer, register_job

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[dict[str, Any]], None]

@dataclass
class BatchRenderJob:
    """Manages a batch of RenderJobs to process sequentially."""
    batch_id: str = field(default_factory=new_job_id)
    jobs: list[RenderJob] = field(default_factory=list)
    status: str = "pending" # pending | running | done | error
    current_job_index: int = 0
    error_message: str = ""
    _callbacks: list[ProgressCallback] = field(default_factory=list, repr=False)
    
    def add_callback(self, cb: ProgressCallback) -> None:
        self._callbacks.append(cb)
        
    def _emit(self, data: dict[str, Any]) -> None:
        for cb in self._callbacks:
            try:
                cb(data)
            except Exception:
                pass

# Global registry for batch jobs (similar to RenderJob registry)
_batch_registry: dict[str, BatchRenderJob] = {}
_registry_lock = threading.Lock()

def register_batch(batch: BatchRenderJob) -> None:
    with _registry_lock:
        _batch_registry[batch.batch_id] = batch

def get_batch(batch_id: str) -> BatchRenderJob | None:
    with _registry_lock:
        return _batch_registry.get(batch_id)

def _run_batch_sync(batch: BatchRenderJob) -> None:
    batch.status = "running"
    batch._emit({"type": "batch_status", "batch_id": batch.batch_id, "status": "running", "total_jobs": len(batch.jobs)})
    
    renderer = Renderer()
    
    for i, job in enumerate(batch.jobs):
        batch.current_job_index = i
        batch._emit({
            "type": "batch_progress",
            "batch_id": batch.batch_id,
            "current_index": i,
            "total_jobs": len(batch.jobs),
            "current_job_id": job.job_id,
            "current_job_title": job.title
        })
        
        # Forward individual job progress up to the batch listeners
        def on_job_progress(data: dict[str, Any], _job_idx=i) -> None:
            batch._emit({
                "type": "job_event",
                "batch_id": batch.batch_id,
                "job_index": _job_idx,
                "event": data
            })
            
        job.add_callback(on_job_progress)
        
        try:
            renderer.run(job)
        except Exception as e:
            logger.error(f"Batch {batch.batch_id} failed on job {job.job_id}: {e}")
            # If one job fails, we can either fail the whole batch or continue. We'll fail the batch for now.
            batch.status = "error"
            batch.error_message = f"Failed on clip '{job.title}': {e}"
            batch._emit({"type": "batch_error", "batch_id": batch.batch_id, "message": batch.error_message})
            return
            
    batch.status = "done"
    batch._emit({"type": "batch_done", "batch_id": batch.batch_id, "status": "done"})

def batch_progress_generator(batch: BatchRenderJob) -> Generator[str, None, None]:
    """Yield SSE-formatted progress events for the batch render."""
    import config
    event_queue: queue.Queue[dict | None] = queue.Queue()

    def on_event(data: dict) -> None:
        event_queue.put(data)

    batch.add_callback(on_event)

    def run() -> None:
        try:
            _run_batch_sync(batch)
        except Exception as exc:
            event_queue.put({"type": "batch_error", "message": str(exc)})
        finally:
            event_queue.put(None)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    while True:
        try:
            event = event_queue.get(timeout=config.SSE_KEEPALIVE_INTERVAL)
        except Exception:
            yield ": keepalive\n\n"
            continue

        if event is None:
            break

        yield f"data: {json.dumps(event)}\n\n"

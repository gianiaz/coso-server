"""Request and phase timings without logging audio, prompts or credentials."""

import logging
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from time import perf_counter
from uuid import uuid4

from flask import current_app, g, has_request_context, request


def _log(event: str, **fields) -> None:
    in_request = has_request_context()
    logger = current_app.logger if in_request else logging.getLogger(__name__)
    request_id = getattr(g, "timing_id", "-") if in_request else "-"
    timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    details = " ".join(f"{key}={value}" for key, value in fields.items())
    logger.info("[Timing] id=%s utc=%s event=%s %s", request_id, timestamp, event, details)


def install_request_timing(app) -> None:
    @app.before_request
    def start_request():
        supplied_id = request.headers.get("X-Request-ID", "")
        g.timing_id = (
            supplied_id if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", supplied_id)
            else uuid4().hex
        )
        g.timing_started = perf_counter()
        _log("request_start", method=request.method, path=request.path,
             content_length=request.content_length)

    @app.after_request
    def response_ready(response):
        _log("response_ready", status=response.status_code,
             elapsed_ms=f"{(perf_counter() - g.timing_started) * 1000:.1f}")
        return response

    @app.teardown_request
    def request_failed(error):
        if error is not None and hasattr(g, "timing_started"):
            _log("request_error", error=type(error).__name__,
                 elapsed_ms=f"{(perf_counter() - g.timing_started) * 1000:.1f}")


@contextmanager
def timed_phase(phase: str):
    started = perf_counter()
    _log("phase_start", phase=phase)
    outcome = "ok"
    try:
        yield
    except BaseException:
        outcome = "error"
        raise
    finally:
        _log("phase_end", phase=phase, outcome=outcome,
             duration_ms=f"{(perf_counter() - started) * 1000:.1f}")

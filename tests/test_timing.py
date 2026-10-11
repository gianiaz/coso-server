import logging

from flask import Flask

from app import create_app
from app.timing import install_request_timing, timed_phase


def test_info_logging_with_preconfigured_host(tmp_path, caplog, monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    # Simulate a host that installed handlers before creating the Flask app.
    with caplog.at_level(logging.WARNING):
        app = create_app({"COSO_API_KEY": "test", "WAV_OUTPUT_DIR": str(tmp_path),
                          "OPENAI_API_KEY": "", "MEMORY_DB_PATH": str(tmp_path / "memory.db")})
        assert logging.getLogger().level == logging.INFO
        assert app.logger.getEffectiveLevel() == logging.INFO


def test_nested_phase_elapsed_time_and_duration(caplog, monkeypatch):
    caplog.set_level(logging.INFO)
    app = Flask(__name__)
    install_request_timing(app)
    clock = [10.0]
    monkeypatch.setattr("app.timing.perf_counter", lambda: clock[0])

    @app.get("/test")
    def test_route():
        with timed_phase("outer"):
            clock[0] += .1
            with timed_phase("inner"):
                clock[0] += .2
        return "ok"

    assert app.test_client().get("/test", headers={"X-Request-ID": "timing-test"}).status_code == 200
    messages = [record.getMessage() for record in caplog.records if "[Timing]" in record.getMessage()]
    assert all("id=timing-test " in message for message in messages)
    assert "event=request_start" in messages[0] and "elapsed_ms=0.0" in messages[0]
    assert "phase=inner outcome=ok duration_ms=200.0 elapsed_ms=300.0" in messages[-3]
    assert "phase=outer outcome=ok duration_ms=300.0 elapsed_ms=300.0" in messages[-2]
    assert "event=response_ready status=200 elapsed_ms=300.0" in messages[-1]

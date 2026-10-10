"""Standalone GitHub webhook; run with one Gunicorn worker (see deploy.md)."""

import hashlib
import hmac
import logging
import os
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settings:
    secret: str
    repository: str
    directory: Path
    branch: str = "main"

    @classmethod
    def from_environment(cls):
        secret = os.environ.get("DEPLOY_WEBHOOK_SECRET", "")
        repository = os.environ.get("DEPLOY_REPOSITORY", "")
        branch = os.environ.get("DEPLOY_BRANCH", "main")
        if not secret.strip() or not repository.strip() or not branch or branch.startswith("-"):
            raise ValueError("Configure DEPLOY_WEBHOOK_SECRET, DEPLOY_REPOSITORY and DEPLOY_BRANCH.")
        directory = Path(os.environ.get("DEPLOY_DIRECTORY", Path(__file__).resolve().parent.parent))
        return cls(secret, repository, directory.resolve(), branch)


def deploy(settings):
    """Use only local configuration for commands, never values from the payload."""
    environment = os.environ.copy()
    # Children do not need the webhook signing key. Git must never prompt.
    environment.pop("DEPLOY_WEBHOOK_SECRET", None)
    environment["GIT_TERMINAL_PROMPT"] = "0"

    def run(arguments, timeout=300, capture=False):
        return subprocess.run(
            arguments, cwd=settings.directory, env=environment,
            check=True, timeout=timeout, text=True, capture_output=capture,
        )

    branch = run(["git", "symbolic-ref", "--short", "HEAD"], capture=True).stdout.strip()
    if branch != settings.branch:
        raise RuntimeError("The checkout is not on DEPLOY_BRANCH.")
    if run(["git", "status", "--porcelain"], capture=True).stdout.strip():
        raise RuntimeError("The checkout contains local changes; deployment stopped.")
    run(["git", "pull", "--ff-only", "origin", settings.branch])
    python = str(settings.directory / ".venv" / "bin" / "python")
    run([python, "-m", "pip", "install", "--disable-pip-version-check", "-r", "requirements.txt"], timeout=1800)
    run(["sudo", "-n", "/usr/bin/systemctl", "restart", "coso-server.service"], timeout=120)


class DeploymentWorker:
    """Serialize deployments and coalesce pushes received during an update."""

    def __init__(self, action):
        self.action = action
        self.lock = threading.Lock()
        self.running = False
        self.pending = False

    def submit(self):
        with self.lock:
            self.pending = True
            if not self.running:
                self.running = True
                try:
                    threading.Thread(target=self._work, daemon=True, name="deploy").start()
                except Exception:
                    self.running = False
                    self.pending = False
                    raise

    def _work(self):
        while True:
            with self.lock:
                if not self.pending:
                    self.running = False
                    return
                self.pending = False
            logger.info("Deployment started")
            try:
                self.action()
            except Exception:
                logger.exception("Deployment failed; inspect the journal and redeliver the webhook after fixing the cause")
            else:
                logger.info("Deployment completed; coso-server restarted")


def create_app(settings=None, worker=None):
    logging.basicConfig(level=logging.INFO)
    settings = settings or Settings.from_environment()
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024
    worker = worker or DeploymentWorker(lambda: deploy(settings))

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error=error.name, message=error.description), error.code

    @app.route("/deploy", methods=["POST"], provide_automatic_options=False)
    def webhook():
        body = request.get_data()
        expected = "sha256=" + hmac.new(settings.secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not hmac.compare_digest(expected.encode("ascii"), signature.encode("utf-8")):
            return jsonify(error="unauthorized", message="Missing or invalid GitHub signature."), 401
        if request.mimetype != "application/json":
            return jsonify(error="invalid_content_type", message="Use application/json."), 415
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get("repository"), dict):
            return jsonify(error="invalid_payload", message="Expected a GitHub repository payload."), 400
        if payload["repository"].get("full_name") != settings.repository:
            return jsonify(error="wrong_repository", message="Repository not allowed."), 403
        event = request.headers.get("X-GitHub-Event")
        if event == "ping":
            return jsonify(status="ok"), 200
        if event != "push" or payload.get("ref") != f"refs/heads/{settings.branch}" or payload.get("deleted"):
            return jsonify(status="ignored"), 200
        try:
            worker.submit()
        except Exception:
            logger.exception("Cannot start deployment worker")
            return jsonify(error="unavailable", message="Could not queue deployment."), 503
        return jsonify(status="accepted"), 202

    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    create_app().run(host="127.0.0.1", port=9000)

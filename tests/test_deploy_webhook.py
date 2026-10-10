import hashlib
import hmac
import json
import subprocess
import threading
from unittest.mock import Mock

import pytest

from scripts.deploy_webhook import DeploymentWorker, Settings, create_app, deploy


@pytest.fixture
def settings(tmp_path):
    return Settings("test-secret", "gianiaz/coso-server", tmp_path)


def send(client, settings, payload=None, event="push", **headers):
    body = json.dumps(payload if payload is not None else {
        "repository": {"full_name": settings.repository}, "ref": "refs/heads/main",
    }).encode()
    signature = "sha256=" + hmac.new(settings.secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/deploy", data=body, content_type="application/json", headers={
        "X-Hub-Signature-256": signature, "X-GitHub-Event": event, **headers,
    })


def test_signed_push_and_ping(settings):
    worker = Mock()
    client = create_app(settings, worker).test_client()
    assert send(client, settings).status_code == 202
    worker.submit.assert_called_once()
    assert send(client, settings, event="ping").status_code == 200
    worker.submit.assert_called_once()


@pytest.mark.parametrize("signature", ["", "sha256=wrong", "é"])
def test_reject_invalid_signature(settings, signature):
    worker = Mock()
    client = create_app(settings, worker).test_client()
    assert send(client, settings, **{"X-Hub-Signature-256": signature}).status_code == 401
    worker.submit.assert_not_called()


def test_signed_body_cannot_be_modified(settings):
    worker = Mock()
    client = create_app(settings, worker).test_client()
    signature = "sha256=" + hmac.new(settings.secret.encode(), b"{}", hashlib.sha256).hexdigest()
    response = client.post("/deploy", json={"changed": True}, headers={"X-Hub-Signature-256": signature})
    assert response.status_code == 401
    worker.submit.assert_not_called()


@pytest.mark.parametrize("payload,event,status", [
    ({"repository": {"full_name": "other/repo"}}, "push", 403),
    ({"repository": {"full_name": "gianiaz/coso-server"}, "ref": "refs/heads/dev"}, "push", 200),
    ({"repository": {"full_name": "gianiaz/coso-server"}, "ref": "refs/heads/main", "deleted": True}, "push", 200),
    ({"repository": {"full_name": "gianiaz/coso-server"}}, "issues", 200),
    ([], "push", 400),
    ({"repository": None}, "push", 400),
])
def test_filter_payloads(settings, payload, event, status):
    worker = Mock()
    client = create_app(settings, worker).test_client()
    assert send(client, settings, payload, event).status_code == status
    worker.submit.assert_not_called()


def test_only_post_deploy_exists(settings):
    client = create_app(settings, Mock()).test_client()
    assert client.get("/deploy").status_code == 405
    assert client.options("/deploy").status_code == 405
    assert client.get("/health").status_code == 404


def test_missing_secret_fails_startup(monkeypatch):
    monkeypatch.delenv("DEPLOY_WEBHOOK_SECRET", raising=False)
    with pytest.raises(ValueError):
        create_app()


@pytest.mark.parametrize("failure", [0, 1, 2, 3])
def test_failure_prevents_restart(settings, monkeypatch, failure):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if len(calls) - 1 == failure:
            raise subprocess.CalledProcessError(1, args)
        return subprocess.CompletedProcess(args, 0, "main\n" if len(calls) == 1 else "")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        deploy(settings)
    assert not any(args[0] == "sudo" for args in calls)


@pytest.mark.parametrize("branch,dirty", [("dev\n", ""), ("main\n", " M file.py")])
def test_refuse_wrong_checkout(settings, monkeypatch, branch, dirty):
    run = Mock(side_effect=[subprocess.CompletedProcess([], 0, branch), subprocess.CompletedProcess([], 0, dirty)])
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(RuntimeError):
        deploy(settings)
    assert run.call_count <= 2


def test_deploy_order_and_no_secret_in_children(settings, monkeypatch):
    run = Mock(side_effect=[subprocess.CompletedProcess([], 0, "main\n")] + [subprocess.CompletedProcess([], 0, "")] * 4)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setenv("DEPLOY_WEBHOOK_SECRET", settings.secret)
    deploy(settings)
    calls = run.call_args_list
    assert calls[2].args[0] == ["git", "pull", "--ff-only", "origin", "main"]
    assert calls[3].args[0][1:] == ["-m", "pip", "install", "--disable-pip-version-check", "-r", "requirements.txt"]
    assert calls[4].args[0] == ["sudo", "-n", "/usr/bin/systemctl", "restart", "coso-server.service"]
    assert all("DEPLOY_WEBHOOK_SECRET" not in call.kwargs["env"] for call in calls)


def test_pushes_during_deploy_are_serialized_and_coalesced():
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def action():
        calls.append(1)
        if len(calls) == 1:
            started.set()
            assert release.wait(3)
        else:
            finished.set()

    worker = DeploymentWorker(action)
    worker.submit()
    assert started.wait(3)
    try:
        for _ in range(5):
            worker.submit()
        assert len(calls) == 1
    finally:
        release.set()
    assert finished.wait(3)
    assert len(calls) == 2

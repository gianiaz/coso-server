"""Mandatory API-key authentication for every HTTP request."""

import hmac

from flask import current_app, jsonify, request


def authenticate_request():
    expected = current_app.config["COSO_API_KEY"]
    provided = request.headers.get("X-API-Key", "")
    if not expected or not hmac.compare_digest(
        provided.encode("utf-8"), expected.encode("utf-8")
    ):
        return jsonify(error="unauthorized", message="API key mancante o non valida."), 401

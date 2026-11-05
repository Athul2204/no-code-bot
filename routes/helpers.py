from __future__ import annotations

from flask import jsonify, request

from ..validation import ValidationError


def json_body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError("Send a JSON object.")
    return data


def api_error(message: str, status: int = 400, **extra):
    resp = jsonify({"error": message, **extra})
    resp.status_code = status
    return resp


def not_found(what: str = "Item"):
    return api_error(f"{what} not found.", 404)


def register_error_handlers(app) -> None:
    @app.errorhandler(ValidationError)
    def _validation(exc: ValidationError):
        fields = {k: v for k, v in exc.errors.items() if k != "_"}
        message = exc.errors.get("_") or ("Please fix the highlighted fields." if fields else "Invalid request.")
        return jsonify({"error": message, "fields": fields}), 400

    @app.errorhandler(404)
    def _404(_e):
        if request.path.startswith("/api/"):
            return api_error("Not found.", 404)
        from flask import render_template
        return render_template("error.html", code=404, message="That page doesn't exist."), 404

    @app.errorhandler(405)
    def _405(_e):
        return api_error("Method not allowed.", 405) if request.path.startswith("/api/") else ("Method not allowed", 405)

    @app.errorhandler(413)
    def _413(_e):
        return api_error("That file is too large (limit 10 MB).", 413)

    @app.errorhandler(500)
    def _500(_e):
        if request.path.startswith("/api/"):
            return api_error("Something went wrong on the server. Please try again.", 500)
        from flask import render_template
        return render_template("error.html", code=500, message="Something went wrong. Please try again."), 500

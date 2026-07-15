from app import app as _app, limiter

_app.config["CSRF_DISABLED"] = True
_app.config["TESTING"] = True
limiter.enabled = False

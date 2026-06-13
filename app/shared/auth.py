from functools import wraps
from flask import session, redirect, url_for


def require_role(role):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if session.get("role") != role:
                return redirect(url_for("auth.login"))
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def require_customer(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if session.get("role") != "customer":
            return redirect(url_for("mail.login"))
        return fn(*args, **kwargs)
    return wrapper

import pytest
from unittest.mock import patch, MagicMock

from werkzeug.security import generate_password_hash

from app.shared.db import db
from app.shared.models.core import User


@pytest.fixture()
def admin_client(app, client, _clean_db):
    user_id = None
    with app.app_context():
        user = User(
            email="admin@example.com",
            role="admin",
            is_active=True,
            password_hash=generate_password_hash("admin123"),
        )
        db.session.add(user)
        db.session.flush()
        user_id = user.id
        db.session.commit()

    with client.session_transaction() as sess:
        sess["role"] = "admin"
        sess["user_id"] = user_id

    yield client, user_id

import contextlib
import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from app.modules.chat.services import cache_db
from app.modules.chat.services.cache import get_cache_path
from app.shared.models.core import CustomerAccount, Domain

OWN_ID = "@tester:locoroo.test"


@pytest.fixture()
def chat_cache():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = cache_db.open_cache(path, "0" * 64)
    yield conn
    conn.close()
    with contextlib.suppress(OSError):
        os.unlink(path)


@pytest.fixture()
def seeded_client(app, authed_client):
    """authed_client + real chat cache with creds/rooms + mocked Matrix client."""
    client, user_id, account_id = authed_client
    from app.shared.db import db
    from app.shared.keys import get_user_key

    with app.app_context():
        account = db.session.get(CustomerAccount, account_id)
        assert account is not None
        domain = db.session.get(Domain, account.domain_id)
        assert domain is not None
        domain.matrix_host = "synapse"
        domain.matrix_port = 8008
        domain.matrix_mas_url = "http://mas:8080"
        domain.matrix_mas_client_id = "01MAS00000000000000000000A"
        domain.matrix_mas_client_secret = "dev-mas-client-secret"
        db.session.commit()
        path = get_cache_path(account)
        conn = cache_db.open_cache(path, get_user_key(user_id))
        cache_db.set_credentials(
            conn,
            matrix_user_id=OWN_ID,
            access_token="syt_tok",
            device_id="DEV1",
            password_encrypted="enc",
            homeserver_url="http://synapse:8008",
        )
        cache_db.upsert_room(conn, "!room1:server", name="General", is_public=True)
        cache_db.upsert_member(conn, "!room1:server", OWN_ID, displayname="Tester")
        cache_db.upsert_room(conn, "!dm:server", is_direct=True)
        cache_db.upsert_member(conn, "!dm:server", OWN_ID)
        cache_db.upsert_member(conn, "!dm:server", "@peer:server", displayname="Peer")
        cache_db.insert_message(
            conn,
            event_id="$m1",
            room_id="!room1:server",
            sender=OWN_ID,
            type="m.room.message",
            body="hello",
            content_json=json.dumps({"msgtype": "m.text", "body": "hello"}),
            origin_server_ts=1000,
        )
        conn.commit()
        conn.close()

    mock_matrix = MagicMock()
    mock_matrix.user_id = OWN_ID
    mock_matrix.create_room.return_value = {"room_id": "!new:server"}
    mock_matrix.send_message.return_value = {"event_id": "$new1"}
    mock_matrix.send_event.return_value = {"event_id": "$up1"}
    mock_matrix.send_reaction.return_value = {"event_id": "$react1"}
    mock_matrix.send_edit.return_value = {"event_id": "$edit1"}
    mock_matrix.redact.return_value = {"event_id": "$redact1"}
    mock_matrix.sync.return_value = {"next_batch": "s2", "rooms": {}}
    mock_matrix.whoami.return_value = {"user_id": OWN_ID}

    def fake_ensure(account, domain, user_id_session):
        conn = cache_db.open_cache(path, get_user_key(user_id))
        creds = cache_db.get_credentials(conn) or {}
        return conn, mock_matrix, creds

    with patch(
        "app.modules.chat.services.provisioning.ensure_chat_client",
        side_effect=fake_ensure,
    ):
        yield client, mock_matrix, path, user_id
    if os.path.exists(path):
        os.unlink(path)

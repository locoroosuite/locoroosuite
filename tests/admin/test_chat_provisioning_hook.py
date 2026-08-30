from unittest.mock import patch

from app.shared.db import db
from app.shared.models.core import CustomerAccount, Domain, User


def test_create_customer_provisions_chat_identity(app, admin_client):
    client, _admin_id = admin_client
    with app.app_context():
        domain = Domain.query.first()
        if domain is None:
            from sqlalchemy import insert

            db.session.execute(
                insert(Domain).values(
                    name="hooktest.test",
                    imap_host="imap.hooktest.test",
                    smtp_host="smtp.hooktest.test",
                    smtp_tls_mode="starttls",
                )
            )
            db.session.commit()
            domain = Domain.query.first()
        assert domain is not None
        domain_id = domain.id
        domain_name = domain.name

    seen = {}

    def _capture(account):
        seen["email"] = account.email_address  # accessed while the request session is alive

    with patch(
        "app.modules.chat.services.provisioning.best_effort_provision",
        side_effect=_capture,
    ) as mock_provision:
        resp = client.post(
            "/admin/customers/new",
            data={
                "username": "hooktest",
                "domain_id": str(domain_id),
                "create_mode": "password",
                "password": "HookPass-123!",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        mock_provision.assert_called_once()
        assert seen["email"] == f"hooktest@{domain_name}"

    with app.app_context():
        user = User.query.filter_by(email=f"hooktest@{domain_name}").first()
        assert user is not None
        db.session.delete(user)
        db.session.commit()
        account_row = CustomerAccount.query.filter_by(
            email_address=f"hooktest@{domain_name}"
        ).first()
        if account_row is not None:
            db.session.delete(account_row)
            db.session.commit()

import logging


def test_slow_request_logged_when_threshold_low(client, monkeypatch, caplog):
    monkeypatch.setattr("app.SLOW_REQUEST_MS", 0)
    caplog.set_level(logging.WARNING, logger="app")
    resp = client.get("/app/login")
    assert resp.status_code != 500
    msgs = [r.getMessage() for r in caplog.records]
    assert any("slow request" in m and "/app/login" in m for m in msgs), msgs


def test_fast_request_not_logged(client, monkeypatch, caplog):
    monkeypatch.setattr("app.SLOW_REQUEST_MS", 60000)
    caplog.set_level(logging.WARNING, logger="app")
    resp = client.get("/app/login")
    assert resp.status_code != 500
    assert not [r for r in caplog.records if "slow request" in r.getMessage()]

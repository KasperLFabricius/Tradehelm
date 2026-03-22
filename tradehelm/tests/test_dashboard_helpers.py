from tradehelm.dashboard.client import call_api


class DummyResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.content = b"1"

    def json(self):
        return self._payload


def test_call_api_success(monkeypatch):
    monkeypatch.setattr(
        "tradehelm.dashboard.client.requests.request",
        lambda *args, **kwargs: DummyResponse(200, {"ok": True}),
    )
    result = call_api("http://x", "GET", "/health")
    assert result.ok is True
    assert result.payload == {"ok": True}


def test_call_api_structured_error(monkeypatch):
    monkeypatch.setattr(
        "tradehelm.dashboard.client.requests.request",
        lambda *args, **kwargs: DummyResponse(400, {"error": {"code": "bad", "message": "oops"}}),
    )
    result = call_api("http://x", "POST", "/boom")
    assert result.ok is False
    assert "bad" in (result.error or "")

from pathlib import Path

from fastapi.testclient import TestClient

from tradehelm.control_api.app import create_app


def _client_for(db_path: Path) -> TestClient:
    app = create_app(f"sqlite:///{db_path}")
    return TestClient(app)


def test_default_config_persisted_on_first_startup(tmp_path):
    db = tmp_path / "first.db"
    with _client_for(db) as client:
        cfg = client.get("/config")
        assert cfg.status_code == 200
        assert cfg.json()["replay_speed"] == 1.0


def test_persisted_config_loaded_on_subsequent_startup(tmp_path):
    db = tmp_path / "persist.db"
    with _client_for(db) as client:
        response = client.post("/config", json={"config": {"replay_speed": 7.0}})
        assert response.status_code == 200

    with _client_for(db) as client:
        cfg = client.get("/config").json()
        assert cfg["replay_speed"] == 7.0


def test_post_config_applies_and_persists(tmp_path):
    db = tmp_path / "apply.db"
    with _client_for(db) as client:
        update = client.post("/config", json={"config": {"replay_speed": 9.0, "risk": {"max_daily_loss": 300}}})
        assert update.status_code == 200
        assert update.json()["updated"] is True
        state = client.get("/state").json()
        assert state["daily_loss_limit"] == 300.0

    with _client_for(db) as client:
        cfg = client.get("/config").json()
        assert cfg["replay_speed"] == 9.0
        assert cfg["risk"]["max_daily_loss"] == 300.0


def test_invalid_replay_path_returns_clean_api_error(tmp_path):
    with _client_for(tmp_path / "err.db") as client:
        response = client.post("/replay/load", json={"path": "missing.csv"})
        assert response.status_code == 400
        payload = response.json()
        assert payload["error"]["code"] == "invalid_replay_path"


def test_invalid_strategy_id_returns_clean_api_error(tmp_path):
    with _client_for(tmp_path / "strategy.db") as client:
        response = client.post("/strategies/unknown/enable")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "strategy_not_found"


def test_health_returns_readiness_shape(tmp_path):
    with _client_for(tmp_path / "health.db") as client:
        response = client.get("/health")
        assert response.status_code == 200
        payload = response.json()
        assert payload["ok"] is True
        readiness = payload["readiness"]
        assert "db_reachable" in readiness
        assert "replay_loaded" in readiness
        assert "replay_running" in readiness
        assert "mode" in readiness
        assert "active_config_loaded" in readiness


def test_startup_restores_metadata_without_auto_starting_replay(tmp_path):
    dataset = tmp_path / "demo.csv"
    dataset.write_text("timestamp,symbol,open,high,low,close,volume\n2026-01-01T14:30:00Z,DEMO,1,1,1,1,100\n")
    db = tmp_path / "meta.db"

    with _client_for(db) as client:
        assert client.post("/replay/load", json={"path": str(dataset)}).status_code == 200
        assert client.post("/config", json={"config": {"replay_speed": 3.0}}).status_code == 200

    with _client_for(db) as client:
        state = client.get("/state").json()
        assert state["replay_loaded"] is True
        assert state["replay_running"] is False
        assert state["mode"] == "STOPPED"


def test_invalid_config_payload_returns_clean_error(tmp_path):
    with _client_for(tmp_path / "badcfg.db") as client:
        response = client.post("/config", json={"config": {"replay_speed": 0}})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_payload"


def test_analytics_reset_requires_structured_confirmation_error(tmp_path):
    with _client_for(tmp_path / "reset.db") as client:
        response = client.post("/analytics/reset", json={"confirm": False})
        assert response.status_code == 400
        payload = response.json()
        assert payload["error"]["code"] == "reset_confirmation_required"

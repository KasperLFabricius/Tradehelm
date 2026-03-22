from tradehelm.config.models import RiskConfig
from tradehelm.risk.engine import RiskEngine


def test_risk_rejects_non_positive_edge_and_cooldown():
    risk = RiskEngine(RiskConfig(cooldown_bars_after_exit=2))
    ok, reason = risk.validate("DEMO", 10, 100, 0, 0, 0)
    assert not ok
    assert "non-positive" in reason
    risk.on_exit("DEMO")
    ok, reason = risk.validate("DEMO", 10, 100, 5, 0, 0)
    assert not ok
    assert "cooldown" in reason

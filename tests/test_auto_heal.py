from app.workflows.auto_heal import parse_http_status, decision_matrix


def test_parse_403_blocked():
    code, dtype = parse_http_status("HTTP 403 Forbidden")
    assert code == 403
    assert dtype == "BLOCKED"


def test_parse_403_text():
    code, dtype = parse_http_status("waf blocked request")
    assert code == 403
    assert dtype == "BLOCKED"


def test_parse_500_retryable():
    code, dtype = parse_http_status("500 Internal Server Error")
    assert code == 500
    assert dtype == "RETRYABLE"


def test_parse_timeout_retryable():
    code, dtype = parse_http_status("Connection timed out after 30s")
    assert code == 0
    assert dtype == "RETRYABLE"


def test_parse_404_fatal():
    code, dtype = parse_http_status("404 Not Found")
    assert code == 404
    assert dtype == "FATAL"


def test_parse_none():
    code, dtype = parse_http_status(None)
    assert code is None
    assert dtype is None


def test_decision_matrix_blocked_stand_down():
    state = {}
    decision, info = decision_matrix("test_mod", 403, "BLOCKED", "403", state)
    assert decision == "BLOCKED"
    assert info["action"] == "stand_down"
    assert "test_mod" in state
    assert state["test_mod"]["decision"] == "BLOCKED"


def test_decision_matrix_fatal_skip():
    state = {"test_mod": {"decision": "FATAL"}}
    decision, info = decision_matrix("test_mod", 404, "FATAL", "404", state)
    assert decision == "FATAL"
    assert info["action"] == "skip"


def test_decision_matrix_retryable_first_try():
    state = {}
    decision, info = decision_matrix("test_mod", 500, "RETRYABLE", "500", state)
    assert decision == "RETRYABLE"
    assert info["action"] == "retry"
    assert info["retry_count"] == 1


def test_decision_matrix_retryable_exhausted():
    state = {}
    for _ in range(4):
        decision, info = decision_matrix("test_mod", 502, "RETRYABLE", "502", state)
    assert decision == "BLOCKED"
    assert info["action"] == "escalate"


def test_decision_matrix_unknown():
    state = {}
    decision, info = decision_matrix("test_mod", None, None, "ok", state)
    assert decision is None
    assert info["action"] == "unknown"

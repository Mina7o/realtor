def test_index_returns_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.content_type == "text/html; charset=utf-8"


def test_charts_page(client):
    resp = client.get("/charts")
    assert resp.status_code == 200


def test_commercial_page(client):
    resp = client.get("/commercial")
    assert resp.status_code == 200


def test_insights_page(client):
    resp = client.get("/insights")
    assert resp.status_code == 200


def test_commercial_charts_page(client):
    resp = client.get("/commercial_charts")
    assert resp.status_code == 200


def test_map_page(client):
    resp = client.get("/map")
    assert resp.status_code == 200


def test_api_system_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data is not None
    assert "status" in data

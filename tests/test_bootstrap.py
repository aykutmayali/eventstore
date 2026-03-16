def test_healthz_endpoint(client):
    response = client.get("/healthz/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_schema_endpoint(client):
    response = client.get("/api/schema/?format=json")
    assert response.status_code == 200
    data = response.json()
    assert data["info"]["title"] == "EventShop API"

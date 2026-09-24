def test_health_devuelve_200(client):
    response = client.get("/health/")

    assert response.status_code == 200


def test_health_devuelve_status_ok(client):
    response = client.get("/health/")

    assert response.json() == {"status": "ok"}

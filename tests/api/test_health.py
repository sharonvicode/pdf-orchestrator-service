def test_health_devuelve_200_sin_redirigir(client):
    response = client.get("/health", follow_redirects=False)

    assert response.status_code == 200


def test_health_devuelve_status_ok(client):
    response = client.get("/health")

    assert response.json() == {"status": "ok"}

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PASSWORD = "password12"


def upload(client, name="a.pdf", content=PDF):
    return client.post(
        "/api/files",
        files={"file": (name, content, "application/octet-stream")},
    )


def register(client, nickname=None, email=None, password=PASSWORD):
    body = {"password": password}
    if nickname is not None:
        body["nickname"] = nickname
    if email is not None:
        body["email"] = email
    response = client.post("/api/auth/register", json=body)
    assert response.status_code == 200, response.text
    return response.json()["user"]

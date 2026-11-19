def test_every_page_carries_a_strict_csp_with_a_fresh_nonce(client):
    first = client.get("/healthz").headers["content-security-policy"]
    second = client.get("/healthz").headers["content-security-policy"]
    assert "'nonce-" in first and first != second
    assert "'unsafe-inline'" not in first and "'unsafe-eval'" not in first
    assert "frame-ancestors 'none'" in first


def test_the_other_security_headers_are_set(client):
    headers = client.get("/healthz").headers
    assert headers["x-frame-options"] == "DENY"
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"

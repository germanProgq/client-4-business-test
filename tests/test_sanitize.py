from app.core.sanitize import sanitize_headers, sanitize_value


def test_sanitize_value_redacts_denylisted_keys_recursively():
    raw = {
        "title": "Instagram reel draft",
        "reviewerUserIds": ["usr_1", "usr_2"],
        "actor": {
            "email": "someone@example.com",
            "session_token": "abc123",
        },
        "providerPayload": {"raw": "should not survive"},
        "nested": [{"apiKey": "super-secret"}, {"comment": "keep me"}],
    }

    result = sanitize_value(raw)

    assert result["title"] == "Instagram reel draft"
    assert result["reviewerUserIds"] == ["usr_1", "usr_2"]
    assert result["actor"]["email"] == "***REDACTED***"
    assert result["actor"]["session_token"] == "***REDACTED***"
    assert result["providerPayload"] == "***REDACTED***"
    assert result["nested"][0]["apiKey"] == "***REDACTED***"
    assert result["nested"][1]["comment"] == "keep me"


def test_sanitize_value_passes_through_non_mapping_values():
    assert sanitize_value("plain string") == "plain string"
    assert sanitize_value(42) == 42
    assert sanitize_value(None) is None


def test_sanitize_headers_redacts_auth_and_cookies():
    headers = {
        "Authorization": "Bearer secret-token",
        "Cookie": "session=abc",
        "X-Workspace-Id": "ws_1",
        "Content-Type": "application/json",
    }

    result = sanitize_headers(headers)

    assert result["Authorization"] == "***REDACTED***"
    assert result["Cookie"] == "***REDACTED***"
    assert result["X-Workspace-Id"] == "ws_1"
    assert result["Content-Type"] == "application/json"

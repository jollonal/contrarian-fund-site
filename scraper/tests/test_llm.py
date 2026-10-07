import pytest

from vernissage import llm as L


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.ok = status < 400
        self.text = body if isinstance(body, str) else str(body)

    def json(self):
        if isinstance(self._body, str):
            raise ValueError("not json")
        return self._body


def make(monkeypatch, responses):
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(json)
        return responses.pop(0)

    monkeypatch.setattr(L.requests, "post", fake_post)
    monkeypatch.setattr(L, "SPACING", 0)
    m = L.LLM()
    m.token = "t"
    return m, calls


def test_object_response(monkeypatch):
    m, calls = make(monkeypatch, [Resp(200, {"success": True, "result": {"response": {"exhibitions": []}}})])
    assert m.json("s", "TEXT:\nx") == {"exhibitions": []}
    assert calls[0]["response_format"]["type"] == "json_schema"


def test_string_response_with_prose(monkeypatch):
    m, _ = make(monkeypatch, [Resp(200, {"success": True, "result": {"response": 'Here: {"exhibitions": [1]}'}})])
    assert m.json("s", "x") == {"exhibitions": [1]}


def test_json_mode_failure_falls_back_to_plain(monkeypatch):
    m, calls = make(monkeypatch, [
        Resp(400, {"success": False, "errors": [{"message": "JSON Mode couldn't be met"}]}),
        Resp(200, {"success": True, "result": {"response": '{"opening": {"date": null}}'}}),
    ])
    assert m.json("s", "EXCERPTS:\nx") == {"opening": {"date": None}}
    assert "response_format" not in calls[1]


def test_non_json_body_and_quota(monkeypatch):
    m, _ = make(monkeypatch, [Resp(502, "<html>bad gateway</html>")])
    with pytest.raises(L.LLMUnavailable):
        m.json("s", "x")
    m, _ = make(monkeypatch, [Resp(429, {"success": False, "errors": [{"message": "daily neuron limit"}]})])
    with pytest.raises(L.LLMUnavailable):
        m.json("s", "x")
    assert m.exhausted and not m.available

import json

from gold_wirewatch.config import Settings
from gold_wirewatch.openclaw_client import OpenClawClient


def test_openclaw_payload_format(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Resp:
        def raise_for_status(self) -> None:
            return None

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url: str, headers: dict[str, str], content: str) -> Resp:
            captured["url"] = url
            captured["headers"] = headers
            captured["payload"] = json.loads(content)
            return Resp()

    monkeypatch.setattr("gold_wirewatch.openclaw_client.httpx.Client", lambda timeout: FakeClient())
    settings = Settings(openclaw_token="tok", openclaw_base_url="http://localhost:7331")
    client = OpenClawClient(settings)
    client.trigger("hello", {"k": "v"})

    assert captured["url"] == "http://localhost:7331/hooks/agent"
    payload = captured["payload"]
    assert payload["wakeMode"] == "now"
    assert payload["agentId"] == "quant"

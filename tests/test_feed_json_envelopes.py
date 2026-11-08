"""FIRMS and AIS must return parseable JSON in both detail modes."""
import json
import pytest
from gev import detail, hermes_tools

@pytest.mark.parametrize("name", ["get_active_fires", "vessels_in_area", "find_vessel", "get_vessel_track"])
@pytest.mark.parametrize("mode", ["concise", "full"])
@pytest.mark.parametrize("shape", ["structured", "text_json", "missing", "error", "is_error"])
def test_feed_json_envelope(monkeypatch, name, mode, shape):
    body = {"total": 0, "rows": [], "source": "test-provider"}
    result = {"content": [{"type": "text", "text": "0 records in the requested box."}]}
    if shape == "structured":
        result["structuredContent"] = body
    elif shape == "text_json":
        result["content"].append({"type": "text", "text": json.dumps(body)})
    elif shape == "missing":
        result["content"] = [{"type": "text", "text": "FIRMS_MAP_KEY is not configured"}]
    elif shape == "error":
        result["structuredContent"] = {"error": "Provider unavailable"}
    else:
        result["isError"] = True
    class Client:
        def call_tool(self, *_args):
            return result
    monkeypatch.setattr(hermes_tools.mcp_client, "client", lambda: Client())
    value = json.loads(hermes_tools.make_handler(name)({"detail": mode}))
    assert value["ok"] is (shape in {"structured", "text_json"})
    if value["ok"]:
        assert value["total"] == 0
    else:
        assert value["error"]

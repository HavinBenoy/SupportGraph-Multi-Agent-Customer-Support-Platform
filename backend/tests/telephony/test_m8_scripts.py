"""The Edesy setup script builds the documented requests and refuses unsafe URLs. No network: the HTTP opener is replaced."""

import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import edesy_setup  # noqa: E402

URL = "wss://example.org/api/telephony/edesy/stream/" + "k" * 40


class Opener:
    def __init__(self):
        self.reqs = []

    def __call__(self, req, timeout=None):
        self.reqs.append(req)
        return io.BytesIO(json.dumps({"ok": True}).encode())


def test_attach_builds_the_documented_request_with_the_key_from_the_environment(monkeypatch, capsys):
    monkeypatch.setenv("EDESY_API_KEY", "vp_secret")
    o = Opener()
    edesy_setup.main(["attach", "--number-id", "pn_1", "--url", URL, "--rate", "8000"], opener=o)
    r = o.reqs[0]
    assert (r.get_method(), r.full_url) == ("PUT", "https://voice-api.edesy.in/v1/phone-numbers/pn_1/inbound")
    assert json.loads(r.data) == {"target": "media_stream", "stream_url": URL, "sample_rate": 8000}
    assert r.headers["Authorization"] == "Bearer vp_secret" and "vp_secret" not in capsys.readouterr().out
    edesy_setup.main(["detach", "--number-id", "pn_1"], opener=o)
    edesy_setup.main(["stream-test", "--url", URL], opener=o)
    assert [(q.get_method(), q.full_url.rsplit("/v1", 1)[1]) for q in o.reqs[1:]] == [("DELETE", "/phone-numbers/pn_1/inbound"), ("POST", "/stream-test")]


@pytest.mark.parametrize("url", ["ws://example.org/api/telephony/edesy/stream/" + "k" * 40, "wss://example.org/other", "wss://example.org/api/telephony/edesy/stream/short"])
def test_unsafe_or_wrong_urls_are_refused_before_any_request(monkeypatch, url):
    monkeypatch.setenv("EDESY_API_KEY", "vp_secret")
    o = Opener()
    with pytest.raises(SystemExit):
        edesy_setup.main(["attach", "--number-id", "pn_1", "--url", url], opener=o)
    assert o.reqs == []


def test_no_key_no_request(monkeypatch):
    monkeypatch.delenv("EDESY_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        edesy_setup.main(["numbers"], opener=Opener())

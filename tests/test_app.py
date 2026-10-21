"""The local web page: upload flow, limits, and refusing anything not local."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from techx_roster_check import app as app_module
from techx_roster_check.app import _is_local_host_header, _is_local_origin, create_app

from conftest import SAMPLE

LOCAL = "http://127.0.0.1:8766"


@pytest.fixture
def client():
    return TestClient(create_app(), base_url=LOCAL)


def post(client, data: bytes, pack: str = "health", name: str = "students.txt", **headers):
    return client.post("/check", params={"pack": pack, "name": name}, content=data,
                       headers={"Content-Type": "application/octet-stream", **headers})


class TestUpload:
    def test_upload_page_lists_packs(self, client):
        res = client.get("/")
        assert res.status_code == 200
        assert '<option value="health"' in res.text
        assert res.headers["cache-control"] == "no-store"

    def test_check_returns_the_report(self, client):
        res = post(client, SAMPLE.read_bytes())
        assert res.status_code == 200
        assert "TechX Roster Check" in res.text and "students.txt" in res.text
        assert "Check another file" in res.text
        assert res.headers["cache-control"] == "no-store"

    def test_empty_file(self, client):
        res = post(client, b"  \n")
        assert res.status_code == 400 and res.json()["detail"] == "The file is empty."

    def test_unknown_pack(self, client):
        res = post(client, b"A\n1\n", pack="nope")
        assert res.status_code == 400 and "no rule pack" in res.json()["detail"]

    def test_pack_paths_are_refused(self, client, tmp_path):
        path = tmp_path / "evil.toml"
        path.write_text("")
        assert post(client, b"A\n1\n", pack=str(path)).status_code == 400

    def test_too_large(self, client, monkeypatch):
        monkeypatch.setattr(app_module, "MAX_UPLOAD_BYTES", 10)
        res = post(client, b"A\n" + b"1\n" * 20)
        assert res.status_code == 413

    def test_broken_pack_is_shown_not_fatal(self, client, user_packs):
        (user_packs / "broken.toml").write_text('[[rules]]\nid = "a"\ntype = "nope"\n')
        res = client.get("/")
        assert res.status_code == 200
        assert "broken (has a mistake)" in res.text
        assert post(client, b"A\n1\n", pack="broken").status_code == 400


class TestLocalOnly:
    @pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.1:8766", "localhost:8766", "[::1]:8766"])
    def test_this_machine(self, host):
        assert _is_local_host_header(host)

    @pytest.mark.parametrize("host", ["evil.example", "127.0.0.1.evil.example", "192.168.1.10", "", None])
    def test_other_names(self, host):
        assert not _is_local_host_header(host)

    def test_origins(self):
        assert _is_local_origin(LOCAL)
        assert not _is_local_origin("http://evil.example")
        assert not _is_local_origin("null")

    def test_rebound_hostname_is_refused(self, client):
        assert client.get("/", headers={"Host": "evil.example"}).status_code == 421

    def test_cross_origin_post_is_refused(self, client):
        res = post(client, SAMPLE.read_bytes(), Origin="http://evil.example")
        assert res.status_code == 403
        assert "Sample" not in res.text

    def test_own_origin_is_allowed(self, client):
        assert post(client, SAMPLE.read_bytes(), Origin=LOCAL).status_code == 200

    def test_no_api_docs(self, client):
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404

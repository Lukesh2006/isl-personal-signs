"""Web API tests. MediaPipe never runs: feature extraction is replaced."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["PERSONAL_SIGNS_WARM_UP"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from personal_signs import api, engine  # noqa: E402
from test_engine import _sequence  # noqa: E402


class ApiTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.clips = {}
        for patcher in (
            patch.object(engine, "SAMPLE_ROOT", root / "training_samples"),
            patch.object(engine, "MODEL_ROOT", root / "personal_models"),
            patch.object(engine, "SEED_MODEL_PATH", root / "personal_models" / "seed.json"),
            patch.object(engine, "SEED_ROOT", root / "training_samples" / "seed"),
            patch.object(engine, "extract_sequence", side_effect=lambda data, frame_count=16: self.clips[data]),
            patch.dict(os.environ, {api.API_KEY_ENV: "", api.POCKETBASE_URL_ENV: ""}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        api._token_cache.clear()
        self.client = TestClient(api.app)

    def clip(self, **kwargs) -> bytes:
        data = f"clip-{len(self.clips)}".encode()
        self.clips[data] = _sequence(seed=len(self.clips) + 1, **kwargs)
        return data

    def test_health_and_demo_page(self):
        self.assertEqual(self.client.get("/health").json()["status"], "ok")
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Personal ISL Signs", page.text)

    def test_teach_train_and_recognize(self):
        for _ in range(3):
            self.assertTrue(self.client.post("/signs/hello", content=self.clip()).json()["saved"])
            self.assertTrue(self.client.post("/signs/thanks", content=self.clip(offset=0.6)).json()["saved"])
        self.assertEqual(self.client.get("/signs").json()["samples"], {"hello": 3, "thanks": 3})

        trained = self.client.post("/train").json()
        self.assertEqual(trained["sign_count"], 2)
        self.assertEqual(trained["validation"]["accuracy"], 1.0)

        result = self.client.post("/recognize", content=self.clip()).json()
        self.assertEqual(result["decision"], "accepted")
        self.assertEqual(result["label"], "hello")
        self.assertEqual([r["label"] for r in result["ranking"]], ["hello", "thanks"])
        self.assertLess(result["ranking"][0]["distance"], result["ranking"][1]["distance"])

        # Answering "yes" to a question saves that clip as a new example of the sign.
        confirmed = self.client.post("/signs/hello?confirm=true", content=self.clip()).json()
        self.assertTrue(confirmed["saved"])
        self.assertEqual(confirmed["kind"], "isl_variant")  # an existing sign keeps its type

    def test_users_are_kept_apart(self):
        self.client.post("/signs/hello", content=self.clip(), headers={"X-User": "asha"})
        self.assertEqual(self.client.get("/signs", headers={"X-User": "ravi"}).json()["samples"], {})
        self.assertEqual(self.client.get("/signs", headers={"X-User": "asha"}).json()["samples"], {"hello": 1})

    def test_bad_input_returns_clear_errors(self):
        self.assertEqual(self.client.get("/signs", headers={"X-User": "../etc"}).status_code, 400)
        self.assertEqual(self.client.post("/signs/hello", content=b"").status_code, 400)
        self.assertEqual(self.client.delete("/signs/hello/123").status_code, 404)
        self.assertEqual(self.client.post("/recognize", content=self.clip()).status_code, 400)  # nothing trained

    def test_website_may_call_the_local_server(self):
        preflight = self.client.options(
            "/signs",
            headers={
                "Origin": "https://signbridgeco.xyz",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "x-user",
                "Access-Control-Request-Private-Network": "true",
            },
        )
        self.assertEqual(preflight.status_code, 200)
        self.assertEqual(preflight.headers["access-control-allow-origin"], "https://signbridgeco.xyz")
        self.assertEqual(preflight.headers.get("access-control-allow-private-network"), "true")
        other = self.client.get("/signs", headers={"Origin": "https://evil.example"})
        self.assertNotIn("access-control-allow-origin", other.headers)

    def test_api_key_is_required_when_set(self):
        with patch.dict(os.environ, {api.API_KEY_ENV: "secret-key"}):
            self.assertEqual(self.client.get("/signs").status_code, 401)
            self.assertEqual(self.client.get("/signs", headers={"Authorization": "Bearer wrong"}).status_code, 401)
            ok = self.client.get("/signs", headers={"Authorization": "Bearer secret-key"})
            self.assertEqual(ok.status_code, 200)
            self.assertEqual(self.client.get("/health").status_code, 200)  # health stays public

    def test_config_reports_local_mode(self):
        self.assertEqual(self.client.get("/config").json(), {"auth": "none"})


class _FakeResponse:
    def __init__(self, body: bytes):
        self.body = body

    def read(self, *args):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class PocketBaseAccountTests(unittest.TestCase):
    """Account mode: logins are checked with PocketBase (simulated here)."""

    ACCOUNTS = {"token-asha": "asha0000000001", "token-ravi": "ravi0000000002"}
    clip = ApiTests.clip

    def setUp(self):
        ApiTests.setUp(self)
        self.checks = []
        for patcher in (
            patch.dict(os.environ, {
                api.POCKETBASE_URL_ENV: "http://pb.test",
                api.POCKETBASE_PUBLIC_URL_ENV: "https://db.example.test",
            }),
            patch.object(api.urllib.request, "urlopen", side_effect=self._pocketbase),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _pocketbase(self, request, timeout=None):
        self.checks.append(request.full_url)
        token = request.get_header("Authorization")
        if token not in self.ACCOUNTS:
            raise api.urllib.error.HTTPError(request.full_url, 401, "Unauthorized", None, None)
        return _FakeResponse(json.dumps({"token": token, "record": {"id": self.ACCOUNTS[token]}}).encode())

    def as_(self, token):
        return {"Authorization": f"Bearer {token}"}

    def test_config_tells_the_page_where_to_log_in(self):
        self.assertEqual(
            self.client.get("/config").json(), {"auth": "pocketbase", "pocketbase_url": "https://db.example.test"}
        )

    def test_login_is_required(self):
        response = self.client.get("/signs")
        self.assertEqual(response.status_code, 401)
        self.assertIn("log in", response.json()["detail"])
        self.assertEqual(self.client.get("/signs", headers=self.as_("stolen")).status_code, 401)

    def test_each_account_has_its_own_signs(self):
        self.assertTrue(self.client.post("/signs/hello", content=self.clip(), headers=self.as_("token-asha")).json()["saved"])
        self.assertEqual(self.client.get("/signs", headers=self.as_("token-asha")).json()["samples"], {"hello": 1})
        self.assertEqual(self.client.get("/signs", headers=self.as_("token-ravi")).json()["samples"], {})
        # The X-User header cannot be used to reach another account's signs.
        sneaky = self.client.get("/signs", headers={**self.as_("token-ravi"), "X-User": "asha0000000001"})
        self.assertEqual(sneaky.json()["samples"], {})
        self.assertTrue((engine.SAMPLE_ROOT / "asha0000000001" / "hello").is_dir())

    def test_checked_logins_are_cached_briefly(self):
        for _ in range(3):
            self.client.get("/signs", headers=self.as_("token-asha"))
        self.assertEqual(len(self.checks), 1)
        self.assertTrue(self.checks[0].endswith("/api/collections/users/auth-refresh"))

    def test_account_server_down_gives_503(self):
        with patch.object(api.urllib.request, "urlopen", side_effect=api.urllib.error.URLError("refused")):
            response = self.client.get("/signs", headers=self.as_("token-new"))
        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()

"""Login and refuse-to-start checks for the panel.

    uv run python -m unittest discover tests
"""

import base64
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from starlette.testclient import TestClient  # noqa: E402

import webui  # noqa: E402


def basic(user: str, password: str) -> dict:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


class LoginTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(webui.app)
        # Any route that only reads local files will do.
        self.path = "/api/notes/pending"

    def with_password(self):
        return mock.patch.multiple(webui, PANEL_USER="admin", PANEL_PASSWORD="s3cret")

    def test_open_when_no_password_is_set(self):
        with mock.patch.object(webui, "PANEL_PASSWORD", ""):
            self.assertEqual(self.client.get(self.path).status_code, 200)

    def test_asks_for_login(self):
        with self.with_password():
            for path in ("/", self.path):
                res = self.client.get(path)
                self.assertEqual(res.status_code, 401, path)
                self.assertTrue(res.headers["WWW-Authenticate"].startswith("Basic realm="))

    def test_post_needs_login_too(self):
        with self.with_password():
            self.assertEqual(self.client.post("/api/worker/stop").status_code, 401)

    def test_right_login_gets_in(self):
        with self.with_password():
            res = self.client.get(self.path, headers=basic("admin", "s3cret"))
            self.assertEqual(res.status_code, 200)

    def test_wrong_logins_are_refused(self):
        with self.with_password():
            for headers in (
                basic("admin", "wrong"),
                basic("someone", "s3cret"),
                basic("admin", ""),
                {"Authorization": "Basic !!!not-base64"},
                {"Authorization": "Bearer s3cret"},
            ):
                self.assertEqual(self.client.get(self.path, headers=headers).status_code, 401)

    def test_password_may_contain_colons_and_unicode(self):
        with mock.patch.multiple(webui, PANEL_USER="admin", PANEL_PASSWORD="pä:ss:wörd"):
            res = self.client.get(self.path, headers=basic("admin", "pä:ss:wörd"))
            self.assertEqual(res.status_code, 200)

    def test_healthz_is_open_and_says_only_ok(self):
        with self.with_password():
            res = self.client.get("/healthz")
            self.assertEqual((res.status_code, res.text), (200, "ok"))


class RefuseToStartTests(unittest.TestCase):
    def test_behind_proxy_without_password_exits_before_serving(self):
        with mock.patch.multiple(webui, BEHIND_PROXY=True, PANEL_PASSWORD=""), \
             mock.patch("uvicorn.run") as run, \
             mock.patch("sys.stderr"):
            self.assertEqual(webui.main(), 1)
            run.assert_not_called()

    def test_behind_proxy_with_password_serves_without_certificate_or_browser(self):
        with mock.patch.multiple(webui, BEHIND_PROXY=True, PANEL_PASSWORD="s3cret"), \
             mock.patch("uvicorn.run") as run, mock.patch("webbrowser.open") as browser:
            self.assertEqual(webui.main(), 0)
            run.assert_called_once()
            self.assertNotIn("ssl_certfile", run.call_args.kwargs)
            browser.assert_not_called()


if __name__ == "__main__":
    unittest.main()

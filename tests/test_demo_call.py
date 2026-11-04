"""Public /call demo: switched off by default, link key, rate and concurrency caps.

    uv run python -m unittest discover tests
"""

import base64
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from starlette.testclient import TestClient  # noqa: E402

import catalog  # noqa: E402
import demo_call  # noqa: E402
import webui  # noqa: E402

LK = {
    "LIVEKIT_URL": "wss://example.livekit.cloud",
    "LIVEKIT_API_KEY": "APItest",
    "LIVEKIT_API_SECRET": "x" * 40,
    "DEMO_ENABLED": "1",
    "DEMO_KEY": "",
    "BEHIND_PROXY": "",
    "DEMO_IMAGE": "demo.jpg",
}


def claims(jwt: str) -> dict:
    part = jwt.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


class DemoCallTests(unittest.TestCase):
    def setUp(self):
        demo_call.reset()
        self.client = TestClient(webui.app)
        # Panel password on: the demo routes must still be reachable without it.
        self.pw = mock.patch.multiple(webui, PANEL_USER="admin", PANEL_PASSWORD="s3cret")
        self.pw.start()
        self.addCleanup(self.pw.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        (pathlib.Path(tmp.name) / "demo.jpg").write_bytes(b"x")
        self.dir = mock.patch.object(demo_call, "AVATAR_DIR", pathlib.Path(tmp.name))
        self.dir.start()
        self.addCleanup(self.dir.stop)
        self.presets = pathlib.Path(tmp.name) / "presets.json"
        self.presets.write_text(json.dumps({"presets": [
            {"id": "tessa", "label": "Tessa", "image": "tessa.jpg", "frame": "wide"},
            {"id": "nopic", "label": "No pic", "image": "gone.jpg"},
        ]}))
        (pathlib.Path(tmp.name) / "tessa.jpg").write_bytes(b"TESSA")
        pp = mock.patch.object(catalog, "PRESETS_PATH", self.presets)
        pp.start()
        self.addCleanup(pp.stop)

    def env(self, **extra):
        return mock.patch.dict(os.environ, {**LK, **extra})

    def token(self, k="", ip=None):
        headers = {"x-forwarded-for": ip} if ip else {}
        return self.client.post("/api/call/token", json={"k": k}, headers=headers)

    def test_off_unless_enabled(self):
        with self.env(DEMO_ENABLED=""):
            self.assertEqual(self.client.get("/call").status_code, 404)
            self.assertEqual(self.token().status_code, 404)

    def test_refuses_without_demo_face(self):
        # Otherwise the agent quietly uses the first face in avatars/.
        for img in ("", "missing.jpg"):
            with self.env(DEMO_IMAGE=img):
                self.assertEqual(self.token().status_code, 503)

    def test_page_is_public_but_panel_is_not(self):
        with self.env():
            self.assertEqual(self.client.get("/call").status_code, 200)
            self.assertEqual(self.client.get("/").status_code, 401)

    def test_token_dispatches_agent_into_own_room(self):
        with self.env(DEMO_MAX_SECONDS="300", AGENT_NAME="zoom-bot", DEMO_PRESET="tessa"):
            a, b = self.token().json(), self.token().json()
        self.assertNotEqual(a["room"], b["room"])
        self.assertEqual(a["maxSeconds"], 300)
        c = claims(a["token"])
        self.assertEqual(c["video"]["room"], a["room"])
        self.assertTrue(c["video"]["roomJoin"])
        self.assertFalse(c["video"].get("roomAdmin", False))
        self.assertLessEqual(c["exp"] - c["nbf"], 120)
        dispatch = c["roomConfig"]["agents"][0]
        self.assertEqual(dispatch["agentName"], "zoom-bot")
        meta = json.loads(dispatch["metadata"])
        self.assertEqual((meta["mode"], meta["maxSeconds"], meta["preset"]), ("browser", 300, "tessa"))

    def test_link_key(self):
        with self.env(DEMO_KEY="abc123"):
            self.assertEqual(self.client.get("/call").status_code, 404)
            self.assertEqual(self.client.get("/call?k=wrong").status_code, 404)
            self.assertEqual(self.client.get("/call?k=abc123").status_code, 200)
            self.assertEqual(self.token("wrong").status_code, 404)
            self.assertEqual(self.token("abc123").status_code, 200)

    def test_per_ip_limit(self):
        with self.env(DEMO_CALLS_PER_IP="2", DEMO_MAX_CONCURRENT="10", BEHIND_PROXY="1"):
            self.assertEqual(self.token(ip="1.1.1.1").status_code, 200)
            self.assertEqual(self.token(ip="1.1.1.1").status_code, 200)
            self.assertEqual(self.token(ip="1.1.1.1").status_code, 429)
            # A spoofed first entry doesn't dodge it; the proxy's last entry counts.
            self.assertEqual(self.token(ip="9.9.9.9, 1.1.1.1").status_code, 429)
            self.assertEqual(self.token(ip="2.2.2.2").status_code, 200)

    def test_concurrency_cap_and_slots_free_up(self):
        with self.env(DEMO_MAX_CONCURRENT="1", DEMO_CALLS_PER_IP="10", DEMO_MAX_SECONDS="60"):
            self.assertEqual(self.token().status_code, 200)
            self.assertEqual(self.token().status_code, 503)
            with mock.patch("demo_call.time.time", return_value=__import__("time").time() + 121):
                self.assertEqual(self.token().status_code, 200)

    def test_refuses_without_livekit_credentials(self):
        with self.env(LIVEKIT_API_SECRET=""):
            self.assertEqual(self.token().status_code, 503)


class DemoPresetInPanelTests(DemoCallTests):
    """The demo avatar is picked in the panel, not in env."""

    AUTH = {"Authorization": "Basic " + base64.b64encode(b"admin:s3cret").decode()}

    def env(self, **extra):
        return mock.patch.dict(os.environ, {**LK, "DEMO_IMAGE": "", "DEMO_PRESET": "", **extra})

    def test_panel_save_and_read_needs_login(self):
        with self.env():
            self.assertEqual(self.client.get("/api/demo").status_code, 401)
            self.assertEqual(self.client.post("/api/demo", json={"preset": "tessa"}).status_code, 401)
            self.assertEqual(self.client.get("/api/demo", headers=self.AUTH).json()["preset"], "")
            r = self.client.post("/api/demo", json={"preset": "tessa"}, headers=self.AUTH)
            self.assertEqual(r.status_code, 200)
            self.assertEqual((r.json()["preset"], r.json()["image"]), ("tessa", "tessa.jpg"))
            self.assertEqual(self.client.get("/api/demo", headers=self.AUTH).json()["preset"], "tessa")
            self.assertEqual(
                self.client.post("/api/demo", json={"preset": "nope"}, headers=self.AUTH).status_code, 400)
        # Saving a preset afterwards keeps the demo choice.
        catalog.save_preset({"id": "other", "label": "Other"})
        self.assertEqual(catalog.get_demo_preset(), "tessa")

    def test_token_and_poster_use_preset_image(self):
        catalog.set_demo_preset("tessa")
        with self.env(DEMO_IMAGE="demo.jpg", DEMO_PRESET="nopic"):  # panel choice beats env
            r = self.token().json()
            self.assertEqual(self.client.get("/call/poster").content, b"TESSA")
        meta = json.loads(claims(r["token"])["roomConfig"]["agents"][0]["metadata"])
        self.assertEqual((meta["preset"], meta["image"], meta["frame"]), ("tessa", "tessa.jpg", "wide"))

    def test_refuses_when_none_chosen_or_face_missing(self):
        with self.env():
            self.assertEqual(self.token().status_code, 503)
            self.assertEqual(self.client.get("/call/poster").status_code, 404)
        catalog.set_demo_preset("nopic")
        with self.env():
            self.assertEqual(self.token().status_code, 503)

    # Inherited env-based tests need a face; give them the panel choice.
    def setUp(self):
        super().setUp()
        if not self._testMethodName.startswith(("test_panel", "test_token_and", "test_refuses_when")):
            catalog.set_demo_preset("tessa")

    def test_refuses_without_demo_face(self):
        pass  # covered by test_refuses_when_none_chosen_or_face_missing


class AgentConfigTests(unittest.TestCase):
    def test_browser_mode_needs_no_meeting_url(self):
        import agent

        ctx = mock.Mock()
        ctx.job.metadata = json.dumps({"mode": "browser", "maxSeconds": 600})
        cfg = agent._load_session_config(ctx)
        self.assertEqual((cfg["mode"], cfg["max_seconds"]), ("browser", 600))

        ctx.job.metadata = json.dumps({"bot_name": "x"})
        with self.assertRaises(ValueError):
            agent._load_session_config(ctx)

    def test_frame_choice(self):
        import agent

        ctx = mock.Mock()
        ctx.job.metadata = json.dumps({"mode": "browser", "frame": "wide"})
        with mock.patch.dict(os.environ, {"AVATAR_IMAGE_WIDE": "tessa-wide.jpg"}):
            cfg = agent._load_session_config(ctx)
        self.assertEqual((cfg["frame"], cfg["image"]), ("wide", "tessa-wide.jpg"))
        self.assertEqual(agent.FRAMES[cfg["frame"]], "1x1")
        self.assertEqual(agent.resolve_frame("16x9"), "portrait")  # LemonSlice has no 16:9


if __name__ == "__main__":
    unittest.main()

import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.exceptions import HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from app_config import DEFAULT_CONFIG
from web import server


ROOT = Path(__file__).resolve().parents[1]


class ReactWebUITests(unittest.TestCase):
    def test_react_form_covers_every_config_key_once(self):
        source = (ROOT / "frontend/src/app/configFields.ts").read_text(encoding="utf-8")
        configured = re.findall(r"(?:text|secret|number|toggle|select)\('([^']+)'", source)
        self.assertEqual(len(configured), len(set(configured)), "Duplicate React config field")
        self.assertEqual(set(configured), set(DEFAULT_CONFIG))
        self.assertEqual(len(configured), len(DEFAULT_CONFIG))

    def test_react_entrypoint_and_api_are_wired(self):
        entry = (ROOT / "frontend/src/main.tsx").read_text(encoding="utf-8")
        app = (ROOT / "frontend/src/app/App.tsx").read_text(encoding="utf-8")
        html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
        self.assertIn("createRoot", entry)
        self.assertIn("./app/App", entry)
        self.assertIn("./styles.css", entry)
        self.assertIn('id="root"', html)
        for path in (
            "/api/config", "/api/status", "/api/logs", "/api/start", "/api/stop",
            "/api/health-check", "/api/proxy-pool/status", "/api/proxy-pool/reload",
            "/api/proxy-pool/test", "/api/proxy-pool/preflight",
            "/api/mailboxes/outlook", "/api/mailboxes/outlook/test",
        ):
            self.assertIn(path, app)

    def test_root_serves_compiled_react_index_when_available(self):
        with tempfile.TemporaryDirectory() as directory:
            dist = Path(directory)
            index = dist / "index.html"
            index.write_text("<html>react build</html>", encoding="utf-8")
            with patch.object(server, "FRONTEND_DIST", dist):
                response = server.index()
            self.assertIsInstance(response, FileResponse)
            self.assertEqual(Path(response.path), index)
            self.assertEqual(response.headers["cache-control"], "no-store")

    def test_root_keeps_legacy_webui_as_build_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(server, "FRONTEND_DIST", Path(directory)):
                response = server.index()
            self.assertIsInstance(response, HTMLResponse)
            self.assertIn("grok-register", response.body.decode("utf-8"))

    def test_asset_route_serves_build_assets_and_rejects_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            assets = Path(directory)
            bundle = assets / "app.js"
            bundle.write_text("console.log('react')", encoding="utf-8")
            with patch.object(server, "FRONTEND_ASSETS", assets):
                response = server.frontend_asset("app.js")
                with self.assertRaises(HTTPException) as raised:
                    server.frontend_asset("../index.html")
            self.assertIsInstance(response, FileResponse)
            self.assertEqual(Path(response.path), bundle)
            self.assertEqual(raised.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()

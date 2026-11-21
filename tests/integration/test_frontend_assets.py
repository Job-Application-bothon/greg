"""Integration tests for static HTML/CSS/ES module serving, MIME headers, and preserved UI copy."""
from __future__ import annotations

import threading
import unittest
import urllib.request
from pathlib import Path

from greg.api.server import create_server
from greg.domain.sessions import SESSIONS

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "src" / "greg" / "static"


class FrontendAssetsIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        SESSIONS.clear_all()
        self.server = create_server(host="127.0.0.1", port=0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        SESSIONS.clear_all()

    def test_serves_modular_static_assets_and_fonts(self) -> None:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/assets/InterVariable.woff2", timeout=5
        ) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("Content-Type"), "font/woff2")
            self.assertGreater(len(resp.read()), 1000)

        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/static/fonts/InterVariable.woff2", timeout=5
        ) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("Content-Type"), "font/woff2")
            self.assertGreater(len(resp.read()), 1000)

        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("Cache-Control"), "no-store")
            html = resp.read().decode("utf-8")
            self.assertIn("Import Bank Statement (CSV)", html)
            self.assertIn('/static/css/base.css', html)
            self.assertIn('/static/js/app.js', html)

        for css_name in ("base.css", "forecast.css", "messenger.css"):
            with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/static/css/{css_name}", timeout=5
            ) as resp:
                self.assertEqual(resp.status, 200)
                self.assertEqual(resp.headers.get("Content-Type"), "text/css; charset=utf-8")
                self.assertEqual(resp.headers.get("Cache-Control"), "no-store")
                resp.read()

        for js_name in (
            "app.js",
            "api.js",
            "format.js",
            "csv-mapping.js",
            "chart.js",
            "controls.js",
            "chat.js",
            "whatif.js",
        ):
            with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/static/js/{js_name}", timeout=5
            ) as resp:
                self.assertEqual(resp.status, 200)
                self.assertEqual(
                    resp.headers.get("Content-Type"), "application/javascript; charset=utf-8"
                )
                self.assertEqual(resp.headers.get("Cache-Control"), "no-store")
                resp.read()

    def test_frontend_labels_design_features_and_step_c_noop(self) -> None:
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        css_all = "\n".join(
            p.read_text(encoding="utf-8") for p in sorted((STATIC_DIR / "css").glob("*.css"))
        )
        js_all = "\n".join(
            p.read_text(encoding="utf-8") for p in sorted((STATIC_DIR / "js").glob("*.js"))
        )
        combined = "\n".join((html, css_all, js_all))

        for forbidden in (
            "Plain-language monthly income &amp; expense breakdown and cash forecast",
            "(; or , separated)",
            "Group / Category",
            "Choose any month to inspect who paid you and who you paid",
            "Money In",
            "Money Out",
            "Left Over This Month",
            "— Details",
            "Manually Add an Income or Expense",
            "Grouped by category",
            "Liquidity Agent — Future Cash Forecast",
            "1. Import",
            "2. Manually",
            "3. Monthly",
            "4. Liquidity",
            "5. Ask Greg",
            "Month End (",
            "Past balance",
            "/api/approve-credit",
        ):
            self.assertNotIn(forbidden, combined, msg=f"Unexpected forbidden UI text: {forbidden}")

        for required in (
            "Import Bank Statement (CSV)",
            "Add Manually",
            "Monthly Overview",
            ">Income<",
            ">Expenses<",
            ">Profit<",
            ">Forecast<",
            '<option value="Salaries">Salaries</option>',
            '<option value="Taxes">Taxes</option>',
            '<option value="Other">Other</option>',
            'id="chart-tooltip"',
            'id="chart-crosshair"',
            'id="chart-hover-dot"',
            'id="chart-plot-area"',
            'id="btn-zoom-in"',
            'id="btn-zoom-out"',
            'id="btn-zoom-reset"',
            'id="interval-select"',
            'id="chart-scroll"',
            'id="chart-range-label"',
            'class="chart-legend hidden"',
            'id="forecast-mode-subtitle"',
            'id="forecast-method-bar"',
            "Method Chosen",
            "Own Model (Neural Network)",
            "StatsForecast",
            'id="chart-hover-pess-dot"',
            'id="whatif-panel"',
            'id="chart-hover-whatif-dot"',
            "What-if scenario",
            'id="csv-mapping-box"',
            'id="map-date"',
            'id="map-amount-mode"',
            'id="map-income"',
            'id="map-expenses"',
            'id="map-default-currency"',
            'id="steps-toggle"',
            'id="forecast-steps"',
            'id="chat-launcher"',
            'id="greg-messenger"',
            "number-stepper",
            "Step A · Analyzed Automatically",
            "Step B · Recommended Action",
            "Step C · Your Approval (Simulated)",
            "Monthly seasonal forecast",
            ">Today<",
            "X-Greg-Session",
            "sessionStorage",
        ):
            self.assertIn(required, combined, msg=f"Missing required UI text: {required}")


if __name__ == "__main__":
    unittest.main()

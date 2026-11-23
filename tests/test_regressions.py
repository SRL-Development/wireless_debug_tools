import json
import http.client
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "wireless_app"
sys.path.insert(0, str(APP_DIR))


class HistoryRotationTests(unittest.TestCase):
    def test_history_file_is_compacted_after_overflow_buffer(self):
        from desktop import History

        with tempfile.TemporaryDirectory() as tmp:
            history = History(tmp, max_items=3)
            for i in range(203):
                history.append({"point": "p%d" % i})

            with open(history.path, "r", encoding="utf-8") as f:
                rows = [json.loads(line) for line in f if line.strip()]

            self.assertEqual(3, len(rows))
            self.assertEqual(["p200", "p201", "p202"], [row["point"] for row in rows])


@unittest.skipUnless(os.name == "nt", "Native Wi-Fi layout is Windows-only")
class WlanLayoutTests(unittest.TestCase):
    def test_wlan_bss_entry_offsets_match_wlanapi_header(self):
        import wlan_scan

        self.assertEqual(360, wlan_scan.BSS_ENTRY_SIZE)
        self.assertEqual(40, wlan_scan.OFF_BSSID)
        self.assertEqual(56, wlan_scan.OFF_RSSI)
        self.assertEqual(60, wlan_scan.OFF_LINK_QUALITY)
        self.assertEqual(66, wlan_scan.OFF_BEACON_PERIOD)
        self.assertEqual(88, wlan_scan.OFF_CAPABILITY)
        self.assertEqual(92, wlan_scan.OFF_FREQ_KHZ)
        self.assertEqual(352, wlan_scan.OFF_IE_OFFSET)
        self.assertEqual(356, wlan_scan.OFF_IE_SIZE)


class PropagationGeometryTests(unittest.TestCase):
    def test_ap_height_uses_three_dimensional_distance(self):
        import app

        low = app.prep_aps([{
            "x": 100, "y": 100, "tx_power": 20, "gain": 4,
            "height_m": 2.0, "mounting": "ceiling", "band": "5",
        }], "5")[0]
        high = app.prep_aps([{
            "x": 100, "y": 100, "tx_power": 20, "gain": 4,
            "height_m": 10.0, "mounting": "ceiling", "band": "5",
        }], "5")[0]

        low_signal = app.predict_at(150, 100, low, [], 52.0, 3.2, 10, 10)
        high_signal = app.predict_at(150, 100, high, [], 52.0, 3.2, 10, 10)
        self.assertGreater(low_signal, high_signal)

    def test_mounting_method_applies_expected_loss(self):
        import app

        base = {"x": 100, "y": 100, "tx_power": 20, "gain": 4,
                "height_m": 4.5, "band": "5"}
        ceiling = app.prep_aps([dict(base, mounting="ceiling")], "5")[0]
        cabinet = app.prep_aps([dict(base, mounting="cabinet")], "5")[0]
        ceiling_signal = app.predict_at(200, 100, ceiling, [], 52.0, 3.2, 10, 10)
        cabinet_signal = app.predict_at(200, 100, cabinet, [], 52.0, 3.2, 10, 10)
        self.assertAlmostEqual(6.0, ceiling_signal - cabinet_signal, places=6)

    def test_independent_xy_scales_change_physical_distance(self):
        import app

        ap = app.prep_aps([{
            "x": 0, "y": 0, "tx_power": 20, "gain": 4,
            "height_m": 1.5, "mounting": "ceiling", "band": "5",
        }], "5")[0]
        along_x = app.predict_at(100, 0, ap, [], 52.0, 3.2, 10, 20)
        along_y = app.predict_at(0, 100, ap, [], 52.0, 3.2, 10, 20)
        self.assertGreater(along_y, along_x)


class LocalHttpSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.old_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = cls.tmp.name

        import app

        cls.app = app
        cls.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)
        if cls.old_appdata is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = cls.old_appdata
        cls.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        payload = response.read()
        result = response.status, dict(response.getheaders()), payload
        conn.close()
        return result

    def session_cookie(self):
        status, headers, _body = self.request("GET", "/")
        self.assertEqual(200, status)
        return headers["Set-Cookie"].split(";", 1)[0]

    def test_post_requires_session_cookie(self):
        status, _headers, _body = self.request(
            "POST", "/api/unknown", body="{}",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(403, status)

    def test_valid_session_reaches_router(self):
        status, _headers, _body = self.request(
            "POST", "/api/unknown", body="{}",
            headers={"Content-Type": "application/json", "Cookie": self.session_cookie()},
        )
        self.assertEqual(404, status)

    def test_cross_origin_post_is_rejected(self):
        status, _headers, _body = self.request(
            "POST", "/api/unknown", body="{}",
            headers={
                "Content-Type": "application/json",
                "Cookie": self.session_cookie(),
                "Origin": "https://example.invalid",
            },
        )
        self.assertEqual(403, status)

    def test_security_headers_are_present(self):
        status, headers, _body = self.request("GET", "/api/health")
        self.assertEqual(200, status)
        self.assertEqual("DENY", headers.get("X-Frame-Options"))
        self.assertEqual("nosniff", headers.get("X-Content-Type-Options"))


if __name__ == "__main__":
    unittest.main()

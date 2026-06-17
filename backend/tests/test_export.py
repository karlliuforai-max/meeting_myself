from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote
from zipfile import ZipFile

from fastapi.testclient import TestClient


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import api.routes as routes  # noqa: E402
import pipeline.engine as engine  # noqa: E402
from main import app  # noqa: E402
from storage.session_store import SessionStore  # noqa: E402


class ExportRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = SessionStore(root=Path(self.tmp.name))
        self.meta = self.store.create("business_school", "课堂/测试")
        self.orig_routes_store = routes.session_store
        self.orig_engine_store = engine.session_store
        routes.session_store = self.store
        engine.session_store = self.store
        self.client = TestClient(app)

    def tearDown(self):
        routes.session_store = self.orig_routes_store
        engine.session_store = self.orig_engine_store
        self.tmp.cleanup()

    def _artifact_url(self, name: str) -> str:
        return f"/api/sessions/{self.meta.id}/artifacts/{quote(name)}/download"

    def test_download_current_artifact(self):
        self.store.write_artifact(self.meta.id, "实录.md", "# 实录\n\n内容", note="生成")

        res = self.client.get(self._artifact_url("实录.md"))

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content.decode("utf-8"), "# 实录\n\n内容")
        self.assertIn("attachment", res.headers["content-disposition"])
        self.assertIn("filename*=UTF-8''", res.headers["content-disposition"])

    def test_download_artifact_version(self):
        self.store.write_artifact(self.meta.id, "撷要.md", "# v1", note="生成")
        self.store.write_artifact(self.meta.id, "撷要.md", "# v2", note="修订")

        res = self.client.get(
            f"/api/sessions/{self.meta.id}/artifacts/{quote('撷要.md')}/versions/1/download"
        )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content.decode("utf-8"), "# v1")
        self.assertIn("v1", res.headers["content-disposition"])

    def test_versions_merge_legacy_and_current_names(self):
        self.store.write_artifact(self.meta.id, "逐字稿.md", "# old transcript", note="旧版")
        self.store.write_artifact(self.meta.id, "实录.md", "# new transcript 1", note="新版1")
        self.store.write_artifact(self.meta.id, "实录.md", "# new transcript 2", note="新版2")

        res = self.client.get(f"/api/sessions/{self.meta.id}/artifacts/{quote('实录.md')}")

        self.assertEqual(res.status_code, 200)
        versions = res.json()["versions"]
        self.assertEqual([v["version"] for v in versions], [1, 2, 3])
        self.assertEqual(versions[0]["source_name"], "逐字稿.md")
        self.assertEqual(versions[1]["source_name"], "实录.md")

        old = self.client.get(
            f"/api/sessions/{self.meta.id}/artifacts/{quote('实录.md')}/versions/1"
        )
        latest = self.client.get(
            f"/api/sessions/{self.meta.id}/artifacts/{quote('实录.md')}/versions/3"
        )
        self.assertEqual(old.json()["content"], "# old transcript")
        self.assertEqual(latest.json()["content"], "# new transcript 2")

    def test_bundle_exports_generated_artifacts_only(self):
        self.store.save_input(self.meta.id, "原始转写.txt", b"raw")
        self.store.write_artifact(self.meta.id, "实录.md", "# 实录", note="生成")
        self.store.write_artifact(self.meta.id, "脉络.mmd", "flowchart LR\nA-->B", note="生成")

        res = self.client.get(f"/api/sessions/{self.meta.id}/exports/bundle")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers["content-type"], "application/zip")
        zf = ZipFile(io.BytesIO(res.content))
        self.assertEqual(zf.namelist(), ["实录.md", "脉络.mmd"])
        self.assertEqual(zf.read("实录.md").decode("utf-8"), "# 实录")
        self.assertNotIn("原始转写.txt", zf.namelist())

    def test_empty_bundle_returns_404(self):
        res = self.client.get(f"/api/sessions/{self.meta.id}/exports/bundle")

        self.assertEqual(res.status_code, 404)
        self.assertIn("暂无可导出的产出", res.text)


if __name__ == "__main__":
    unittest.main()

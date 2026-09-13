import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "registry" / "samples" / "catalog.json"


class CatalogSampleTest(unittest.TestCase):
    def test_sample_structure(self) -> None:
        data = json.loads(CATALOG.read_text(encoding="utf-8"))
        self.assertEqual(data["schemaVersion"], 1)
        self.assertEqual(data["channel"], "stable")
        self.assertRegex(data["revision"], r"^\d{8}T\d{6}Z$")
        self.assertGreaterEqual(len(data["applications"]), 2)

        by_id = {app["id"]: app for app in data["applications"]}
        self.assertIn("net.typixnode.notes", by_id)
        self.assertIn("com.example.media-studio", by_id)
        self.assertIn("cm4", by_id["com.example.media-studio"]["versions"][0]["compatibility"]["cores"])

    def test_artifacts_are_fully_described(self) -> None:
        data = json.loads(CATALOG.read_text(encoding="utf-8"))
        for app in data["applications"]:
            for version in app["versions"]:
                artifact = version["artifact"]
                self.assertEqual(artifact["arch"], "arm64")
                self.assertRegex(artifact["filename"], r"_arm64\.deb$")
                self.assertRegex(artifact["sha256"], r"^[0-9a-f]{64}$")
                self.assertGreater(artifact["sizeBytes"], 0)
                self.assertTrue(artifact["url"].startswith("https://"))


if __name__ == "__main__":
    unittest.main()

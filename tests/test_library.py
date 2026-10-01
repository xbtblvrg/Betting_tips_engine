import json
from pathlib import Path
import tempfile
import unittest
from betting_engine.library import ingest, search


class LibraryTests(unittest.TestCase):
    def test_extraction_search_and_honest_coverage(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "books"
            source.mkdir()
            (source / "research.txt").write_text("Kelly criterion is a sizing rule.", encoding="utf-8")
            (source / "unsupported.epub").write_bytes(b"placeholder")
            output = root / "index"
            manifest = ingest(source, output)
            self.assertEqual(len(manifest["files"]), 2)
            self.assertTrue(all(r["read_status"] == "not_read" for r in manifest["files"]))
            self.assertEqual(len(search(output / "library.sqlite", "Kelly")), 1)
            self.assertEqual(json.loads((output / "manifest.json").read_text())["files"], manifest["files"])
            ingest(source, output)
            self.assertEqual(len(search(output / "library.sqlite", "Kelly")), 1)
            (source / "research.txt").unlink()
            ingest(source, output)
            self.assertEqual(search(output / "library.sqlite", "Kelly"), [])

    def test_missing_source_is_not_empty_success(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(FileNotFoundError):
                ingest(Path(temp) / "missing", Path(temp) / "index")

    def test_output_cannot_be_reingested(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                ingest(Path(temp), Path(temp) / "output")


if __name__ == "__main__":
    unittest.main()

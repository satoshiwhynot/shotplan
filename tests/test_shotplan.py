import csv
from decimal import Decimal
import io
from pathlib import Path
import tempfile
import unittest
import shotplan


class ShotplanTests(unittest.TestCase):
    def fixture(self, changes=None, extra=None):
        row = dict(shot_id="S01", duration_seconds="3.5", description="A form, in light", camera="Locked", prompt="Keep <shape> & light", attempts="3", unit_cost="0.10", reference="")
        row.update(changes or {})
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=list(row))
        writer.writeheader(); writer.writerow(row)
        if extra:
            writer.writerow(dict(row, **extra))
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "film.csv"; path.write_text(output.getvalue(), encoding="utf-8")
        return path

    def test_quoted_fields_and_exact_decimal_budget(self):
        shots = shotplan.read_shots(self.fixture())
        self.assertEqual(shots[0]["description"], "A form, in light")
        summary = shotplan.summarize(shots, "EUR")
        self.assertEqual(summary["estimated_generation_cost"], "0.30")
        self.assertEqual(summary["duration_seconds"], "3.5")

    def test_html_escapes_user_text(self):
        shots = shotplan.read_shots(self.fixture({"description": '<script>alert("x")</script>'}))
        page = shotplan.storyboard(shots, "<title>", shotplan.summarize(shots, "<USD>"))
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("&lt;USD&gt;", page)

    def test_invalid_numeric_inputs(self):
        for field, value in [("unit_cost", "NaN"), ("unit_cost", "-1"), ("duration_seconds", "0"), ("duration_seconds", "Infinity"), ("attempts", "1.5"), ("attempts", "0")]:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                shotplan.read_shots(self.fixture({field: value}))

    def test_duplicate_ids(self):
        with self.assertRaisesRegex(ValueError, "duplicate shot_id"):
            shotplan.read_shots(self.fixture(extra={"description": "Another frame"}))

    def test_reference_cannot_escape_csv_directory(self):
        with self.assertRaisesRegex(ValueError, "inside the CSV directory"):
            shotplan.read_shots(self.fixture({"reference": "../private.png"}))

    def test_reference_is_embedded(self):
        path = self.fixture({"reference": "frame.png"})
        (path.parent / "frame.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        self.assertTrue(shotplan.read_shots(path)[0]["image"].startswith("data:image/png;base64,"))

    def test_empty_missing_and_malformed_csv(self):
        for text in ["", "shot_id\nS01", 'shot_id,duration_seconds,description,camera,prompt,attempts,unit_cost\nS01,3,"unfinished']:
            path = self.fixture(); path.write_text(text, encoding="utf-8")
            with self.subTest(text=text), self.assertRaises((ValueError, csv.Error)):
                shotplan.read_shots(path)

    def test_cli_generates_all_three_files(self):
        path = self.fixture(); out = path.parent / "build"
        self.assertEqual(shotplan.main([str(path), "--out", str(out), "--title", "Form Study"]), 0)
        self.assertEqual({p.name for p in out.iterdir()}, {"storyboard.html", "summary.json", "prompts.md"})
        self.assertIn("Form Study", (out / "storyboard.html").read_text())

    def test_invalid_input_does_not_overwrite_output(self):
        path = self.fixture({"unit_cost": "-2"}); out = path.parent / "build"; out.mkdir()
        (out / "summary.json").write_text("keep")
        self.assertEqual(shotplan.main([str(path), "--out", str(out)]), 2)
        self.assertEqual((out / "summary.json").read_text(), "keep")


if __name__ == "__main__":
    unittest.main()

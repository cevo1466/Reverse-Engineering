from collections import Counter
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "reverse-engineer-and-rebuild" / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("extract_tokens", SCRIPTS / "extract_tokens.py")
tokens = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tokens)


class ColorTests(unittest.TestCase):
    def test_rgb_forms(self):
        self.assertEqual(tokens.normalise_color("rgb(255, 0, 16)"), "#ff0010")
        self.assertEqual(tokens.normalise_color("rgba(0, 0, 0, 0.5)"), "#00000080")
        self.assertEqual(tokens.normalise_color("rgb(10 20 30 / 50%)"), "#0a141e80")
        self.assertEqual(tokens.normalise_color("color(srgb 1 0 0)"), "#ff0000")

    def test_transparent_and_empty_are_none(self):
        for value in ("rgba(0, 0, 0, 0)", "transparent", "", None, 5):
            self.assertIsNone(tokens.normalise_color(value), repr(value))

    def test_unknown_format_is_kept(self):
        self.assertEqual(tokens.normalise_color("oklch(0.7 0.1 200)"), "oklch(0.7 0.1 200)")


class HelperTests(unittest.TestCase):
    def test_pixels_and_number_text(self):
        self.assertEqual(tokens.pixels("16px"), 16.0)
        self.assertEqual(tokens.pixels("1.5px"), 1.5)
        self.assertIsNone(tokens.pixels("normal"))
        self.assertIsNone(tokens.pixels("2em"))
        self.assertEqual(tokens.number_text(16.0), "16")
        self.assertEqual(tokens.number_text(1.5), "1.5")

    def test_rank_orders_by_count_then_value(self):
        self.assertEqual(tokens.rank(Counter({"b": 2, "a": 2, "c": 5, "d": 1}), 3), [{"value": "c", "count": 5}, {"value": "a", "count": 2}, {"value": "b", "count": 2}])

    def test_parse_viewport(self):
        self.assertEqual(tokens.parse_viewport("1280x720"), {"width": 1280, "height": 720})
        for text in ("0x5", "5", "4001x5", "axb"):
            with self.assertRaises(ValueError, msg=text):
                tokens.parse_viewport(text)

    def test_first_family(self):
        self.assertEqual(tokens.first_family('"Inter", system-ui, sans-serif'), "Inter")


RAW = {
    "total": 4, "truncated": False, "unreadable": 1, "variables": {"--b": "2", "--a": "#fff"},
    "elements": [
        {"backgroundColor": "rgb(16, 32, 48)", "borderRadius": "0px", "boxShadow": "none", "margin": ["0px", "auto", "8px", "0px"], "padding": ["24px", "24px", "24px", "24px"], "gap": []},
        {"backgroundColor": "rgba(0, 0, 0, 0)", "borderRadius": "12px", "boxShadow": "rgba(0, 0, 0, 0.4) 0px 4px 12px 0px", "margin": ["16px"] * 4, "padding": ["0px"] * 4, "gap": ["8px", "normal"],
         "color": "rgb(255, 255, 255)", "fontFamily": '"Inter", sans-serif', "fontSize": "16px", "fontWeight": "400", "lineHeight": "24px", "letterSpacing": "normal", "borderColor": "rgb(1, 2, 3)"},
        {"backgroundColor": "rgb(16, 32, 48)", "borderRadius": "12px", "boxShadow": "none", "margin": [], "padding": [], "gap": [],
         "color": "rgb(255, 255, 255)", "fontFamily": '"Inter", sans-serif', "fontSize": "32px", "fontWeight": "700", "lineHeight": "normal", "letterSpacing": "-0.5px"},
    ],
}


class AggregateTests(unittest.TestCase):
    def setUp(self):
        self.result = tokens.aggregate(RAW)

    def test_colors(self):
        self.assertEqual(self.result["colors"]["background"], [{"value": "#102030", "count": 2}])
        self.assertEqual(self.result["colors"]["text"], [{"value": "#ffffff", "count": 2}])
        self.assertEqual(self.result["colors"]["border"], [{"value": "#010203", "count": 1}])

    def test_typography(self):
        typography = self.result["typography"]
        self.assertEqual(typography["families"], [{"value": "Inter", "count": 2}])
        self.assertEqual({row["value"] for row in typography["sizes"]}, {"16px", "32px"})
        self.assertEqual({row["value"] for row in typography["line_heights"]}, {"24px", "normal"})
        self.assertEqual({row["value"] for row in typography["letter_spacing"]}, {"normal", "-0.5px"})

    def test_spacing_radii_shadows(self):
        spacing = {row["value"]: row["count"] for row in self.result["spacing"]}
        self.assertEqual(spacing, {"24px": 4, "16px": 4, "8px": 2})
        self.assertEqual(self.result["radii"], [{"value": "12px", "count": 2}])
        self.assertEqual(self.result["shadows"], [{"value": "rgba(0, 0, 0, 0.4) 0px 4px 12px 0px", "count": 1}])

    def test_counts_and_variables(self):
        self.assertEqual((self.result["elements_read"], self.result["visible_elements"], self.result["unreadable_stylesheets"]), (3, 4, 1))
        self.assertEqual(list(self.result["css_variables"]), ["--a", "--b"])

    def test_limit(self):
        self.assertEqual(len(tokens.aggregate(RAW, limit=1)["spacing"]), 1)

    def test_empty_input(self):
        empty = tokens.aggregate({})
        self.assertEqual((empty["elements_read"], empty["spacing"], empty["colors"]["text"]), (0, [], []))

    def test_markdown_lists_groups_and_escapes_pipes(self):
        raw = {"elements": [], "variables": {"--x": "a | b"}}
        text = tokens.markdown(tokens.aggregate(raw), "http://x/", {"width": 1, "height": 2})
        self.assertIn("a \\| b", text)
        self.assertIn("1x2", text)
        self.assertIn("### Colour: text", tokens.markdown(self.result, "http://x/", {"width": 1280, "height": 720}))


class CommandTests(unittest.TestCase):
    def test_bad_arguments_exit_two(self):
        with tempfile.TemporaryDirectory() as root:
            out = str(Path(root) / "out")
            self.assertEqual(tokens.main(["--url", "http://127.0.0.1:1/", "--output", out, "--viewport", "nope"]), 2)
            self.assertEqual(tokens.main(["--url", "ftp://x/", "--output", out]), 2)
            self.assertEqual(tokens.main(["--url", "http://127.0.0.1:1/", "--output", out, "--top", "0"]), 2)
            self.assertFalse(Path(out).exists())


if __name__ == "__main__":
    unittest.main()

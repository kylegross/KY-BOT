import ast
import io
import unittest
from pathlib import Path

from PIL import Image, ImageChops

from ky_bot.advice.panels import heading_image
from ky_bot.advice.store import defaults, validate
from ky_bot.checklists.artwork import title_image as checklist_title
from ky_bot.confessions.headings import title_image
from ky_bot.views.menu_strips import category_files


class HeadingLineColorTests(unittest.TestCase):
    def test_hex_input_specs_allow_optional_hash(self):
        root = Path(__file__).parents[1] / "src" / "ky_bot"
        for path in root.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
                if not isinstance(node, ast.Tuple) or len(node.elts) < 3:
                    continue
                key, label = node.elts[:2]
                if not (
                    isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and isinstance(label, ast.Constant)
                    and isinstance(label.value, str)
                ):
                    continue
                if "color" not in key.value and "colour" not in key.value:
                    continue
                for item in node.elts[2:]:
                    if isinstance(item, ast.Constant) and type(item.value) is int:
                        self.assertGreaterEqual(item.value, 7, f"{path}:{node.lineno}")

    def test_line_color_changes_rules_without_recoloring_letters(self):
        for render in (
            lambda c: title_image("WANT TO HELP?", "silver_neon", "#228855", line_color=c),
            lambda c: checklist_title("WANT TO HELP?", "silver_neon", "#228855", c),
            lambda c: heading_image(
                "ASK FOR ADVICE", defaults() | {"text_color": "#228855", "line_color": c}, True
            ),
        ):
            before = Image.open(io.BytesIO(render(None))).convert("RGBA")
            after = Image.open(io.BytesIO(render("#FF0088"))).convert("RGBA")
            self.assertEqual(before.getchannel("A").tobytes(), after.getchannel("A").tobytes())
            changed = ImageChops.difference(before, after).convert("RGB").getbbox()
            self.assertIsNotNone(changed)
            self.assertLessEqual(changed[3] - changed[1], 2)
            self.assertLessEqual(changed[1], before.height // 2)
            self.assertGreater(changed[3], before.height // 2)

    def test_askdm_strips_support_independent_line_color(self):
        original = category_files(["ACCESS & PRIVACY"], "silver_neon", "#228855")
        changed = category_files(["ACCESS & PRIVACY"], "silver_neon", "#228855", "#FF0088")
        try:
            before = Image.open(original[0].fp).convert("RGBA")
            after = Image.open(changed[0].fp).convert("RGBA")
            self.assertEqual(before.getchannel("A").tobytes(), after.getchannel("A").tobytes())
            self.assertNotEqual(before.tobytes(), after.tobytes())
        finally:
            for item in original + changed:
                item.close()

    def test_advice_accepts_hash_and_independent_lines(self):
        config = validate(defaults() | {"color": "#18AA77", "line_color": "FF0088"})
        self.assertEqual(config["color"], "18AA77")
        self.assertEqual(config["line_color"], "#FF0088")

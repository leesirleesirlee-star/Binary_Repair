"""Keep both standalone preview pages portable after asset moves."""

from html.parser import HTMLParser
from pathlib import Path
import re
import unittest


UI_ROOT = Path(__file__).resolve().parents[1] / "app" / "static" / "ui"
IMPORT = re.compile(r"""(?:\bfrom\s*|\bimport\s*\()['"]([^'"]+)['"]""")


class PageAssets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        key = {"link": "href", "script": "src"}.get(tag)
        if key and attributes.get(key):
            self.paths.append(attributes[key])


class PreviewAssetsTest(unittest.TestCase):
    def assert_local_file(self, origin, path):
        target = (origin / path).resolve()
        self.assertTrue(target.is_relative_to(UI_ROOT), path)
        self.assertTrue(target.is_file(), path)

    def test_entry_pages_reference_local_assets(self):
        for page in ("index.html", "workbench.html"):
            with self.subTest(page=page):
                assets = PageAssets()
                assets.feed((UI_ROOT / page).read_text(encoding="utf-8"))
                self.assertTrue(any(path.endswith(".mjs") for path in assets.paths))
                for path in assets.paths:
                    self.assert_local_file(UI_ROOT, path)

    def test_module_imports_resolve_after_reorganization(self):
        for module in UI_ROOT.rglob("*.mjs"):
            for path in IMPORT.findall(module.read_text(encoding="utf-8")):
                with self.subTest(module=module.name, path=path):
                    self.assert_local_file(module.parent, path)

    def test_both_theme_icon_assets_are_present(self):
        for theme in ("light", "dark"):
            self.assert_local_file(UI_ROOT, f"assets/mark-{theme}.svg")

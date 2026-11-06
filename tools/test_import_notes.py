"""Run with: python -m unittest discover -s tools -p test_import_notes.py"""
from pathlib import Path
import tempfile
import unittest

from import_notes import plan


class ImportTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / 'build' / 'import-tests'
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / 'incoming'
        self.root = self.base / 'repo'
        self.source.mkdir()
        (self.source / 'assets').mkdir()
        (self.source / 'assets/图 (1).png').write_bytes(b'image')
        (self.source / 'assets/图(1).png').write_bytes(b'image2')
        (self.source / 'assets/main.py').write_text('print(1)', encoding='utf-8')
        (self.source / 'assets/manual.pdf').write_bytes(b'pdf')
        (self.source / 'other.md').write_text('# Other', encoding='utf-8')

    def note(self, text):
        (self.source / 'main.md').write_text(text, encoding='utf-8')

    def test_links_classification_code_and_idempotence(self):
        original = '''# 导入
![图](assets/图%20%281%29.png "title")
[other](other.md#标题)
[code](assets/main.py?download=1#L1)
[manual][pdf]
[pdf]: assets/manual.pdf "说明"
![balanced](assets/图(1).png)
`![example](absent.png)`
```md
![example](absent.png)
```
'''
        self.note(original)
        result = plan(self.source, self.root, 'electronics/fpga', 'batch')
        rewritten = next(data.decode() for target, data, _ in result if target.name == 'main.md')
        self.assertIn('../../../../images/batch/assets/', rewritten)
        self.assertIn('../../../../code/batch/assets/main.py?download=1#L1', rewritten)
        self.assertIn('../../../../attachments/batch/assets/manual.pdf', rewritten)
        self.assertIn('(other.md#标题)', rewritten)
        self.assertIn('`![example](absent.png)`', rewritten)
        for target, data, _ in result:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        self.assertTrue(all(exists for _, _, exists in plan(self.source, self.root, 'electronics/fpga', 'batch')))
        self.assertEqual((self.source / 'main.md').read_text(encoding='utf-8'), original)
        # Exercise the site's actual Markdown URL validator with imported files.
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'website'))
        import build
        import markdown
        from unittest.mock import patch
        with patch.object(build, 'ROOT', self.root):
            for target, _, _ in result:
                if target.suffix == '.md':
                    md = markdown.Markdown(extensions=['extra', build.LinkExtension()])
                    md.source = target
                    md.page = target.relative_to(self.root / 'content').with_suffix('.html')
                    self.assertTrue(md.convert(target.read_text(encoding='utf-8')))

    def test_missing_external_and_collision_fail_before_writes(self):
        for link in ('missing.png', '../outside.pdf', '/absolute.png', 'file:///C:/a.png'):
            self.note(f'[bad]({link})')
            with self.assertRaises(ValueError):
                plan(self.source, self.root, 'imported', 'batch')
            self.assertFalse(self.root.exists())
        self.note('# OK')
        result = plan(self.source, self.root, 'imported', 'batch')
        target = result[0][0]
        target.parent.mkdir(parents=True)
        target.write_bytes(b'different')
        with self.assertRaises(ValueError):
            plan(self.source, self.root, 'imported', 'batch')
        self.assertEqual(target.read_bytes(), b'different')

    def test_unsupported_syntax_and_external_links(self):
        for text in ('![[图.png]]', '<img src="assets/图 (1).png">'):
            self.note(text)
            with self.assertRaises(ValueError):
                plan(self.source, self.root, 'imported', 'batch')
        self.note('[web](https://example.com/a?q=1#b)\n[anchor](#标题)')
        self.assertTrue(plan(self.source, self.root, 'imported', 'batch'))


if __name__ == '__main__':
    unittest.main()

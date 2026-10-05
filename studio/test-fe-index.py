"""The index must remain usable while resolving malformed/merged source."""
import subprocess
import sys
from pathlib import Path
import unittest


class ParserProgress(unittest.TestCase):
    def test_python_factory_classes_and_conditional_definitions_are_indexed(self):
        import fe_index as idx
        text = ('def build_client():\n'
                '    class Client:\n'
                '        async def goal_thread(self):\n'
                '            return None\n'
                '    if True:\n'
                '        def helper():\n'
                '            pass\n'
                '    return Client\n')
        syms, _, _ = idx.parse_python(text)
        by_name = {s[idx.QUAL]: s for s in syms}
        method = by_name['build_client.Client.goal_thread']
        self.assertEqual((method[idx.LINE], method[idx.END], method[idx.DEPTH]), (3, 4, 2))
        self.assertIn('build_client.helper', by_name)

    def test_type_outline_prefers_its_methods_to_default(self):
        script = r'''
import contextlib, io
import fe_index as fi
syms, _, _ = fi.parse_brace('impl Default for Look { fn default() {} }\nimpl Look { fn knobs() {} }', 'rust')
files = {'look.rs': {'syms': syms, 'lines': 2, 'doc': ''}}
for query, expected, absent in [('Look', 'knobs', 'default'),
                                ('impl Default for Look', 'default', 'knobs')]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert fi.cmd_outline(files, {'orphans': set()}, ['look.rs', query]) == 0
    assert expected in out.getvalue() and absent not in out.getvalue(), out.getvalue()
'''
        subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).parent, check=True, timeout=5)

    def test_stray_close_and_merge_conflicts_cannot_stall_indexing(self):
        script = """
import fe_index
assert list(fe_index.statements('}', 0, 1)) == [(0, 0, None, 1)]
source = '<<<<<<< Updated upstream\\nfn a() {}\\n=======\\nfn b() {}\\n>>>>>>> Stashed changes\\n}\\nfn c() {}\\n'
fe_index.parse_brace(source, 'rust')
syms, _, _ = fe_index.parse_brace('}\\nfn after() {}', 'rust')
assert any(s[fe_index.NAME] == 'after' for s in syms)
"""
        subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).parent, check=True, timeout=5)

    def test_javascript_items_and_masking(self):
        """The board UI (board_ui/app.js): braces inside templates (nested `${}`), strings
        and regex literals are masked, so items get their real spans."""
        script = r'''
import fe_index as fi
src = """// header comment
const A = { "k-1": x => `a ${b ? `in ${"}"} {` : '{'} }`, re: /["'{}`\\/]+/g, n: 1 };
function f(a) { return a / 2 / 3; }
const g = async () => { if (/}/.test(s)) return `}`; };
document.addEventListener("click", e => { const s = "{"; });
class C { m() { return 1; } static async n(x) { } }
let z = 3
"""
code, _ = fi.mask_js(src)
assert code.count("{") == code.count("}") == 7, (code.count("{"), code.count("}"))
syms, _, doc = fi.parse_js(src)
got = [(s[fi.K], s[fi.QUAL], s[fi.LINE]) for s in syms]
assert got == [("const", "A", 2), ("fn", "A.k-1", 2), ("key", "A.re", 2), ("key", "A.n", 2),
               ("fn", "f", 3), ("fn", "g", 4), ("listener", "document.click", 5),
               ("class", "C", 6), ("fn", "C.m", 6), ("fn", "C.n", 6), ("var", "z", 7)], got
assert doc == "header comment"
for bad in ("`${", "`a ${ `b ${ '", "/[", "const X = { a: `", "/* open"):
    fi.parse_js(bad)  # truncated input ends, it does not stall
'''
        subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).parent, check=True, timeout=5)


if __name__ == "__main__":
    unittest.main()

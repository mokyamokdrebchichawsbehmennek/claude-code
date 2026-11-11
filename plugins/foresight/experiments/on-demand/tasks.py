"""Thirty small command-line utilities: the task set of the on-demand experiment.

Each task has
  spec   what a user would ask for; the only thing the two agents see
  ref    a reference implementation, used only to check that the hidden cases
         are themselves consistent (`run.py validate`)
  cases  hidden reference cases; never shown to either agent

A case runs the tool with `args` and `stdin` in a fresh directory holding
`files`, and expects `out` on stdout (trailing whitespace ignored) and exit
status 0. `exit="nz"` expects any non-zero status; `json=True` compares stdout
as parsed JSON. Cases only check behaviour that the spec states.
"""

FMT = '''
def fmt(x, d=6):
    r = round(x, d)
    if r == int(r):
        return str(int(r))
    return f"{r:.{d}f}".rstrip("0").rstrip(".")
'''


def c(args=(), stdin="", files=None, out=None, exit=0, json=False):
    return {"args": list(args), "stdin": stdin, "files": files or {},
            "out": out, "exit": exit, "json": json}


def bad(*args, stdin="", files=None):
    return c(args, stdin, files, exit="nz")


TASKS = {}


def task(name, spec, ref, cases):
    TASKS[name] = {"spec": " ".join(spec.split()), "ref": ref, "cases": cases}


task("wordfreq", """
Read text from standard input. A word is a maximal run of ASCII letters (a-z, A-Z); convert words
to lowercase. Print one line per distinct word as `COUNT WORD` (single space), sorted by count
descending, then word ascending. If an integer argument N is given, print only the first N lines.
Empty input prints nothing.
""", r'''
import sys, re, collections
counts = collections.Counter(w.lower() for w in re.findall(r"[A-Za-z]+", sys.stdin.read()))
items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
if len(sys.argv) > 1:
    items = items[: int(sys.argv[1])]
for word, n in items:
    print(n, word)
''', [
    c(stdin="The cat and the hat. THE end\n", out="3 the\n1 and\n1 cat\n1 end\n1 hat"),
    c(stdin="don't stop\n", out="1 don\n1 stop\n1 t"),
    c(["2"], stdin="b a b a c\n", out="2 a\n2 b"),
    c(stdin="", out=""),
    c(stdin="x1y x-y\n", out="2 x\n2 y"),
])

task("csv2json", """
Usage: csv2json FILE. FILE is a CSV file whose first row is a header. Print a JSON array with one
object per data row, mapping each header name to the cell value as a string. Quoted fields
containing commas, quotes or newlines must be handled by the standard CSV rules. A file with only
a header prints `[]`. If FILE does not exist, exit with a non-zero status.
""", r'''
import sys, csv, json
try:
    with open(sys.argv[1], newline="") as fh:
        print(json.dumps(list(csv.DictReader(fh))))
except OSError:
    sys.exit(1)
''', [
    c(["p.csv"], files={"p.csv": 'name,age\nAnn,30\n"Smith, Bob",41\n'}, json=True,
      out='[{"name":"Ann","age":"30"},{"name":"Smith, Bob","age":"41"}]'),
    c(["p.csv"], files={"p.csv": "a,b\n"}, json=True, out="[]"),
    c(["p.csv"], files={"p.csv": 'q\n"say ""hi"""\n"two\nlines"\n'}, json=True,
      out='[{"q":"say \\"hi\\""},{"q":"two\\nlines"}]'),
    bad("missing.csv"),
])

task("dedupe", """
Read lines from standard input and print them without duplicates, keeping the first occurrence of
each line and the original order. With the option `-i`, lines that differ only in letter case are
duplicates, and the first spelling seen is the one printed. Otherwise lines are compared exactly:
leading and trailing spaces matter.
""", r'''
import sys
fold = "-i" in sys.argv[1:]
seen = set()
for line in sys.stdin.read().split("\n"):
    key = line.lower() if fold else line
    if key in seen:
        continue
    seen.add(key)
    print(line)
''', [
    c(stdin="a\nb\na\nc\nb\n", out="a\nb\nc"),
    c(["-i"], stdin="Foo\nfoo\nBar\nFOO\nbar\n", out="Foo\nBar"),
    c(stdin="Foo\nfoo\n", out="Foo\nfoo"),
    c(stdin=" a\na\n a\n", out=" a\na"),
])

task("dupfiles", """
Usage: dupfiles DIR. Recursively find regular files under DIR that have identical content. Print
one line per group of two or more identical files: their paths relative to DIR (with `/`
separators), sorted ascending and joined by a tab character. Sort the groups by their first path.
Empty files are identical to each other. Print nothing if there are no duplicates.
""", r'''
import sys, os, hashlib, collections
root = sys.argv[1]
groups = collections.defaultdict(list)
for base, _, names in os.walk(root):
    for name in names:
        path = os.path.join(base, name)
        with open(path, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        groups[digest].append(os.path.relpath(path, root).replace(os.sep, "/"))
for paths in sorted(sorted(g) for g in groups.values() if len(g) > 1):
    print("\t".join(paths))
''', [
    c(["d"], files={"d/a.txt": "x", "d/sub/b.txt": "x", "d/c.txt": "y"}, out="a.txt\tsub/b.txt"),
    c(["d"], files={"d/e1": "", "d/z/e2": "", "d/a": "1", "d/b": "1", "d/c": "2"},
      out="a\tb\ne1\tz/e2"),
    c(["d"], files={"d/a": "1", "d/b": "2"}, out=""),
    c(["d"], files={"d/3": "k", "d/1": "k", "d/2/x": "k"}, out="1\t2/x\t3"),
])

task("isodates", """
Read text from standard input and print every date written as YYYY-MM-DD that is a valid calendar
date, one per line, in order of appearance, repeating duplicates. A match must not be immediately
preceded or followed by a digit. Invalid dates such as 2023-02-30 or 2023-13-01 are skipped.
""", r'''
import sys, re, datetime
for m in re.finditer(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)", sys.stdin.read()):
    try:
        datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        continue
    print(m.group(0))
''', [
    c(stdin="on 2024-02-29 and 2023-02-29, also 2024-02-29.\n", out="2024-02-29\n2024-02-29"),
    c(stdin="id 12024-01-015 then x2024-01-01y\n", out="2024-01-01"),
    c(stdin="2023-13-01 2023-12-31 2023-04-31\n", out="2023-12-31"),
    c(stdin="no dates here\n", out=""),
])

task("roman", """
Usage: roman VALUE. If VALUE is a decimal integer from 1 to 3999, print it as an uppercase Roman
numeral. If VALUE is a Roman numeral, in uppercase or lowercase, print its integer value. Only
canonical numerals are valid: IIII, VV, IC and IL, for example, are invalid. For any invalid VALUE,
including 0, 4000 and the empty string, exit with a non-zero status.
""", r'''
import sys, re
PAIRS = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
         (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
value = sys.argv[1] if len(sys.argv) == 2 else ""
if re.fullmatch(r"[0-9]+", value):
    n = int(value)
    if not 1 <= n <= 3999:
        sys.exit(1)
    out = ""
    for k, s in PAIRS:
        while n >= k:
            out += s
            n -= k
    print(out)
else:
    s = value.upper()
    if not s or not re.fullmatch(r"M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})", s):
        sys.exit(1)
    total, i = 0, 0
    for k, sym in PAIRS:
        while s.startswith(sym, i):
            total += k
            i += len(sym)
    print(total)
''', [
    c(["1994"], out="MCMXCIV"), c(["mcmxciv"], out="1994"), c(["3999"], out="MMMCMXCIX"),
    c(["XLII"], out="42"), c(["4"], out="IV"),
    bad("IIII"), bad("0"), bad("4000"), bad("IL"), bad("VV"), bad(""),
])

task("semver-sort", """
Read version strings from standard input, one per line, and print them sorted in ascending order
of Semantic Versioning 2.0.0 precedence. Each version is MAJOR.MINOR.PATCH with an optional
pre-release part after a hyphen, for example 1.0.0-alpha.1. MAJOR, MINOR and PATCH compare
numerically. A version with a pre-release part is lower than the same version without one.
Pre-release parts compare identifier by identifier, identifiers being separated by dots: numeric
identifiers compare numerically, other identifiers compare in ASCII order, a numeric identifier is
lower than a non-numeric one, and when all shared identifiers are equal the version with fewer
identifiers is lower. Blank lines are ignored. Every input line is a valid version.
""", r'''
import sys
def key(v):
    core, _, pre = v.partition("-")
    nums = tuple(int(x) for x in core.split("."))
    if not pre:
        return (nums, 1, ())
    ids = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split("."))
    return (nums, 0, ids)
for v in sorted((l.strip() for l in sys.stdin if l.strip()), key=key):
    print(v)
''', [
    c(stdin="1.0.0\n1.0.0-beta.11\n1.0.0-alpha.beta\n1.0.0-rc.1\n1.0.0-alpha\n1.0.0-beta.2\n"
            "1.0.0-alpha.1\n1.0.0-beta\n",
      out="1.0.0-alpha\n1.0.0-alpha.1\n1.0.0-alpha.beta\n1.0.0-beta\n1.0.0-beta.2\n"
          "1.0.0-beta.11\n1.0.0-rc.1\n1.0.0"),
    c(stdin="1.10.0\n1.2.0\n1.9.9\n", out="1.2.0\n1.9.9\n1.10.0"),
    c(stdin="2.0.0\n\n2.0.0-rc.1\n1.99.99\n", out="1.99.99\n2.0.0-rc.1\n2.0.0"),
    c(stdin="1.0.0-10\n1.0.0-9\n1.0.0-a\n1.0.0-9.1\n", out="1.0.0-9\n1.0.0-9.1\n1.0.0-10\n1.0.0-a"),
])

task("dursum", """
Usage: dursum DURATION... Each argument is a duration made of one or more NUMBER+UNIT parts with
the units d (days), h (hours), m (minutes) and s (seconds), for example `1h30m`, `45s` or `2d`.
Numbers are non-negative integers; parts may come in any order. Print the sum of all arguments as
`DdHhMmSs`, normalised (60s = 1m, 60m = 1h, 24h = 1d) and leaving out units whose value is zero.
A total of zero prints `0s`. If an argument is malformed (such as `5x`, `h`, `1.5h` or an empty
string), or there are no arguments, exit with a non-zero status.
""", r'''
import sys, re
UNIT = {"d": 86400, "h": 3600, "m": 60, "s": 1}
args = sys.argv[1:]
if not args:
    sys.exit(1)
total = 0
for a in args:
    if not re.fullmatch(r"(\d+[dhms])+", a):
        sys.exit(1)
    for n, u in re.findall(r"(\d+)([dhms])", a):
        total += int(n) * UNIT[u]
out = ""
for u in "dhms":
    q, total = divmod(total, UNIT[u])
    if q:
        out += f"{q}{u}"
print(out or "0s")
''', [
    c(["1h30m", "45m"], out="2h15m"), c(["59s", "1s"], out="1m"), c(["0s"], out="0s"),
    c(["23h", "1h"], out="1d"), c(["1d2h3m4s"], out="1d2h3m4s"), c(["3600s", "30m1h"], out="2h30m"),
    c(["90m"], out="1h30m"),
    bad("5x"), bad(), bad("1.5h"), bad("h"), bad("1h", ""),
])

task("tsvalign", """
Read tab-separated rows from standard input and print them as aligned columns. Each cell is
left-aligned and padded with spaces to the width of the widest cell in its column, and columns are
separated by two spaces. Rows with fewer cells than the widest row are treated as having empty
cells at the end. No output line has trailing spaces. Empty input prints nothing.
""", r'''
import sys
rows = [line.split("\t") for line in sys.stdin.read().split("\n")]
if rows and rows[-1] == [""]:
    rows.pop()
if rows:
    n = max(len(r) for r in rows)
    rows = [r + [""] * (n - len(r)) for r in rows]
    widths = [max(len(r[i]) for r in rows) for i in range(n)]
    for r in rows:
        print("  ".join(cell.ljust(w) for cell, w in zip(r, widths)).rstrip())
''', [
    c(stdin="a\tbbb\tc\nxxxx\ty\tz\n", out="a     bbb  c\nxxxx  y    z"),
    c(stdin="a\tb\tc\nlong\n", out="a     b  c\nlong"),
    c(stdin="", out=""),
    c(stdin="name\tqty\nwidget\t10\nnut\t5\n", out="name    qty\nwidget  10\nnut     5"),
])

task("jsonflat", """
Read a JSON object from standard input and print one line per leaf value as `PATH=VALUE`, in
document order. PATH joins object keys with dots and writes array indexes as `[N]`, for example
`a.b[0].c`. VALUE is the leaf encoded as compact JSON: strings keep their quotes, and true, false
and null are written as in JSON. An empty object or an empty array is a leaf, printed as `{}` or
`[]`. An empty top-level object prints nothing.
""", r'''
import sys, json
def walk(node, path):
    if isinstance(node, dict) and node:
        for k, v in node.items():
            walk(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list) and node:
        for i, v in enumerate(node):
            walk(v, f"{path}[{i}]")
    else:
        print(f"{path}={json.dumps(node, separators=(',', ':'))}")
data = json.load(sys.stdin)
if data:
    walk(data, "")
''', [
    c(stdin='{"a":{"b":[{"c":1},"x"]},"d":null,"e":true}',
      out='a.b[0].c=1\na.b[1]="x"\nd=null\ne=true'),
    c(stdin='{"a":[],"b":{},"c":[[1,2],[3]]}', out="a=[]\nb={}\nc[0][0]=1\nc[0][1]=2\nc[1][0]=3"),
    c(stdin="{}", out=""),
    c(stdin='{"z":false,"a":{"k":"v w"}}', out='z=false\na.k="v w"'),
])

task("cidr", """
Usage: cidr ADDRESS/PREFIX for IPv4. Print three values separated by single spaces: the first
address of the network, the last address, and the number of addresses. Host bits set in ADDRESS
are ignored, so 192.168.1.77/24 is the network 192.168.1.0/24. PREFIX is 0 to 32. Invalid input
(no prefix, an octet above 255, a prefix above 32, the wrong number of octets) exits with a
non-zero status.
""", r'''
import sys, re
m = re.fullmatch(r"(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})/(\d{1,2})", sys.argv[1] if len(sys.argv) == 2 else "")
if not m:
    sys.exit(1)
*octets, prefix = (int(g) for g in m.groups())
if any(o > 255 for o in octets) or prefix > 32:
    sys.exit(1)
addr = 0
for o in octets:
    addr = addr << 8 | o
size = 1 << (32 - prefix)
first = addr & ~(size - 1) & 0xFFFFFFFF
dotted = lambda n: ".".join(str(n >> s & 255) for s in (24, 16, 8, 0))
print(dotted(first), dotted(first + size - 1), size)
''', [
    c(["192.168.1.0/30"], out="192.168.1.0 192.168.1.3 4"),
    c(["192.168.1.77/24"], out="192.168.1.0 192.168.1.255 256"),
    c(["10.1.2.3/32"], out="10.1.2.3 10.1.2.3 1"),
    c(["1.2.3.4/0"], out="0.0.0.0 255.255.255.255 4294967296"),
    c(["172.16.5.130/25"], out="172.16.5.128 172.16.5.255 128"),
    bad("256.1.1.1/8"), bad("10.0.0.0/33"), bad("10.0.0.0"), bad("10.0.0/8"),
])

task("wrap", """
Usage: wrap WIDTH. Read text from standard input. Paragraphs are separated by one or more blank
lines. Re-flow each paragraph: join its words, which are separated by any whitespace, with single
spaces, and break lines greedily so that no line is longer than WIDTH characters. A word longer
than WIDTH goes on a line of its own, unbroken. Print the paragraphs separated by exactly one
blank line, with no blank line at the start or the end. If WIDTH is not a positive integer, exit
with a non-zero status.
""", r'''
import sys, re
try:
    width = int(sys.argv[1])
except (IndexError, ValueError):
    sys.exit(1)
if width < 1:
    sys.exit(1)
paragraphs = [p.split() for p in re.split(r"\n[ \t]*(?:\n[ \t]*)+", sys.stdin.read())]
out = []
for words in paragraphs:
    if not words:
        continue
    lines, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}" if cur else w
    lines.append(cur)
    out.append("\n".join(lines))
print("\n\n".join(out))
''', [
    c(["10"], stdin="the quick brown fox jumps\n", out="the quick\nbrown fox\njumps"),
    c(["80"], stdin="a b\nc\n\n\n\nd   e\n", out="a b c\n\nd e"),
    c(["5"], stdin="hi incomprehensible yo\n", out="hi\nincomprehensible\nyo"),
    c(["9"], stdin="\n\nthe quick\n", out="the quick"),
    bad("0", stdin="x\n"), bad("abc", stdin="x\n"),
])

task("calc", """
Usage: calc EXPRESSION, as one argument. Evaluate an arithmetic expression made of decimal
numbers, the binary operators + - * /, unary minus and parentheses, with the usual precedence and
left-to-right associativity. Spaces are allowed between tokens. Division is real division. Print
the result as an integer if it is integral, otherwise rounded to 6 decimal places with trailing
zeros removed. Division by zero or a malformed expression (such as `2 +`, `(1`, `2 3` or `a+1`)
exits with a non-zero status.
""", FMT + r'''
import sys, re
tokens = re.findall(r"\d+\.?\d*|\.\d+|[-+*/()]|\S", sys.argv[1] if len(sys.argv) == 2 else "")
pos = 0
def peek():
    return tokens[pos] if pos < len(tokens) else None
def take():
    global pos
    pos += 1
    return tokens[pos - 1]
def atom():
    t = peek()
    if t == "-":
        take()
        return -atom()
    if t == "(":
        take()
        v = expr()
        if peek() != ")":
            raise ValueError
        take()
        return v
    if t is None or not re.fullmatch(r"\d+\.?\d*|\.\d+", t):
        raise ValueError
    return float(take())
def term():
    v = atom()
    while peek() in ("*", "/"):
        v = v * atom() if take() == "*" else v / atom()
    return v
def expr():
    v = term()
    while peek() in ("+", "-"):
        v = v + term() if take() == "+" else v - term()
    return v
try:
    result = expr()
    if pos != len(tokens):
        raise ValueError
except (ValueError, ZeroDivisionError):
    sys.exit(1)
print(fmt(result))
''', [
    c(["1+2*3"], out="7"), c(["(1+2)*3"], out="9"), c(["7/2"], out="3.5"),
    c(["1/3"], out="0.333333"), c(["-(2-5)*2"], out="6"), c(["8/4/2"], out="1"),
    c(["2-3-4"], out="-5"), c(["0.1+0.2"], out="0.3"), c([" 2 * ( 3 + 4 ) "], out="14"),
    bad("1/0"), bad("2 +"), bad("a+1"), bad("(1"), bad("2 3"),
])

task("loglevels", """
Read log lines from standard input. A line's level is its second whitespace-separated field when
that field is exactly DEBUG, INFO, WARN or ERROR; every other non-empty line counts as OTHER.
Print `LEVEL COUNT` for each level whose count is above zero, in the fixed order DEBUG, INFO,
WARN, ERROR, OTHER. Empty lines are ignored.
""", r'''
import sys, collections
ORDER = ["DEBUG", "INFO", "WARN", "ERROR", "OTHER"]
counts = collections.Counter()
for line in sys.stdin.read().split("\n"):
    if not line:
        continue
    fields = line.split()
    level = fields[1] if len(fields) > 1 and fields[1] in ORDER[:4] else "OTHER"
    counts[level] += 1
for level in ORDER:
    if counts[level]:
        print(level, counts[level])
''', [
    c(stdin="t1 INFO a\nt2 ERROR b\nt3 INFO c\ngarbage\n\nt4 info d\nERROR first field\n",
      out="INFO 2\nERROR 1\nOTHER 3"),
    c(stdin="1 DEBUG x\n2 DEBUG y\n", out="DEBUG 2"),
    c(stdin="", out=""),
    c(stdin="a WARN\nb ERROR z\nc WARNING z\nd DEBUG\n", out="DEBUG 1\nWARN 1\nERROR 1\nOTHER 1"),
])

task("colsum", """
Usage: colsum FILE N. FILE is a CSV file with a header row. Print the sum of column N, counted
from 1, over the data rows. Empty cells are skipped. Cells are integers or decimals, possibly
surrounded by spaces. Print the sum as an integer if it is integral, otherwise rounded to 6
decimal places with trailing zeros removed. A sum over no values prints 0. If a non-empty cell is
not a number, or N is not a column of the header, exit with a non-zero status.
""", FMT + r'''
import sys, csv
try:
    n = int(sys.argv[2])
    with open(sys.argv[1], newline="") as fh:
        rows = list(csv.reader(fh))
    if not rows or not 1 <= n <= len(rows[0]):
        sys.exit(1)
    total = 0.0
    for row in rows[1:]:
        cell = row[n - 1].strip() if len(row) >= n else ""
        if cell:
            total += float(cell)
except (IndexError, ValueError, OSError):
    sys.exit(1)
print(fmt(total))
''', [
    c(["t.csv", "2"], files={"t.csv": "a,b\n1,2\n3,4.5\n"}, out="6.5"),
    c(["t.csv", "1"], files={"t.csv": "a,b\n1,2\n3,4.5\n"}, out="4"),
    c(["t.csv", "2"], files={"t.csv": "a,b\n1,\n2, 3 \n"}, out="3"),
    c(["t.csv", "1"], files={"t.csv": "a,b\n"}, out="0"),
    c(["t.csv", "2"], files={"t.csv": "a,b\nx,0.1\ny,0.2\n"}, out="0.3"),
    bad("t.csv", "1", files={"t.csv": "a\nx\n"}),
    bad("t.csv", "3", files={"t.csv": "a,b\n1,2\n"}),
    bad("t.csv", "0", files={"t.csv": "a,b\n1,2\n"}),
])

task("rle", """
Usage: rle encode|decode. Read one line from standard input, without its newline. `encode`
replaces each run of a repeated character by the run length followed by the character: `aaabcc`
becomes `3a1b2c`. `decode` reverses it: `12a1b` becomes twelve `a` followed by one `b`. An empty
line gives an empty line in both modes. `encode` exits with a non-zero status if the input
contains a digit. `decode` exits with a non-zero status if the input is not a sequence of
COUNT+CHARACTER pairs with COUNT a positive integer; `a3`, `3` and `0a` are invalid.
""", r'''
import sys, re, itertools
mode = sys.argv[1] if len(sys.argv) == 2 else ""
line = sys.stdin.readline().rstrip("\n")
if mode == "encode":
    if re.search(r"\d", line):
        sys.exit(1)
    print("".join(f"{len(list(g))}{ch}" for ch, g in itertools.groupby(line)))
elif mode == "decode":
    if not re.fullmatch(r"(\d+\D)*", line):
        sys.exit(1)
    pairs = re.findall(r"(\d+)(\D)", line)
    if any(int(n) == 0 for n, _ in pairs):
        sys.exit(1)
    print("".join(ch * int(n) for n, ch in pairs))
else:
    sys.exit(1)
''', [
    c(["encode"], stdin="aaabcc\n", out="3a1b2c"),
    c(["encode"], stdin="aaaaaaaaaaaab\n", out="12a1b"),
    c(["decode"], stdin="12a1b\n", out="aaaaaaaaaaaab"),
    c(["decode"], stdin="3a1b2c\n", out="aaabcc"),
    c(["encode"], stdin="\n", out=""), c(["decode"], stdin="\n", out=""),
    c(["encode"], stdin="a  b\n", out="1a2 1b"), c(["decode"], stdin="1a2 1b\n", out="a  b"),
    bad("encode", stdin="a1\n"), bad("decode", stdin="a3\n"),
    bad("decode", stdin="0a\n"), bad("decode", stdin="3\n"),
])

task("bizdays", """
Usage: bizdays START END, with dates as YYYY-MM-DD. Print the number of weekdays (Monday to
Friday) from START inclusive to END exclusive. If END is before START, print the negated count
for the swapped dates, that is, minus the number of weekdays from END inclusive to START
exclusive. Equal dates print 0. An invalid date exits with a non-zero status.
""", r'''
import sys, datetime
try:
    a, b = (datetime.date.fromisoformat(x) for x in sys.argv[1:3])
except ValueError:
    sys.exit(1)
sign = 1
if b < a:
    a, b, sign = b, a, -1
n = sum(1 for i in range((b - a).days) if (a + datetime.timedelta(i)).weekday() < 5)
print(sign * n)
''', [
    c(["2024-01-01", "2024-01-08"], out="5"), c(["2024-01-06", "2024-01-08"], out="0"),
    c(["2024-01-05", "2024-01-06"], out="1"), c(["2024-01-08", "2024-01-01"], out="-5"),
    c(["2024-03-15", "2024-03-15"], out="0"), c(["2024-01-01", "2025-01-01"], out="262"),
    c(["2024-01-07", "2024-01-05"], out="-1"),
    bad("2024-02-30", "2024-03-01"), bad("2024-01-01", "tomorrow"),
])

task("extcount", """
Usage: extcount DIR. Recursively count the regular files under DIR by extension. The extension is
the text after the last dot of the file name, in lowercase. A name with no dot, or whose only dot
is its first character (such as `.bashrc`), has the extension `(none)`. Print `EXTENSION COUNT`
lines sorted by count descending, then extension ascending. An empty directory prints nothing.
""", r'''
import sys, os, collections
counts = collections.Counter()
for _, _, names in os.walk(sys.argv[1]):
    for name in names:
        stem, dot, ext = name.rpartition(".")
        counts[ext.lower() if dot and stem else "(none)"] += 1
for ext, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
    print(ext, n)
''', [
    c(["d"], files={"d/a.py": "", "d/b.PY": "", "d/sub/c.txt": "", "d/Makefile": "",
                    "d/.bashrc": "", "d/x.tar.gz": ""},
      out="(none) 2\npy 2\ngz 1\ntxt 1"),
    c(["d"], files={"d/": None}, out=""),
    c(["d"], files={"d/.config.yml": "", "d/a/b/c.yml": "", "d/z.md": ""}, out="yml 2\nmd 1"),
])

task("slug", """
Usage: slug TEXT, as one argument. Print a URL slug: decompose accented letters and drop the
accents (so that é becomes e), convert to lowercase, replace every run of characters other than
a-z and 0-9 with a single hyphen, and remove hyphens at the start and the end. If the result is
empty, exit with a non-zero status.
""", r'''
import sys, re, unicodedata
text = unicodedata.normalize("NFKD", sys.argv[1] if len(sys.argv) == 2 else "")
text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
if not slug:
    sys.exit(1)
print(slug)
''', [
    c(["Hello, World!"], out="hello-world"), c(["  Crème brûlée  "], out="creme-brulee"),
    c(["a--b__c"], out="a-b-c"), c(["Été 2024"], out="ete-2024"), c(["C++ & C#"], out="c-c"),
    bad("!!!"), bad(""),
])

task("baseconv", """
Usage: baseconv NUMBER FROM TO. Convert the integer NUMBER, written in base FROM, to base TO.
Bases are 2 to 36, with the digits 0-9 then a-z; input letters may be in either case. Print the
result in lowercase. A leading `-` marks a negative number, and zero prints `0`. If NUMBER
contains a character that is not a digit of base FROM, or is empty, or a base is outside 2 to 36,
exit with a non-zero status.
""", r'''
import sys, re
DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"
try:
    number, src, dst = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
except (IndexError, ValueError):
    sys.exit(1)
if not (2 <= src <= 36 and 2 <= dst <= 36):
    sys.exit(1)
m = re.fullmatch(r"(-?)([0-9a-zA-Z]+)", number)
if not m or any(DIGITS.index(ch) >= src for ch in m.group(2).lower()):
    sys.exit(1)
n = int(m.group(2), src)
out = ""
while n:
    n, r = divmod(n, dst)
    out = DIGITS[r] + out
out = out or "0"
print(("-" if m.group(1) and out != "0" else "") + out)
''', [
    c(["ff", "16", "2"], out="11111111"), c(["-255", "10", "16"], out="-ff"),
    c(["0", "10", "2"], out="0"), c(["ZZ", "36", "10"], out="1295"), c(["1010", "2", "8"], out="12"),
    c(["-1010", "2", "10"], out="-10"),
    bad("12", "2", "10"), bad("10", "1", "10"), bad("10", "10", "37"), bad("", "10", "2"),
    bad("1.5", "10", "2"),
])

task("merge-intervals", """
Read intervals from standard input, one per line as `START END`, two integers with START <= END.
Merge intervals that overlap or touch: an interval ending at 5 and one starting at 5 are merged,
but one ending at 5 and one starting at 6 are not. Print the merged intervals sorted by START,
one per line as `START END`. Blank lines are ignored. A line with START > END, or one that is not
exactly two integers, exits with a non-zero status. Empty input prints nothing.
""", r'''
import sys
spans = []
for line in sys.stdin:
    if not line.strip():
        continue
    parts = line.split()
    try:
        a, b = (int(p) for p in parts)
    except ValueError:
        sys.exit(1)
    if a > b:
        sys.exit(1)
    spans.append([a, b])
merged = []
for a, b in sorted(spans):
    if merged and a <= merged[-1][1]:
        merged[-1][1] = max(merged[-1][1], b)
    else:
        merged.append([a, b])
for a, b in merged:
    print(a, b)
''', [
    c(stdin="1 3\n2 6\n8 10\n15 18\n", out="1 6\n8 10\n15 18"),
    c(stdin="5 7\n1 5\n", out="1 7"), c(stdin="1 5\n6 7\n", out="1 5\n6 7"),
    c(stdin="-5 -1\n\n-2 3\n", out="-5 3"), c(stdin="1 10\n2 3\n", out="1 10"),
    c(stdin="", out=""),
    bad(stdin="3 1\n"), bad(stdin="1 2 3\n"), bad(stdin="1 x\n"),
])

task("anagrams", """
Read words from standard input, one per line. Convert them to lowercase and ignore repeated
words. Print each group of two or more words that are anagrams of one another on one line, with
the words sorted ascending and separated by single spaces. Sort the lines by their first word.
Words with no anagram are not printed. Blank lines are ignored.
""", r'''
import sys, collections
groups = collections.defaultdict(set)
for line in sys.stdin:
    word = line.strip().lower()
    if word:
        groups["".join(sorted(word))].add(word)
for words in sorted(sorted(g) for g in groups.values() if len(g) > 1):
    print(" ".join(words))
''', [
    c(stdin="listen\nSilent\nenlist\ngoogle\ntinsel\nlisten\ncat\nact\n",
      out="act cat\nenlist listen silent tinsel"),
    c(stdin="a\nA\n", out=""), c(stdin="", out=""),
    c(stdin="tea\n\neat\nate\nTEA\nbat\n", out="ate eat tea"),
])

task("iniget", """
Usage: iniget FILE SECTION KEY. Print the value of KEY in the section `[SECTION]` of an INI file.
Settings are lines of the form `key = value`: spaces around the key and around the value are
removed, and the value may itself contain `=`. Blank lines, and lines whose first non-space
character is `;` or `#`, are ignored. Section and key names are case-sensitive. If the key appears
more than once in the section, the last one wins. A section header may appear more than once, and
its keys accumulate. If the file, the section or the key is missing, exit with a non-zero status.
""", r'''
import sys
try:
    path, want_section, want_key = sys.argv[1:4]
    lines = open(path).read().split("\n")
except (ValueError, OSError):
    sys.exit(1)
section, found = None, None
for raw in lines:
    line = raw.strip()
    if not line or line[0] in ";#":
        continue
    if line.startswith("[") and line.endswith("]"):
        section = line[1:-1].strip()
    elif "=" in line and section == want_section:
        key, _, value = line.partition("=")
        if key.strip() == want_key:
            found = value.strip()
if found is None:
    sys.exit(1)
print(found)
''', (lambda ini: [
    c(["c.ini", "db", "host"], files=ini, out="localhost"),
    c(["c.ini", "db", "url"], files=ini, out="postgres://u:p@h/db?x=1"),
    c(["c.ini", "app", "name"], files=ini, out="final"),
    c(["c.ini", "db", "port"], files=ini, out="5432"),
    bad("c.ini", "db", "missing", files=ini), bad("c.ini", "nosec", "host", files=ini),
    bad("c.ini", "DB", "host", files=ini), bad("c.ini", "app", "host", files=ini),
    bad("nofile.ini", "db", "host"),
])({"c.ini": "; comment\n[db]\nhost = localhost\nurl = postgres://u:p@h/db?x=1\n\n[app]\n"
             "name=demo\n  # note\nname = final\n[db]\nport=5432\n"}))

task("histo", """
Read integers from standard input, one per line; blank lines are ignored. Print one line per
distinct value in ascending numeric order: the value right-aligned to the width of the widest
value, then ` | `, then one `#` per occurrence. A line that is not an integer exits with a
non-zero status. Empty input prints nothing.
""", r'''
import sys, collections
counts = collections.Counter()
for line in sys.stdin:
    if line.strip():
        try:
            counts[int(line.strip())] += 1
        except ValueError:
            sys.exit(1)
if counts:
    width = max(len(str(v)) for v in counts)
    for v in sorted(counts):
        print(f"{v:>{width}} | {'#' * counts[v]}")
''', [
    c(stdin="3\n1\n3\n10\n3\n", out=" 1 | #\n 3 | ###\n10 | #"),
    c(stdin="-5\n5\n\n5\n", out="-5 | #\n 5 | ##"),
    c(stdin="", out=""), c(stdin="9\n10\n9\n100\n", out="  9 | ##\n 10 | #\n100 | #"),
    bad(stdin="x\n"), bad(stdin="1\n2.5\n"),
])

task("luhn", """
Usage: luhn NUMBER. NUMBER is a string of digits that may also contain spaces and hyphens, which
are ignored. If it has at least two digits and passes the Luhn checksum, print `valid` and exit
with status 0. Otherwise print `invalid` and exit with status 1. Any other character makes the
number invalid.
""", r'''
import sys, re
digits = re.sub(r"[ -]", "", sys.argv[1] if len(sys.argv) == 2 else "")
ok = len(digits) >= 2 and digits.isascii() and digits.isdigit()
if ok:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    ok = total % 10 == 0
print("valid" if ok else "invalid")
sys.exit(0 if ok else 1)
''', [
    c(["79927398713"], out="valid"), c(["4539 3195 0343 6467"], out="valid"),
    c(["055-444-285"], out="valid"), c(["059"], out="valid"), c(["59"], out="valid"),
    c(["79927398710"], out="invalid", exit="nz"), c(["8273 1232 7352 0569"], out="invalid", exit="nz"),
    c(["0"], out="invalid", exit="nz"), c(["055a 444 285"], out="invalid", exit="nz"),
    c([""], out="invalid", exit="nz"),
])

task("timediff", """
Usage: timediff START END, with 24-hour times as HH:MM, two digits each. Print the time from
START to END as `HhMMm`, hours without padding and minutes with two digits, for example `1h05m`.
If END is earlier than START, END is on the next day. Equal times print `0h00m`. An invalid time,
such as 24:00, 12:60, 7:30 or noon, exits with a non-zero status.
""", r'''
import sys, re
def minutes(t):
    m = re.fullmatch(r"(\d\d):(\d\d)", t)
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        sys.exit(1)
    return int(m.group(1)) * 60 + int(m.group(2))
if len(sys.argv) != 3:
    sys.exit(1)
diff = (minutes(sys.argv[2]) - minutes(sys.argv[1])) % 1440
print(f"{diff // 60}h{diff % 60:02d}m")
''', [
    c(["09:00", "10:05"], out="1h05m"), c(["23:30", "00:15"], out="0h45m"),
    c(["12:00", "12:00"], out="0h00m"), c(["00:00", "23:59"], out="23h59m"),
    c(["10:05", "09:00"], out="22h55m"),
    bad("24:00", "01:00"), bad("12:60", "13:00"), bad("7:30", "08:00"), bad("noon", "13:00"),
])

task("caesar", """
Usage: caesar SHIFT. Read text from standard input and print it with every ASCII letter moved
SHIFT positions forward in the alphabet, wrapping around and keeping its case. All other
characters are unchanged. SHIFT is any integer, possibly negative or larger than 26. If SHIFT is
not an integer, exit with a non-zero status.
""", r'''
import sys
try:
    shift = int(sys.argv[1])
except (IndexError, ValueError):
    sys.exit(1)
out = []
for ch in sys.stdin.read():
    if "a" <= ch <= "z":
        ch = chr((ord(ch) - 97 + shift) % 26 + 97)
    elif "A" <= ch <= "Z":
        ch = chr((ord(ch) - 65 + shift) % 26 + 65)
    out.append(ch)
sys.stdout.write("".join(out))
''', [
    c(["3"], stdin="Hello, World!\n", out="Khoor, Zruog!"),
    c(["-3"], stdin="Khoor, Zruog!\n", out="Hello, World!"),
    c(["29"], stdin="abc xyz\n", out="def abc"),
    c(["0"], stdin="Same 123\nlines\n", out="Same 123\nlines"),
    c(["-27"], stdin="aB\n", out="zA"),
    bad("x", stdin="a\n"), bad("1.5", stdin="a\n"),
])

task("envmerge", """
Usage: envmerge A B. A and B are files of `KEY=VALUE` lines. Print the merged settings as
`KEY=VALUE` lines: the keys of A in their order in A, with the value from B when B also defines
the key, followed by the keys found only in B, in their order in B. Blank lines and lines starting
with `#` are ignored. The value is everything after the first `=` and is kept as it is. If a key
is repeated within one file, the last value wins and the key keeps its first position. A line
that is not blank, not a comment and has no `=` exits with a non-zero status, and so does a
missing file.
""", r'''
import sys
def load(path):
    data = {}
    try:
        lines = open(path).read().split("\n")
    except OSError:
        sys.exit(1)
    for line in lines:
        if not line.strip() or line.startswith("#"):
            continue
        if "=" not in line:
            sys.exit(1)
        key, _, value = line.partition("=")
        data[key] = value
    return data
if len(sys.argv) != 3:
    sys.exit(1)
merged = load(sys.argv[1])
merged.update(load(sys.argv[2]))
for key, value in merged.items():
    print(f"{key}={value}")
''', [
    c(["a.env", "b.env"], files={"a.env": "X=1\nY=2\n# c\n\nZ=a=b\n", "b.env": "Y=20\nW=5\n"},
      out="X=1\nY=20\nZ=a=b\nW=5"),
    c(["a.env", "b.env"], files={"a.env": "K=1\nJ=2\nK=3\n", "b.env": ""}, out="K=3\nJ=2"),
    c(["a.env", "b.env"], files={"a.env": "", "b.env": "B=2\nA=1\nB=3\n"}, out="B=3\nA=1"),
    c(["a.env", "b.env"], files={"a.env": "E=\n", "b.env": "#E=9\n"}, out="E="),
    bad("a.env", "b.env", files={"a.env": "X=1\noops\n", "b.env": ""}),
    bad("a.env", "b.env", files={"a.env": "X=1\n"}),
])

task("pctl", """
Usage: pctl P. Read numbers from standard input, one per line, ignoring blank lines, and print the
P-th percentile using linear interpolation between closest ranks: sort the N values, compute the
position (N-1)*P/100, counted from 0, and interpolate linearly between the values at the floor and
the ceiling of that position. Print the result as an integer if it is integral, otherwise rounded
to 4 decimal places with trailing zeros removed. P is a number from 0 to 100. If P is out of range
or not a number, or there are no values, exit with a non-zero status.
""", FMT + r'''
import sys, math
try:
    p = float(sys.argv[1])
    values = sorted(float(l) for l in sys.stdin if l.strip())
except (IndexError, ValueError):
    sys.exit(1)
if not values or not 0 <= p <= 100:
    sys.exit(1)
pos = (len(values) - 1) * p / 100
lo, hi = math.floor(pos), math.ceil(pos)
print(fmt(values[lo] + (values[hi] - values[lo]) * (pos - lo), 4))
''', [
    c(["50"], stdin="1\n2\n3\n4\n", out="2.5"), c(["0"], stdin="4\n1\n3\n2\n", out="1"),
    c(["100"], stdin="4\n1\n3\n2\n", out="4"), c(["25"], stdin="1\n2\n3\n4\n", out="1.75"),
    c(["90"], stdin="10\n\n20\n", out="19"), c(["50"], stdin="7\n", out="7"),
    c(["33"], stdin="1\n2\n4\n", out="1.66"),
    bad("101", stdin="1\n"), bad("50", stdin=""), bad("abc", stdin="1\n"), bad("-1", stdin="1\n"),
])

task("transpose", """
Read a matrix from standard input: one row per line, with cells separated by spaces or tabs;
blank lines are ignored. Print its transpose, with cells separated by single spaces. If the rows
do not all have the same number of cells, exit with a non-zero status. Empty input prints nothing.
""", r'''
import sys
rows = [line.split() for line in sys.stdin if line.strip()]
if len({len(r) for r in rows}) > 1:
    sys.exit(1)
for col in zip(*rows):
    print(" ".join(col))
''', [
    c(stdin="1 2 3\n4 5 6\n", out="1 4\n2 5\n3 6"), c(stdin="a\tb\n", out="a\nb"),
    c(stdin="1\n2\n", out="1 2"), c(stdin="", out=""), c(stdin="x  y\n\nz\tw\n", out="x z\ny w"),
    bad(stdin="1 2\n3\n"),
])

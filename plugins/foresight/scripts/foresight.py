#!/usr/bin/env python3
"""foresight - a software world model and a verified tool library for Claude Code.

Two learning loops that live outside the model's weights:

  World model  The agent annotates Bash commands with a prediction
               (`# predict: exit=0 p=0.8 out~12 passed`). Hooks score each
               prediction against what actually happened, feed surprises back,
               and keep the lessons the agent draws from them.

  Toolbox      The agent turns procedures it worked out into scripts. A tool is
               only registered if its test passes, so the library is competence
               stored as verifiable code that can be reused and composed.

Standard library only. State lives in <project>/.claude/foresight/.
"""

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
PREDICT_RE = re.compile(r"(?:^|\s)#\s*predict:\s*(.+?)\s*$", re.IGNORECASE)
EXIT_CODE_RE = re.compile(r"^Exit code (\d+)", re.MULTILINE)
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
TOOL_CALL_RE = re.compile(r"foresight\s+tool\s+run\s+([a-z0-9][a-z0-9_-]*)")
MAX_COMMAND_CHARS = 500
MAX_LESSONS_AT_START = 10
MAX_LESSONS_PER_COMMAND = 3
TEST_TIMEOUT_S = 120


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

def find_home(cwd=None):
    """Locate the state directory. Hooks and the CLI must agree on this."""
    explicit = os.environ.get("FORESIGHT_HOME")
    if explicit:
        return Path(explicit)
    project = os.environ.get("CLAUDE_PROJECT_DIR")
    if project:
        return Path(project) / ".claude" / "foresight"
    start = Path(cwd or os.getcwd()).resolve()
    for d in (start, *start.parents):
        if (d / ".claude" / "foresight").is_dir():
            return d / ".claude" / "foresight"
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=start,
            capture_output=True, text=True, timeout=5,
        )
        if top.returncode == 0 and top.stdout.strip():
            return Path(top.stdout.strip()) / ".claude" / "foresight"
    except (OSError, subprocess.SubprocessError):
        pass
    return start / ".claude" / "foresight"


class Store:
    def __init__(self, home):
        self.home = Path(home)
        self.ledger = self.home / "ledger.jsonl"
        self.lessons = self.home / "lessons.jsonl"
        self.tools_dir = self.home / "tools"
        self.index = self.tools_dir / "index.json"
        self.pending = self.home / "pending"

    def ensure(self):
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        self.pending.mkdir(exist_ok=True)
        ignore = self.home / ".gitignore"
        if not ignore.exists():
            # Tools and lessons are worth sharing; the raw command log is not.
            ignore.write_text("ledger.jsonl\npending/\n*.lock\n")

    @contextmanager
    def lock(self):
        self.ensure()
        with open(self.home / "state.lock", "w") as fh:
            if fcntl:
                fcntl.flock(fh, fcntl.LOCK_EX)
            yield

    def append(self, path, record):
        self.ensure()
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    @staticmethod
    def read_jsonl(path):
        rows = []
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
        except OSError:
            pass
        return rows

    def load_index(self):
        try:
            return json.loads(self.index.read_text())
        except (OSError, ValueError):
            return {}

    def save_index(self, index):
        self.ensure()
        tmp = self.index.with_suffix(".tmp")
        tmp.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, self.index)


# --------------------------------------------------------------------------
# World model: predictions
# --------------------------------------------------------------------------

def split_prediction(command):
    """Return (command without the annotation, raw prediction text or None)."""
    lines = command.rstrip().split("\n")
    m = PREDICT_RE.search(lines[-1])
    if not m:
        return command.strip(), None
    lines[-1] = lines[-1][: m.start()].rstrip()
    return "\n".join(lines).strip(), m.group(1)


def parse_prediction(text):
    """Parse `exit=0 p=0.8 out~regex`. Returns None if nothing is checkable."""
    if not text:
        return None
    pred = {}
    head, sep, pattern = text.partition("out~")
    if sep:
        pattern = pattern.strip()
        if len(pattern) >= 2 and pattern[0] == pattern[-1] and pattern[0] in "\"'/":
            pattern = pattern[1:-1]
        if pattern:
            pred["out"] = pattern[:300]
    for token in head.split():
        key, eq, value = token.partition("=")
        key = key.lower()
        if not eq:
            continue
        if key == "exit":
            value = value.lower()
            if value in ("!0", "nonzero", "fail"):
                pred["exit"] = "!0"
            elif value.isdigit():
                pred["exit"] = int(value)
        elif key == "p":
            try:
                p = float(value)
            except ValueError:
                continue
            if p > 1:  # tolerate p=80 meaning 80%
                p /= 100
            if 0 <= p <= 1:
                pred["p"] = p
    if "exit" not in pred and "out" not in pred:
        return None
    return pred


def score(pred, actual_exit, output):
    """Did the prediction hold? actual_exit is None when it failed without a code."""
    reasons = []
    if "exit" in pred:
        want = pred["exit"]
        if want == "!0":
            ok = actual_exit != 0
        else:
            ok = actual_exit == want
        if not ok:
            reasons.append(f"expected exit={want}, got exit={actual_exit}")
    if "out" in pred:
        try:
            found = re.search(pred["out"], output[:200000], re.IGNORECASE | re.MULTILINE)
        except re.error:
            found = pred["out"].lower() in output.lower()
        if not found:
            reasons.append(f"output did not match /{pred['out']}/")
    return not reasons, reasons


def signature(command):
    """Coarse command family, e.g. `npm test`, `git push`, `pytest`."""
    try:
        tokens = shlex.split(command.split("\n")[0])
    except ValueError:
        tokens = command.split()
    while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
        tokens.pop(0)
    if not tokens:
        return "?"
    sig = os.path.basename(tokens[0])
    if len(tokens) > 1 and re.match(r"^[A-Za-z][\w./:-]*$", tokens[1]):
        sig += " " + tokens[1]
    return sig[:60]


def matching_lessons(lessons, command):
    hits = []
    for lesson in lessons:
        pattern = lesson.get("pattern")
        if not pattern:
            continue
        try:
            if re.search(pattern, command[:2000]):
                hits.append(lesson)
        except re.error:
            continue
    return hits


# --------------------------------------------------------------------------
# Hooks
# --------------------------------------------------------------------------

def emit(event, context):
    if context:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": event, "additionalContext": context}}))


def session_file(store, session_id):
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "default")
    return store.pending / f"session-{safe}.json"


def load_shown(store, session_id):
    try:
        return set(json.loads(session_file(store, session_id).read_text()))
    except (OSError, ValueError):
        return set()


def save_shown(store, session_id, shown):
    store.ensure()
    session_file(store, session_id).write_text(json.dumps(sorted(shown)))


def hook_session_start(store, data):
    store.ensure()
    cutoff = time.time() - 86400
    for stale in store.pending.glob("*.json"):
        try:
            if stale.stat().st_mtime < cutoff:
                stale.unlink()
        except OSError:
            pass

    lessons = store.read_jsonl(store.lessons)[-MAX_LESSONS_AT_START:]
    save_shown(store, data.get("session_id"), {l["id"] for l in lessons if "id" in l})
    index = store.load_index()

    out = [
        "## Foresight: predict, then act",
        "You keep a world model of this project. Before a Bash command whose "
        "outcome is not trivially certain (tests, builds, scripts, installs, "
        "migrations, git operations), append your prediction as a trailing "
        "comment on its last line:",
        "    <command>  # predict: exit=0 p=0.8 out~12 passed",
        "- `exit=N` or `exit=!0`: the exit code you expect.",
        "- `p=0.x`: your probability that the whole prediction holds. "
        "Calibration is scored, so do not round up.",
        "- `out~REGEX`: optional, must come last; text the output will contain.",
        "Skip trivial read-only commands. Each prediction is scored "
        "automatically. When one is wrong you are told: work out what your "
        "model got wrong before continuing, and if it is a durable fact about "
        "this project, save it with "
        "`foresight lesson add --pattern 'REGEX for similar commands' 'what is true'`.",
        "",
        "## Toolbox: verified tools built in earlier sessions",
    ]
    if index:
        for name in sorted(index):
            tool = index[name]
            flag = " [BROKEN: re-verify before relying on it]" if tool.get("status") == "broken" else ""
            usage = f" Usage: {tool['usage']}" if tool.get("usage") else ""
            out.append(f"- `{name}`: {tool.get('desc', '')}{usage}{flag}")
        out.append("Run one with `foresight tool run NAME [args]`. Prefer these, "
                   "and compositions of them, over redoing the work by hand.")
    else:
        out.append("(empty)")
    out += [
        "When you solve something with a multi-step procedure you are likely to "
        "need again, make it a tool: write the script, then "
        "`foresight tool add NAME --desc '...' --file SCRIPT --test 'CMD'`. "
        "CMD must exit 0 only if the tool works (the tool's path is in $TOOL); "
        "a tool whose test fails is not registered.",
    ]
    if lessons:
        out += ["", "## Lessons from past surprises"]
        out += [f"- {l.get('text', '')}" for l in lessons]
    emit("SessionStart", "\n".join(out))


def hook_pre_tool_use(store, data):
    command = (data.get("tool_input") or {}).get("command") or ""
    tool_use_id = data.get("tool_use_id")
    if not command or not tool_use_id:
        return
    bare, raw = split_prediction(command)
    pred = parse_prediction(raw)
    store.ensure()
    (store.pending / f"{tool_use_id}.json").write_text(json.dumps({
        "command": bare[:MAX_COMMAND_CHARS], "pred": pred, "ts": time.time(),
    }))

    session_id = data.get("session_id")
    shown = load_shown(store, session_id)
    fresh = [l for l in matching_lessons(store.read_jsonl(store.lessons), bare)
             if l.get("id") not in shown][:MAX_LESSONS_PER_COMMAND]
    if fresh:
        save_shown(store, session_id, shown | {l["id"] for l in fresh})
        emit("PreToolUse", "Foresight: lessons from earlier surprises with commands like this one:\n"
             + "\n".join(f"- {l.get('text', '')}" for l in fresh))


def hook_post(store, data, failed):
    tool_use_id = data.get("tool_use_id")
    tool_input = data.get("tool_input") or {}
    pending_path = store.pending / f"{tool_use_id}.json"
    try:
        pending = json.loads(pending_path.read_text())
        pending_path.unlink()
    except (OSError, ValueError):
        bare, raw = split_prediction(tool_input.get("command") or "")
        pending = {"command": bare[:MAX_COMMAND_CHARS], "pred": parse_prediction(raw)}

    if failed:
        output = data.get("error") or ""
        m = EXIT_CODE_RE.search(output)
        actual_exit = int(m.group(1)) if m else None
    else:
        resp = data.get("tool_response")
        resp = resp if isinstance(resp, dict) else {}
        output = (resp.get("stdout") or "") + "\n" + (resp.get("stderr") or "")
        actual_exit = 0

    # A backgrounded or interrupted command has no outcome to score yet.
    unscorable = (
        tool_input.get("run_in_background")
        or data.get("is_interrupt")
        or (isinstance(data.get("tool_response"), dict) and data["tool_response"].get("interrupted"))
    )
    pred = None if unscorable else pending.get("pred")
    record = {
        "ts": time.time(),
        "session": data.get("session_id"),
        "sig": signature(pending["command"]),
        "command": pending["command"],
        "exit": actual_exit,
        "duration_ms": data.get("duration_ms"),
    }
    reasons = []
    if pred:
        hit, reasons = score(pred, actual_exit, output)
        record.update(pred=pred, hit=hit)
    store.append(store.ledger, record)

    if pred and reasons:
        confidence = f" at p={pred['p']:g}" if "p" in pred else ""
        emit(data.get("hook_event_name"), (
            f"Foresight: your prediction{confidence} was wrong ({'; '.join(reasons)}). "
            "Before continuing, work out what your model of this project got wrong. "
            "If the answer is a durable fact rather than a one-off, record it: "
            "foresight lesson add --pattern 'REGEX for similar commands' 'what is true'"
        ))


def cmd_hook(args):
    try:
        data = json.load(sys.stdin)
        store = Store(find_home(data.get("cwd")))
        event = data.get("hook_event_name")
        if event == "SessionStart":
            hook_session_start(store, data)
        elif data.get("tool_name") == "Bash":
            if event == "PreToolUse":
                hook_pre_tool_use(store, data)
            elif event == "PostToolUse":
                hook_post(store, data, failed=False)
            elif event == "PostToolUseFailure":
                hook_post(store, data, failed=True)
    except Exception as exc:  # A learning aid must never break the session.
        print(f"foresight hook: {exc}", file=sys.stderr)
    return 0


# --------------------------------------------------------------------------
# Lessons
# --------------------------------------------------------------------------

def cmd_lesson_add(args):
    store = Store(find_home())
    if args.pattern:
        try:
            re.compile(args.pattern)
        except re.error as exc:
            print(f"error: --pattern is not a valid regex: {exc}", file=sys.stderr)
            return 2
    text = " ".join(args.text.split())
    lesson_id = hashlib.sha256(f"{args.pattern}|{text}".encode()).hexdigest()[:10]
    with store.lock():
        if any(l.get("id") == lesson_id for l in store.read_jsonl(store.lessons)):
            print(f"lesson {lesson_id} already recorded")
            return 0
        store.append(store.lessons, {
            "id": lesson_id, "pattern": args.pattern, "text": text[:500], "ts": time.time(),
        })
    print(f"recorded lesson {lesson_id}")
    return 0


def cmd_lesson_list(args):
    store = Store(find_home())
    for lesson in store.read_jsonl(store.lessons):
        pattern = f"  [/{lesson['pattern']}/]" if lesson.get("pattern") else ""
        print(f"{lesson.get('id')}  {lesson.get('text')}{pattern}")
    return 0


def cmd_lesson_rm(args):
    store = Store(find_home())
    with store.lock():
        lessons = store.read_jsonl(store.lessons)
        kept = [l for l in lessons if l.get("id") != args.id]
        if len(kept) == len(lessons):
            print(f"error: no lesson {args.id}", file=sys.stderr)
            return 1
        store.lessons.write_text("".join(json.dumps(l, ensure_ascii=False) + "\n" for l in kept))
    print(f"removed lesson {args.id}")
    return 0


# --------------------------------------------------------------------------
# Toolbox
# --------------------------------------------------------------------------

def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tool_env(store, tool_path=None):
    env = dict(os.environ)
    env["PATH"] = f"{PLUGIN_ROOT / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    env["FORESIGHT_HOME"] = str(store.home)
    if tool_path:
        env["TOOL"] = str(tool_path)
    return env


def run_test(store, tool_path, test):
    """Run a tool's test. Returns (passed, combined output tail)."""
    try:
        proc = subprocess.run(
            test, shell=True, env=tool_env(store, tool_path), text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=TEST_TIMEOUT_S,
        )
        return proc.returncode == 0, proc.stdout[-2000:]
    except subprocess.TimeoutExpired:
        return False, f"test timed out after {TEST_TIMEOUT_S}s"


def composition_depth(index, name, seen=()):
    if name in seen or name not in index:
        return 0
    deps = index[name].get("deps") or []
    return 1 + max((composition_depth(index, d, (*seen, name)) for d in deps), default=0)


def cmd_tool_add(args):
    store = Store(find_home())
    if not NAME_RE.match(args.name):
        print("error: name must match [a-z0-9][a-z0-9_-]*", file=sys.stderr)
        return 2
    source = Path(args.file)
    if not source.is_file():
        print(f"error: no such file: {source}", file=sys.stderr)
        return 2
    index = store.load_index()
    if args.name in index and not args.force:
        print(f"error: tool '{args.name}' exists; pass --force to replace it", file=sys.stderr)
        return 1
    body = source.read_text(errors="replace")
    deps = sorted(set(TOOL_CALL_RE.findall(body)) - {args.name})
    missing = [d for d in deps if d not in index]
    if missing:
        print(f"error: composes unknown tools: {', '.join(missing)}", file=sys.stderr)
        return 1

    # Verify the candidate before it can be seen under its real name. The lock
    # is not held here: a composed tool's test calls `foresight tool run`.
    store.ensure()
    candidate = store.tools_dir / f".candidate-{args.name}-{os.getpid()}"
    shutil.copyfile(source, candidate)
    candidate.chmod(0o755)
    passed, output = run_test(store, candidate, args.test)
    if not passed:
        candidate.unlink()
        print(f"REJECTED: test failed, '{args.name}' was not registered.\n"
              f"--- test output ---\n{output}", file=sys.stderr)
        return 1
    with store.lock():
        index = store.load_index()
        final = store.tools_dir / args.name
        os.replace(candidate, final)
        previous = index.get(args.name, {})
        index[args.name] = {
            "desc": " ".join(args.desc.split())[:300],
            "usage": args.usage,
            "test": args.test,
            "sha256": sha256_file(final),
            "deps": deps,
            "status": "verified",
            "created": previous.get("created", time.time()),
            "verified_at": time.time(),
            "uses": previous.get("uses", 0),
        }
        store.save_index(index)
    composed = f" (composes: {', '.join(deps)})" if deps else ""
    print(f"registered '{args.name}': test passed{composed}")
    return 0


def cmd_tool_run(args):
    store = Store(find_home())
    with store.lock():
        index = store.load_index()
        tool = index.get(args.name)
        if not tool:
            print(f"error: no tool '{args.name}'. See: foresight tool list", file=sys.stderr)
            return 127
        tool["uses"] = tool.get("uses", 0) + 1
        tool["last_used"] = time.time()
        store.save_index(index)
        store.append(store.ledger, {"ts": time.time(), "type": "tool_run", "tool": args.name})
    if tool.get("status") == "broken":
        print(f"foresight: warning: '{args.name}' failed its last verification", file=sys.stderr)
    path = store.tools_dir / args.name
    os.execve(str(path), [str(path), *args.args], tool_env(store))


def cmd_tool_list(args):
    index = Store(find_home()).load_index()
    if not index:
        print("toolbox is empty")
        return 0
    for name in sorted(index):
        tool = index[name]
        deps = f"  <- {', '.join(tool['deps'])}" if tool.get("deps") else ""
        print(f"{name:<24} {tool.get('status', '?'):<9} uses={tool.get('uses', 0):<4} "
              f"{tool.get('desc', '')}{deps}")
    return 0


def cmd_tool_show(args):
    store = Store(find_home())
    tool = store.load_index().get(args.name)
    if not tool:
        print(f"error: no tool '{args.name}'", file=sys.stderr)
        return 1
    print(json.dumps(tool, indent=2, sort_keys=True))
    print("--- source ---")
    print((store.tools_dir / args.name).read_text(errors="replace"))
    return 0


def cmd_tool_search(args):
    words = [w.lower() for w in args.query]
    index = Store(find_home()).load_index()
    ranked = []
    for name, tool in index.items():
        haystack = f"{name} {tool.get('desc', '')} {tool.get('usage') or ''}".lower()
        hits = sum(w in haystack for w in words)
        if hits:
            ranked.append((hits, name, tool))
    for _, name, tool in sorted(ranked, key=lambda r: (-r[0], r[1])):
        print(f"{name}: {tool.get('desc', '')}")
    return 0 if ranked else 1


def cmd_tool_verify(args):
    """Re-run tests. The project changes; verified tools must stay verified."""
    store = Store(find_home())
    index = store.load_index()
    names = [args.name] if args.name else sorted(index)
    results = {}
    for name in names:
        tool = index.get(name)
        if not tool:
            print(f"error: no tool '{name}'", file=sys.stderr)
            return 1
        path = store.tools_dir / name
        if not path.is_file():
            passed, why = False, "tool file is missing"
        elif sha256_file(path) != tool.get("sha256"):
            passed, why = False, "tool file changed since it was verified"
        else:
            passed, why = run_test(store, path, tool["test"])
        results[name] = passed
        print(f"{'ok    ' if passed else 'BROKEN'} {name}")
        if not passed:
            print("       " + why.strip().replace("\n", "\n       "))
    with store.lock():
        index = store.load_index()
        for name, passed in results.items():
            if name in index:
                index[name]["status"] = "verified" if passed else "broken"
                index[name]["verified_at"] = time.time()
        store.save_index(index)
    return 0 if all(results.values()) else 1


def cmd_tool_rm(args):
    store = Store(find_home())
    with store.lock():
        index = store.load_index()
        if args.name not in index:
            print(f"error: no tool '{args.name}'", file=sys.stderr)
            return 1
        dependents = [n for n, t in index.items() if args.name in (t.get("deps") or [])]
        if dependents:
            print(f"error: still used by: {', '.join(dependents)}", file=sys.stderr)
            return 1
        del index[args.name]
        store.save_index(index)
        try:
            (store.tools_dir / args.name).unlink()
        except OSError:
            pass
    print(f"removed '{args.name}'")
    return 0


# --------------------------------------------------------------------------
# Measurement
# --------------------------------------------------------------------------

def compute_stats(store):
    rows = store.read_jsonl(store.ledger)
    commands = [r for r in rows if r.get("type") != "tool_run"]
    scored = [r for r in commands if "hit" in r]
    hits = sum(r["hit"] for r in scored)

    sessions = {}
    for r in scored:
        s = sessions.setdefault(r.get("session"), {"first": r["ts"], "n": 0, "hits": 0})
        s["n"] += 1
        s["hits"] += r["hit"]
    curve = [{"session": k, "n": v["n"], "accuracy": v["hits"] / v["n"]}
             for k, v in sorted(sessions.items(), key=lambda kv: kv[1]["first"])]

    with_p = [r for r in scored if "p" in r["pred"]]
    brier = (sum((r["pred"]["p"] - r["hit"]) ** 2 for r in with_p) / len(with_p)) if with_p else None
    buckets = []
    for lo, hi in ((0, 0.6), (0.6, 0.8), (0.8, 0.95), (0.95, 1.01)):
        group = [r for r in with_p if lo <= r["pred"]["p"] < hi]
        if group:
            buckets.append({
                "range": f"{lo:.2f}-{min(hi, 1):.2f}", "n": len(group),
                "stated": sum(r["pred"]["p"] for r in group) / len(group),
                "observed": sum(r["hit"] for r in group) / len(group),
            })

    families = {}
    for r in commands:
        f = families.setdefault(r.get("sig", "?"), {"runs": 0, "ok": 0, "predicted": 0, "hits": 0})
        f["runs"] += 1
        f["ok"] += r.get("exit") == 0
        if "hit" in r:
            f["predicted"] += 1
            f["hits"] += r["hit"]
    surprising = sorted(
        ({"sig": k, **v, "misses": v["predicted"] - v["hits"]} for k, v in families.items()
         if v["predicted"] > v["hits"]),
        key=lambda f: -f["misses"])[:8]

    index = store.load_index()
    return {
        "commands": len(commands),
        "predicted": len(scored),
        "coverage": len(scored) / len(commands) if commands else None,
        "accuracy": hits / len(scored) if scored else None,
        "brier": brier,
        "calibration": buckets,
        "sessions": curve,
        "surprising_families": surprising,
        "lessons": len(store.read_jsonl(store.lessons)),
        "tools": {
            "count": len(index),
            "broken": sum(t.get("status") == "broken" for t in index.values()),
            "runs": sum(t.get("uses", 0) for t in index.values()),
            "reused": sum(t.get("uses", 0) > 0 for t in index.values()),
            "max_depth": max((composition_depth(index, n) for n in index), default=0),
        },
    }


def pct(x):
    return "n/a" if x is None else f"{100 * x:.0f}%"


def cmd_stats(args):
    store = Store(find_home())
    s = compute_stats(store)
    if args.json:
        print(json.dumps(s, indent=2))
        return 0
    print(f"State: {store.home}")
    print("\nWORLD MODEL")
    print(f"  commands seen        {s['commands']}")
    print(f"  predictions scored   {s['predicted']}  (coverage {pct(s['coverage'])})")
    print(f"  accuracy             {pct(s['accuracy'])}")
    if s["brier"] is not None:
        print(f"  Brier score          {s['brier']:.3f}  (0 is perfect; 0.25 is a coin flip at p=0.5)")
    if s["calibration"]:
        print("  calibration          stated -> observed")
        for b in s["calibration"]:
            print(f"    p in {b['range']}   {pct(b['stated'])} -> {pct(b['observed'])}  (n={b['n']})")
    if s["sessions"]:
        print("  accuracy by session, oldest first (the learning curve)")
        for i, c in enumerate(s["sessions"], 1):
            print(f"    #{i:<3} {pct(c['accuracy']):>4}  n={c['n']}")
    if s["surprising_families"]:
        print("  most surprising command families")
        for f in s["surprising_families"]:
            print(f"    {f['sig']:<28} {f['misses']} wrong of {f['predicted']} predicted; "
                  f"succeeds {pct(f['ok'] / f['runs'])} of {f['runs']} runs")
    print(f"  lessons recorded     {s['lessons']}")
    t = s["tools"]
    print("\nTOOLBOX")
    print(f"  tools                {t['count']}  ({t['broken']} broken)")
    print(f"  tool runs            {t['runs']}")
    print(f"  tools reused         {t['reused']} of {t['count']}")
    print(f"  max composition depth {t['max_depth']}")
    return 0


def cmd_suggest(args):
    """Procedures repeated often enough to be worth turning into a tool."""
    store = Store(find_home())
    counts = {}
    for r in store.read_jsonl(store.ledger):
        command = " ".join((r.get("command") or "").split())
        if not command or command.startswith("foresight "):
            continue
        multi_step = any(op in command for op in ("&&", "|", ";")) or len(command) >= 60
        if multi_step:
            entry = counts.setdefault(command, {"n": 0, "sessions": set()})
            entry["n"] += 1
            entry["sessions"].add(r.get("session"))
    candidates = sorted(((v["n"], len(v["sessions"]), k) for k, v in counts.items()
                         if v["n"] >= args.min), reverse=True)[:10]
    if not candidates:
        print(f"no multi-step command has been repeated {args.min}+ times yet")
        return 0
    print("Repeated procedures that are not tools yet:")
    for n, sessions, command in candidates:
        print(f"  {n}x in {sessions} session(s): {command}")
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(prog="foresight", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("hook", help="hook entry point; reads the event on stdin").set_defaults(fn=cmd_hook)

    stats = sub.add_parser("stats", help="prediction accuracy, calibration, tool reuse")
    stats.add_argument("--json", action="store_true")
    stats.set_defaults(fn=cmd_stats)

    suggest = sub.add_parser("suggest", help="repeated procedures worth turning into tools")
    suggest.add_argument("--min", type=int, default=3)
    suggest.set_defaults(fn=cmd_suggest)

    lesson = sub.add_parser("lesson", help="durable facts learned from surprises")
    lsub = lesson.add_subparsers(dest="sub", required=True)
    add = lsub.add_parser("add")
    add.add_argument("--pattern", help="regex; the lesson resurfaces before matching commands")
    add.add_argument("text")
    add.set_defaults(fn=cmd_lesson_add)
    lsub.add_parser("list").set_defaults(fn=cmd_lesson_list)
    rm = lsub.add_parser("rm")
    rm.add_argument("id")
    rm.set_defaults(fn=cmd_lesson_rm)

    tool = sub.add_parser("tool", help="the verified tool library")
    tsub = tool.add_subparsers(dest="sub", required=True)
    add = tsub.add_parser("add", help="register a tool; refused unless --test passes")
    add.add_argument("name")
    add.add_argument("--desc", required=True)
    add.add_argument("--file", required=True)
    add.add_argument("--test", required=True, help="shell command; exit 0 iff $TOOL works")
    add.add_argument("--usage")
    add.add_argument("--force", action="store_true")
    add.set_defaults(fn=cmd_tool_add)
    run = tsub.add_parser("run")
    run.add_argument("name")
    run.add_argument("args", nargs=argparse.REMAINDER)
    run.set_defaults(fn=cmd_tool_run)
    tsub.add_parser("list").set_defaults(fn=cmd_tool_list)
    show = tsub.add_parser("show")
    show.add_argument("name")
    show.set_defaults(fn=cmd_tool_show)
    search = tsub.add_parser("search")
    search.add_argument("query", nargs="+")
    search.set_defaults(fn=cmd_tool_search)
    verify = tsub.add_parser("verify", help="re-run tests; flags tools that no longer pass")
    verify.add_argument("name", nargs="?")
    verify.set_defaults(fn=cmd_tool_verify)
    rm = tsub.add_parser("rm")
    rm.add_argument("name")
    rm.set_defaults(fn=cmd_tool_rm)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.fn(args) or 0


if __name__ == "__main__":
    sys.exit(main())

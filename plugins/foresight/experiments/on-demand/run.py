#!/usr/bin/env python3
"""On-demand software: can a program nobody reviewed be trusted?

For each task, one model call writes the program from the spec, and a second,
independent call writes a test script from the same spec without seeing the
program. The program is admitted only if the test script passes; on failure the
author gets the test output and may repair, up to MAX_REPAIRS times. Neither
call has tools or file access.

Every draft is also scored against hidden reference cases that neither call
sees. That gives the number that matters: how often the gate admits a program
that is in fact wrong.

    python3 run.py validate               # hidden cases agree with the references
    python3 run.py run --out results.json
    python3 run.py report results.json
    python3 run.py mutants results.json   # inject bugs: does the gate catch them?
"""

import argparse
import ast
import concurrent.futures
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tasks import TASKS  # noqa: E402

MAX_REPAIRS = 2
CASE_TIMEOUT_S = 10
GATE_TIMEOUT_S = 90
MODEL_TIMEOUT_S = 300

AUTHOR_SYSTEM = (
    "You write small command-line programs. Reply with exactly one fenced python code "
    "block holding a complete Python 3 script that uses only the standard library, and "
    "nothing else. The script is run as `python3 SCRIPT ARGS...`."
)
TESTER_SYSTEM = (
    "You write tests for a command-line program that you cannot see; you only have its "
    "specification. Reply with exactly one fenced python code block holding a complete "
    "Python 3 test script that uses only the standard library, and nothing else. The "
    "program under test must be run as `[sys.executable, os.environ['TOOL'], *args]`. "
    "The script creates any files it needs in a temporary directory, prints each failed "
    "check with what was expected and what was received, and exits with status 0 only if "
    "every check passes. Check only behaviour that the specification states, and cover "
    "the edge cases and error cases it states. Work each expected output out carefully: "
    "a wrong expectation rejects a correct program."
)


# -- hidden reference cases ------------------------------------------------

def normalise(text):
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def run_case(tool, case):
    """Return None if the case passes, else a short reason."""
    with tempfile.TemporaryDirectory() as tmp:
        for rel, content in case["files"].items():
            path = Path(tmp) / rel
            if rel.endswith("/"):
                path.mkdir(parents=True, exist_ok=True)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        try:
            proc = subprocess.run(
                [sys.executable, str(tool), *case["args"]], input=case["stdin"], cwd=tmp,
                capture_output=True, text=True, timeout=CASE_TIMEOUT_S, encoding="utf-8",
            )
        except subprocess.TimeoutExpired:
            return "timeout"
    if case["exit"] == "nz":
        if proc.returncode == 0:
            return "expected a non-zero exit"
    elif proc.returncode != 0:
        return f"exit {proc.returncode}: {proc.stderr.strip()[-200:]}"
    if case["out"] is not None:
        if case["json"]:
            try:
                same = json.loads(proc.stdout) == json.loads(case["out"])
            except ValueError:
                same = False
        else:
            same = normalise(proc.stdout) == normalise(case["out"])
        if not same:
            return f"stdout {proc.stdout[:200]!r} != {case['out'][:200]!r}"
    return None


def hidden_failures(tool, name):
    failures = []
    for i, case in enumerate(TASKS[name]["cases"]):
        reason = run_case(tool, case)
        if reason:
            failures.append({"case": i, "args": case["args"], "stdin": case["stdin"][:80],
                             "reason": reason})
    return failures


def cmd_validate(args):
    bad = 0
    with tempfile.TemporaryDirectory() as tmp:
        for name, task in TASKS.items():
            tool = Path(tmp) / name
            tool.write_text(task["ref"], encoding="utf-8")
            for failure in hidden_failures(tool, name):
                bad += 1
                print(f"{name} case {failure['case']} {failure['args']}: {failure['reason']}")
    cases = sum(len(t["cases"]) for t in TASKS.values())
    print(f"{len(TASKS)} tasks, {cases} hidden cases, {bad} disagreements with the references")
    return 1 if bad else 0


# -- model calls -----------------------------------------------------------

def ask(system, prompt, model=None):
    """One tool-less model call in an empty directory. Returns (text, cost, seconds)."""
    command = ["claude", "-p", prompt, "--system-prompt", system, "--tools", "",
               "--output-format", "json", "--setting-sources", "", "--strict-mcp-config",
               "--disable-slash-commands"]
    if model:
        command += ["--model", model]
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run(command, cwd=tmp, stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=MODEL_TIMEOUT_S)
    data = json.loads(proc.stdout)
    if data.get("is_error"):
        raise RuntimeError(str(data.get("result"))[:300])
    return data["result"], data.get("total_cost_usd") or 0.0, (data.get("duration_ms") or 0) / 1000


def code_block(text):
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", text, re.DOTALL)
    if not blocks:
        raise ValueError("no code block in the reply")
    return max(blocks, key=len)


def run_gate(test_script, tool):
    env = {**os.environ, "TOOL": str(tool)}
    with tempfile.TemporaryDirectory() as tmp:
        try:
            proc = subprocess.run([sys.executable, str(test_script)], cwd=tmp, env=env,
                                  capture_output=True, text=True, timeout=GATE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return False, "the test script timed out"
    return proc.returncode == 0, (proc.stdout + proc.stderr)[-3000:]


def run_task(name, workdir, model):
    spec = TASKS[name]["spec"]
    request = f"Command name: {name}\n\nSpecification:\n{spec}"
    out = {"task": name, "drafts": [], "admitted": False, "cost": 0.0, "seconds": 0.0}
    task_dir = Path(workdir) / name
    task_dir.mkdir(parents=True, exist_ok=True)
    try:
        # The two calls are independent: neither sees the other's output.
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            author = pool.submit(ask, AUTHOR_SYSTEM, request, model)
            tester = pool.submit(ask, TESTER_SYSTEM, request, model)
            (program_reply, cost_a, secs_a), (test_reply, cost_t, secs_t) = author.result(), tester.result()
        out["cost"] += cost_a + cost_t
        out["seconds"] += max(secs_a, secs_t)
        test_script = task_dir / "gate_test.py"
        test_script.write_text(code_block(test_reply), encoding="utf-8")
        program = code_block(program_reply)

        for attempt in range(MAX_REPAIRS + 1):
            tool = task_dir / f"draft{attempt}.py"
            tool.write_text(program, encoding="utf-8")
            passed, gate_output = run_gate(test_script, tool)
            failures = hidden_failures(tool, name)
            out["drafts"].append({"gate_passed": passed, "hidden_correct": not failures,
                                  "hidden_failures": failures,
                                  "gate_output": "" if passed else gate_output[-1500:]})
            if passed:
                out["admitted"] = True
                break
            if attempt == MAX_REPAIRS:
                break
            reply, cost, secs = ask(AUTHOR_SYSTEM, (
                f"{request}\n\nYour program:\n```python\n{program}\n```\n\n"
                "An independent test script, written from the same specification, failed on it "
                f"with this output:\n```\n{gate_output}\n```\n\n"
                "Correct the program so that it follows the specification. If a failed check "
                "contradicts the specification, keep the behaviour the specification states."
            ), model)
            out["cost"] += cost
            out["seconds"] += secs
            program = code_block(reply)
    except Exception as exc:  # one broken task must not lose the others
        out["error"] = f"{type(exc).__name__}: {exc}"[:300]
    return out


def cmd_run(args):
    names = args.tasks.split(",") if args.tasks else list(TASKS)
    workdir = Path(args.workdir)
    if workdir.exists():
        shutil.rmtree(workdir)
    results = []
    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        for result in pool.map(lambda n: run_task(n, workdir, args.model), names):
            final = result["drafts"][-1] if result["drafts"] else {}
            print(f"{result['task']:<16} admitted={result['admitted']!s:<5} "
                  f"correct={final.get('hidden_correct')!s:<5} drafts={len(result['drafts'])} "
                  f"{result.get('error', '')}", flush=True)
            results.append(result)
    Path(args.out).write_text(json.dumps(results, indent=1) + "\n")
    return cmd_report(argparse.Namespace(results=args.out))


# -- report ----------------------------------------------------------------

def cmd_report(args):
    results = json.loads(Path(args.results).read_text())
    done = [r for r in results if r["drafts"]]
    errors = [r["task"] for r in results if "error" in r]
    final = {r["task"]: r["drafts"][-1] for r in done}
    admitted = [r for r in done if r["admitted"]]
    good = [r["task"] for r in admitted if final[r["task"]]["hidden_correct"]]
    false_admit = [r["task"] for r in admitted if not final[r["task"]]["hidden_correct"]]
    rejected = [r for r in done if not r["admitted"]]
    false_reject = [r["task"] for r in rejected if final[r["task"]]["hidden_correct"]]
    first_ok = [r["task"] for r in done if r["drafts"][0]["hidden_correct"]]
    first_gate = [r["task"] for r in done if r["drafts"][0]["gate_passed"]]
    repaired = [r["task"] for r in admitted if len(r["drafts"]) > 1]
    drafts = [d for r in done for d in r["drafts"]]

    def share(part, whole):
        return f"{len(part)}/{len(whole)}" + (f" ({100 * len(part) / len(whole):.0f}%)" if whole else "")

    n = len(done)
    print(f"\nTasks completed: {n} of {len(results)}" + (f"  (errors: {', '.join(errors)})" if errors else ""))
    print(f"First draft correct, no gate        {share(first_ok, done)}")
    print(f"First draft passes the gate         {share(first_gate, done)}")
    print(f"Admitted (after up to {MAX_REPAIRS} repairs)     {share(admitted, done)}   repaired: {', '.join(repaired) or 'none'}")
    print(f"Admitted and correct                {share(good, done)}")
    print(f"Admitted but wrong, of admitted     {share(false_admit, admitted)}   {', '.join(false_admit)}")
    print(f"Rejected although correct           {share(false_reject, done)}   {', '.join(false_reject)}")
    print(f"Rejected and wrong                  {len(rejected) - len(false_reject)}/{n}")
    print("\nGate verdict against the hidden cases, over all drafts:")
    for gate in (True, False):
        for correct in (True, False):
            count = sum(d["gate_passed"] == gate and d["hidden_correct"] == correct for d in drafts)
            print(f"  gate {'passes' if gate else 'fails ':<6} / program {'correct' if correct else 'wrong  '}: {count}")
    if done:
        seconds = sorted(r["seconds"] for r in done)
        print(f"\nCost per task: ${sum(r['cost'] for r in done) / n:.3f} mean, "
              f"${sum(r['cost'] for r in done):.2f} total")
        print(f"Model time per task: {seconds[len(seconds) // 2]:.0f}s median, {seconds[-1]:.0f}s max")
    return 0


# -- gate sensitivity: inject bugs into admitted programs --------------------

class Mutator(ast.NodeTransformer):
    """Apply one small change at site number `target`; count sites when target is None."""

    COMPARE = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt,
               ast.Eq: ast.NotEq, ast.NotEq: ast.Eq}
    BINOP = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.Add, ast.Mod: ast.Mult}

    def __init__(self, target=None):
        self.target, self.sites = target, 0

    def hit(self):
        self.sites += 1
        return self.sites - 1 == self.target

    def visit_Compare(self, node):
        self.generic_visit(node)
        if type(node.ops[0]) in self.COMPARE and self.hit():
            node.ops[0] = self.COMPARE[type(node.ops[0])]()
        return node

    def visit_BinOp(self, node):
        self.generic_visit(node)
        if type(node.op) in self.BINOP and self.hit():
            node.op = self.BINOP[type(node.op)]()
        return node

    def visit_BoolOp(self, node):
        self.generic_visit(node)
        if self.hit():
            node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
        return node

    def visit_Constant(self, node):
        if isinstance(node.value, int) and not isinstance(node.value, bool) and self.hit():
            return ast.copy_location(ast.Constant(node.value + 1), node)
        return node

    def visit_If(self, node):
        self.generic_visit(node)
        if self.hit():
            node.test = ast.UnaryOp(ast.Not(), node.test)
        return node


def mutants_of(source, limit, seed):
    counter = Mutator()
    counter.visit(ast.parse(source))
    sites = list(range(counter.sites))
    random.Random(seed).shuffle(sites)
    for site in sites[:limit]:
        tree = Mutator(site).visit(ast.parse(source))
        yield ast.unparse(ast.fix_missing_locations(tree))


def cmd_mutants(args):
    """How often does the gate reject a program that the hidden cases show is wrong?"""
    results = json.loads(Path(args.results).read_text())
    workdir = Path(args.workdir)
    jobs = []
    for r in results:
        if not (r["admitted"] and r["drafts"][-1]["hidden_correct"]):
            continue
        task_dir = workdir / r["task"]
        source = (task_dir / f"draft{len(r['drafts']) - 1}.py").read_text()
        for i, mutant in enumerate(mutants_of(source, args.per_task, r["task"])):
            jobs.append((r["task"], i, mutant, task_dir / "gate_test.py"))

    def judge(job):
        name, i, mutant, gate = job
        with tempfile.TemporaryDirectory() as tmp:
            tool = Path(tmp) / f"{name}.py"
            tool.write_text(mutant, encoding="utf-8")
            wrong = bool(hidden_failures(tool, name))
            passed, _ = run_gate(gate, tool)
        return name, wrong, passed

    rows = []
    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        rows = list(pool.map(judge, jobs))
    wrong = [r for r in rows if r[1]]
    escaped = [r for r in wrong if r[2]]
    harmless = [r for r in rows if not r[1]]
    print(f"Mutants generated: {len(rows)} from {len({r[0] for r in rows})} admitted, correct programs")
    print(f"Wrong by the hidden cases: {len(wrong)}")
    print(f"  rejected by the gate: {len(wrong) - len(escaped)} ({100 * (len(wrong) - len(escaped)) / max(len(wrong), 1):.0f}%)")
    print(f"  admitted by the gate: {len(escaped)} ({100 * len(escaped) / max(len(wrong), 1):.0f}%)")
    print(f"Pass the hidden cases: {len(harmless)}, of which the gate rejects "
          f"{sum(not r[2] for r in harmless)}")
    by_task = {}
    for name, _, passed in wrong:
        t = by_task.setdefault(name, [0, 0])
        t[0] += 1
        t[1] += passed
    leaky = sorted(((e, n, name) for name, (n, e) in by_task.items() if e), reverse=True)
    if leaky:
        print("Tasks whose gate let wrong mutants through (escaped/wrong):")
        for e, n, name in leaky:
            print(f"  {name:<16} {e}/{n}")
    if args.out:
        Path(args.out).write_text(json.dumps(
            [{"task": n, "wrong": w, "gate_passed": g} for n, w, g in rows]) + "\n")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate").set_defaults(fn=cmd_validate)
    run = sub.add_parser("run")
    run.add_argument("--out", default="results.json")
    run.add_argument("--tasks", help="comma-separated subset")
    run.add_argument("--workers", type=int, default=5)
    run.add_argument("--model")
    run.add_argument("--workdir", default=os.path.join(tempfile.gettempdir(), "on-demand-work"))
    run.set_defaults(fn=cmd_run)
    report = sub.add_parser("report")
    report.add_argument("results")
    report.set_defaults(fn=cmd_report)
    mutants = sub.add_parser("mutants")
    mutants.add_argument("results")
    mutants.add_argument("--workdir", default=os.path.join(tempfile.gettempdir(), "on-demand-work"))
    mutants.add_argument("--per-task", type=int, default=25)
    mutants.add_argument("--workers", type=int, default=8)
    mutants.add_argument("--out")
    mutants.set_defaults(fn=cmd_mutants)
    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())

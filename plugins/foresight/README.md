# Foresight

A research prototype of two ways an agent can learn without changing its weights.

- **A world model of the project.** Before a Bash command with an uncertain outcome, Claude writes down what it expects. Hooks score the prediction against what happened. A wrong prediction is fed straight back, and the lesson Claude draws from it resurfaces in later sessions.
- **A verified tool library.** Claude turns procedures it worked out into scripts. A script is registered only if its test passes, so what accumulates is checkable code, and tools can call other tools.

Both stores live in the project and carry across sessions.

## Status

This is a prototype for experiments, not a finished feature.

What has been checked: the unit tests pass, and in four real Claude Code sessions on a toy project the full loop ran. Claude annotated its commands, a wrong prediction was fed back, Claude recorded a lesson, `/foresight:distill` registered a tool, and a fresh session answered "Are the tests green?" with one call to that tool.

What has not been shown: that any of this makes Claude better at real work. Four sessions on a toy project are a smoke test. Whether prediction accuracy rises with experience, and whether tools reduce the work a task takes, are the open questions the plugin exists to measure. See [Experiments](#experiments).

## How it works

### Predictions

At session start, Claude is asked to annotate commands whose outcome is not obvious:

```bash
npm test  # predict: exit=0 p=0.8 out~12 passed
```

| Field | Meaning |
|-------|---------|
| `exit=N` or `exit=!0` | The expected exit code |
| `p=0.x` | Claude's probability that the whole prediction holds |
| `out~REGEX` | Optional, must come last: text the output will contain |

A prediction holds only if every stated field holds. The annotation is a shell comment, so the command runs unchanged.

| Hook | What it does |
|------|--------------|
| SessionStart | Explains the protocol and lists the toolbox and the latest lessons |
| PreToolUse (Bash) | Stores the prediction. Shows lessons whose pattern matches the command, once per session |
| PostToolUse, PostToolUseFailure (Bash) | Scores the prediction and logs the command. On a miss, tells Claude what differed and asks it to record a lesson if the cause is durable |

Correct predictions produce no output. Backgrounded and interrupted commands are logged but not scored.

### Lessons

```bash
foresight lesson add --pattern '^npm test' 'Tests need the db container: run `make db-up` first'
foresight lesson list
foresight lesson rm ID
```

With `--pattern`, the lesson is shown again just before any command the regex matches.

### Tools

```bash
foresight tool add run-tests \
  --desc 'Run the suite with the environment it needs and print pass/fail counts' \
  --usage 'run-tests [-v]' \
  --file /tmp/run-tests.sh \
  --test '"$TOOL" | grep -q "RESULT: PASS"'
```

The test runs with the candidate's path in `$TOOL`. If it exits non-zero, nothing is registered.

| Command | Purpose |
|---------|---------|
| `foresight tool run NAME [args]` | Run a tool and count the use |
| `foresight tool list`, `show NAME`, `search WORDS` | Browse the library |
| `foresight tool verify [NAME]` | Re-run tests. A tool that no longer passes is marked broken and flagged at session start |
| `foresight tool rm NAME` | Remove a tool. Refused while another tool calls it |

A script that calls `foresight tool run OTHER` is recorded as composing `OTHER`.

### Slash commands

| Command | Purpose |
|---------|---------|
| `/foresight:distill [focus]` | Review the session and register procedures worth keeping as tools |
| `/foresight:stats` | Show and interpret the measurements |

## Measurements

`foresight stats` (add `--json` for scripts) reports:

- **Coverage:** the share of Bash commands that carried a prediction.
- **Accuracy by session:** the learning curve.
- **Brier score and calibration:** whether a stated `p=0.8` holds 80% of the time.
- **Most surprising command families:** where the world model is weakest.
- **Toolbox:** tool count, broken tools, runs, how many tools were ever reused, and the deepest composition.

`foresight suggest` lists multi-step commands repeated three or more times that are not tools yet.

## Experiments

The measurements are meant for comparisons such as these:

1. **Does the world model improve?** Run a fixed set of tasks on one repository over many sessions. Compare accuracy and Brier score in early and late sessions, with lessons enabled and with `lessons.jsonl` emptied before each session.
2. **Do tools pay off?** Run the same tasks with the toolbox kept and with it emptied each session. Compare tool calls and turns per task, and task success.
3. **Does predicting help or only cost?** Run the same tasks with and without the plugin. Predictions take tokens, so success rate and total cost both matter.

Each needs a task set with a success criterion that can be checked automatically. The plugin does not provide one.

## Installation

```bash
claude --plugin-dir /path/to/plugins/foresight
```

Allow the CLI so that Claude is not prompted on each call, for example in `.claude/settings.json`:

```json
{ "permissions": { "allow": ["Bash(foresight:*)"] } }
```

Requires Python 3.8 or newer on a POSIX system. There are no other dependencies.

## State

Everything is stored in `<project>/.claude/foresight/`. Set `FORESIGHT_HOME` to use another directory.

| Path | Contents | In git |
|------|----------|--------|
| `tools/`, `tools/index.json` | Tool scripts and their metadata | Yes, if you want to share them |
| `lessons.jsonl` | Lessons | Yes, if you want to share them |
| `ledger.jsonl` | Every Bash command seen, with its prediction and outcome | Ignored |
| `pending/` | Predictions awaiting an outcome | Ignored |

## Limitations and cautions

- **Shared state is trusted input.** Lessons and tool descriptions are placed in Claude's context, and tools are executed. Review changes to `.claude/foresight/` in a repository as you would review changes to `CLAUDE.md` or to scripts.
- **The ledger holds commands verbatim,** up to 500 characters each. A secret typed on a command line ends up there. The file is ignored by git but stays on disk.
- **Scoring is all or nothing.** A prediction with the right exit code and the wrong output pattern counts as a miss.
- **Claude chooses what to predict.** It can skip commands it is unsure of, which flatters accuracy. Read accuracy together with coverage.
- **A tool is only as good as its test.** The gate checks that the test passes, not that the test is meaningful.
- **Only Bash is modeled.** File edits and other tools are not predicted.

## Development

```bash
python3 -m unittest discover plugins/foresight/tests
```

---
description: Turn procedures worked out in this session into verified, reusable tools
argument-hint: "[what to distill]"
allowed-tools: Bash(foresight:*), Write, Read
---

## Context

- Tools that already exist: !`foresight tool list`
- Procedures repeated across sessions: !`foresight suggest`

## Your task

Find procedures worth keeping and register each as a tool. Focus: $ARGUMENTS

A procedure is worth a tool when it took several steps to get right, it will be needed again, and it can be given inputs as arguments. A one-line command anyone would guess is not.

For each candidate:

1. Check the list above. If an existing tool does part of the job, call it with `foresight tool run NAME` inside the new script instead of repeating its logic.
2. Write the script to a temporary file. Take inputs as arguments, exit non-zero on failure, and print a usage line when called wrongly. Hard-code nothing that is specific to today's task.
3. Write a test that exercises the script on a real case and exits 0 only if the result is right. `$TOOL` holds the script's path. A test that only checks the script starts is not a test.
4. Register it: `foresight tool add NAME --desc '...' --usage 'NAME <args>' --file SCRIPT --test 'CMD'`. If it is rejected, fix the script or the test and try again; do not weaken the test to make it pass.

Finish by listing what was registered, what was rejected and why, and anything you judged not worth a tool.

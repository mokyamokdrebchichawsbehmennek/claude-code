---
description: Show how well Claude predicts this project, and how much the toolbox is reused
allowed-tools: Bash(foresight stats:*), Bash(foresight suggest:*)
---

## Measurements

- World model and toolbox: !`foresight stats`
- Repeated procedures: !`foresight suggest`

## Your task

Interpret these numbers for the user in a few sentences. Say plainly:

1. Whether prediction accuracy is rising across sessions, flat, or too thin to tell. Fewer than about 20 scored predictions is too thin: say so instead of reading a trend into it.
2. Whether confidence is calibrated: compare stated with observed in each bucket and name the direction of any gap.
3. Which command families surprise most, and what that suggests is still missing from the lessons.
4. Whether tools are being reused or only accumulated.

Do not run other commands.

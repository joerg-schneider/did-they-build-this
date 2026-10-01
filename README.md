# Did they build this?

A shiny new repo turns up overnight. 40k lines, a logo, maybe a Discord, and a README so long and dense you give up halfway. Odds are the owner never made it to the end either. It's three days old.

Did you ever see a massive new project pop up and wonder: **is this AI slop, or did actual human energy go into it?**

This project gives you an answer backed by evidence. Paste a GitHub link and Claude clones the repo, goes through its history, reads the arguments in its issues and PRs, and tells you how much of it a human actually cared about.

## What it looks at

| Signal | Question | Weight |
|---|---|---|
| **Discourse** | Are humans talking? Issues, reviews, real arguments. | 0.40 |
| **Oversight** | Did another human review the code before it got merged? | 0.25 |
| **Authorship** | How much of the code carries agent fingerprints? | 0.20 |
| **Stability** | Does the code survive, or get rewritten every week? | 0.15 |

Two signals cost a lot of points (up to −40 each):

- **PRs over 5,000 lines.** Nobody reviewed that. Nobody.
- **Merges with no approval from another human.** Shipping your own work with no second pair of eyes.

The result is a **Human Contribution Index** from 0 to 100, plus a verdict from *Human-led* down to *Mostly autonomous agents*. The report links to the threads and PRs it's based on.

## Usage

You need [Claude Code](https://claude.com/claude-code), `git`, Python 3, and a logged-in [`gh`](https://cli.github.com/). Private repos work if your account can read them.

In Claude Code, from this folder:

```
Did they build this? https://github.com/<owner>/<repo>
```

The report is written to `runs/<owner>__<repo>/report.md`.

If you just want the numbers, without Claude reviewing samples by hand:

```bash
python3 .claude/skills/collect-repo-evidence/scripts/collect.py <owner>/<repo>
python3 .claude/skills/assess-human-contribution/scripts/metrics.py runs/<owner>__<repo>
```

For small repos, raise the limits (`--max-prs 2000 --max-review-prs 1000`) to cover the full history. Weights, penalties and the size limit are set at the top of `metrics.py`.

## Fine print

These are heuristics, not a polygraph. Squash merges remove agent co-author trailers, solo maintainers get penalized for having nobody to approve their PRs, and lockfiles bloat PR sizes. The skill tells Claude to check samples by hand and to report these caveats, not hide them.

## Why this matters

Code is cheap now. Anyone can generate a hundred thousand lines before lunch, so the line count tells you nothing.

What still costs something is **human attention**: a maintainer who pushes back on a PR, an issue thread where three people argue about edge cases until someone finds the real bug, a reviewer who types "why?" and means it, code that has been read, questioned and lived with long enough to stay stable.

That friction **is** the quality signal. Discussion shows that someone cares whether the project is right, not just whether it exists. Reviews show that someone besides its author stood behind the code. Stable history shows that decisions were made carefully rather than regenerated on every whim. Together they're the signs of a project that will still be maintained next year, one that answers your bug report and that you can build on.

Agents can write the code. Only people can care about it. **Look for the humans.**

---
name: assess-human-contribution
description: Estimate how much of a GitHub project is human-made versus agent-made, from code authorship, review oversight, issue/PR discourse and code stability over git history. Use when the user pastes a repo link and asks who built it, how much is AI/agent-generated, or how much human contribution went in.
---

# Assess human contribution

Goal: a defensible estimate of HUMAN contribution. Human activity (discussion, reviews, issues, steering) outranks raw code volume, because agents produce code cheaply but humans decide what gets built and what gets accepted.

## Workflow

1. **Collect.** If `runs/<owner>__<repo>/manifest.json` is missing or stale, run the `collect-repo-evidence` skill.
2. **Compute.**
   ```bash
   python3 .claude/skills/assess-human-contribution/scripts/metrics.py runs/<owner>__<repo>
   ```
   Writes `metrics.json` (full, including monthly timeline and hotspots) and prints the headline numbers.
3. **Verify the heuristics** (required; the script only finds explicit markers):
   - Sample ~10 `unmarked` commits from `commits.json` and look at the diffs (`git -C runs/<o>__<r>/src show <sha>`). Signs of unmarked agent work: big multi-file commits with uniform style, exhaustive docstrings, a polished bullet-list message, many commits in a few minutes.
   - Sample ~10 `agent_marked` items and check for false positives. Projects about AI tools mention "Claude" or "Copilot" in normal text.
   - Read the 5 longest human threads (issues or PR comments). Decide whether they are substantive (design debate, bug reproduction, requirements) or rubber-stamping ("LGTM", "+1", "thanks").
   - Check whether "human" accounts post text that reads like an agent (agents often comment through the user's token).
4. **Report.** Write `runs/<owner>__<repo>/report.md` using the template below, then summarize it in chat.

## Metrics (from `metrics.py`)

| component | weight | definition |
|---|---|---|
| discourse | **0.40** | human share of words in issues, PR descriptions, issue comments, review comments, review bodies. Quoted lines and code blocks are excluded. |
| oversight | **0.25** | share of sampled merged PRs that got a review, or a ≥3-word comment, from a human who is not the PR author |
| authorship | 0.20 | share of HEAD lines (by blame) from commits with no agent marker |
| stability | 0.15 | share of lines added by commits older than 30 days that still survive at HEAD |

`base_index` = weighted mean × 100. When a component is missing (e.g. no merged PRs), its weight is dropped and the rest are renormalized; the index then reports `missing_components`. Weights live in `WEIGHTS` in `metrics.py`.

### Penalties

`human_contribution_index` = `base_index` minus penalties, floored at 0. Each penalty is `PENALTIES[k] × offending share of sampled merged PRs`, so a repo where every merged PR offends loses the full amount:

| penalty | max points | offending merged PR |
|---|---|---|
| `large_pr` | **40** | additions + deletions > `LARGE_PR_LINES` (5,000). Nobody reviews that much line by line. |
| `unapproved_merge` | **40** | no human other than the author has an `APPROVED` review as their latest decisive review. A later `CHANGES_REQUESTED` or a dismissal cancels the approval. Bot approvals don't count. Merging alone doesn't count either. |

The offending PRs are listed in `oversight.large_prs` and `oversight.unapproved_merges`. Check the biggest ones: a large PR made of lockfile, generated or vendored code is a weaker signal than 5,000 hand-reviewable lines. Point this out in the report, but don't recompute the number.

Supporting signals (reported, not weighted): `changes_requested_rate`, `human_issue_driven_pr_rate` (merged PRs that close a human-opened issue), `agent_or_bot_authored_pr_rate`, `distinct_human_participants`, `open_issues_by_humans`, per-class `survival_rate_mature` (agent vs unmarked lines: does agent code get rewritten more?), `churn_ratio` (deleted/added), monthly add/delete and per-class commit counts, file hotspots.

Penalties apply only to merged PRs. A repo that pushes straight to the default branch avoids both. If most commits aren't linked to a PR, say so and look up the largest direct commits in `commits.json`.

Text classes: `bot` (account type Bot or known bot login), `agent_marked` (human account, text carries an agent marker), `human` (everything else).
Commit classes: `agent` (bot identity, agent co-author trailer, or "Generated with …" footer), `unmarked`.

## Caveats (always state them)

- Oversight uses only merged PRs inside the `--max-prs` window. If `merged_prs_sampled` < 20, re-collect with a larger `--max-prs` before trusting the number.
- `unmarked` ≠ human. Squash-merges and edited messages remove trailers. Treat authorship as an upper bound for human code.
- Capped collection windows cover only recent activity; quote the `limits` from `manifest.json`.
- Blame credits the last commit that touched a line. Reformat commits steal credit, and `-w` only offsets whitespace changes.
- Survival is biased toward recent commits; that is why only commits older than 30 days count.

## Report template

```markdown
# Did they build this? <owner>/<repo> @ <head sha short>

**Human Contribution Index: NN / 100** (computed) — **Verdict: <Human-led | Human-steered, agent-built | Agent-led with human review | Mostly autonomous agents>**
<two sentences: why, citing the strongest evidence>

## Scorecard
| component | value | weight | note |
|---|---|---|---|
| Discourse | | 0.40 | |
| Oversight | | 0.25 | |
| Authorship | | 0.20 | |
| Stability | | 0.15 | |
| **Base index** | | | |
| Penalty: PRs > 5,000 lines | −NN | rate | worst offenders (links) |
| Penalty: merged without approval | −NN | rate | self-merges vs maintainer-merged contributions |

## Human activity
Participants, substantive vs rubber-stamp threads (with links), issue-driven work, changes requested.

## Code authorship and stability
Agent vs unmarked lines/commits, survival per class, churn trend over time (call out when agent usage started), hotspots.

## Manual verification
What the commit/comment samples showed; any adjustment to the computed picture, with reasons.

## Caveats
Collection limits, heuristic gaps that apply to this repo.
```

Verdict bands by index: ≥70 Human-led · 50–70 Human-steered, agent-built · 30–50 Agent-led with human review · <30 Mostly autonomous agents. Start from the computed index. Move at most one band, only on evidence from the manual verification step, and say why.

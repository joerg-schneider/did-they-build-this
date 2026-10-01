---
name: collect-repo-evidence
description: Clone a GitHub repository and dump its git history, line-survival blame, PRs, reviews, issues and comments as JSON via the gh CLI. Use when the user pastes a GitHub repo link to analyze, or when assess-human-contribution needs fresh evidence.
---

# Collect repo evidence

Turns a repo link into a local evidence folder. No interpretation here; scoring lives in `assess-human-contribution`.

## Preconditions

- `gh auth status` succeeds and the account can read the repo (private repos work).
- `git`, `python3` on PATH.

## Run

```bash
python3 .claude/skills/collect-repo-evidence/scripts/collect.py <url|owner/repo>
```

Accepts `https://github.com/o/r`, `git@github.com:o/r.git`, `o/r`, GitHub Enterprise URLs.
Re-running fetches and hard-resets the existing clone to `origin/HEAD`.

Limits (defaults keep big repos to a few minutes; raise them for small repos to get full coverage):

| flag | default | scope |
|---|---|---|
| `--max-prs` | 300 | newest PRs, all states |
| `--max-issues` | 500 | newest issues (API includes PRs; flagged `is_pr`) |
| `--max-comments` | 3000 | newest issue comments and newest review comments, each |
| `--max-review-prs` | 150 | newest merged PRs for which size, merger and reviews are fetched (2 API calls each) |
| `--max-blame-files` | 3000 | text files blamed at HEAD |
| `--out` | `runs` | output root |

## Output: `runs/<owner>__<repo>/`

| file | content |
|---|---|
| `src/` | full clone |
| `repo.json` | repo metadata |
| `pulls.json`, `issues.json` | slimmed records: author login + type, body, state, timestamps; sampled merged PRs also carry `additions`, `deletions`, `changed_files`, `merged_by` |
| `issue_comments.json`, `review_comments.json` | comment author, body, `number` of the issue/PR |
| `reviews.json` | `{pr_number: [review]}` with `state` (APPROVED / CHANGES_REQUESTED / COMMENTED) |
| `commits.json` | every commit on HEAD: author/committer identity, full message (trailers included), per-file numstat |
| `blame.json` | `surviving_lines: {sha: lines at HEAD}` via `git blame -w` |
| `manifest.json` | HEAD sha, timestamp, limits used |

## Report back

State which limits were hit (count == limit in the stderr summary) — capped windows mean metrics describe the recent period only.

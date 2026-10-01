#!/usr/bin/env python3
"""Compute human-vs-agent contribution metrics from collect.py output.

Usage: python3 metrics.py runs/<owner>__<repo>

Writes metrics.json next to the evidence and prints it. Stdlib only.
Classification is heuristic: "unmarked" means no agent signal was found,
not that a human provably wrote it.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Human activity dominates the index by design.
WEIGHTS = {"discourse": 0.40, "oversight": 0.25, "authorship": 0.20, "stability": 0.15}
# Index points deducted at a 100% offending rate; scaled linearly by the share of sampled merged PRs.
PENALTIES = {"large_pr": 40, "unapproved_merge": 40}
LARGE_PR_LINES = 5000  # additions + deletions

BOT_LOGINS = re.compile(
    r"\[bot\]$|^(copilot|copilot-swe-agent|devin-ai-integration|coderabbitai|claude|codex|"
    r"chatgpt-codex-connector|github-actions|dependabot|renovate|google-labs-jules|"
    r"sweep-ai|cursor|gemini-code-assist|sourcery-ai|ellipsis-dev|greptile)",
    re.I,
)
BOT_IDENTITY = re.compile(
    r"\[bot\]|noreply@anthropic\.com|cursoragent@cursor\.com|\(aider\)|"
    r"198982749\+copilot@|devin-ai|github-actions|dependabot|renovate",
    re.I,
)
AGENT_MARKERS = re.compile(
    r"co-authored-by:[^\n]*(claude|anthropic|copilot|cursor|devin|codex|openai|chatgpt|gemini|"
    r"aider|jules|windsurf|codeium|amazon q|sweep|junie)|"
    r"generated with \[?claude|🤖 generated|^aider: |\(aider\)|"
    r"generated (by|with) (copilot|cursor|devin|codex|chatgpt|gemini|ai)",
    re.I | re.M,
)
# Excluded from line counts: lockfiles, vendored and generated output.
NOISE_PATH = re.compile(
    r"(^|/)(node_modules|vendor|dist|build|\.venv|__snapshots__)/|"
    r"(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|uv\.lock|Cargo\.lock|"
    r"Gemfile\.lock|go\.sum|composer\.lock)$|\.min\.(js|css)$|\.(svg|map|snap)$",
)
CLOSES_ISSUE = re.compile(r"\b(close[sd]?|fix(e[sd])?|resolve[sd]?)\s+#(\d+)", re.I)
STABLE_AGE = timedelta(days=30)


def load(run: Path, name: str):
    return json.loads((run / f"{name}.json").read_text())


def is_bot(user: dict | None) -> bool:
    return not user or user.get("type") == "Bot" or bool(BOT_LOGINS.search(user.get("login") or ""))


def words(text: str) -> int:
    # Drop quoted replies and code so pasted logs/diffs don't count as discourse.
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith(">"))
    return len(re.findall(r"\w+", text))


def ratio(num: float, den: float) -> float | None:
    return round(num / den, 4) if den else None


def classify_commit(c: dict) -> str:
    ident = " ".join((c["author_name"], c["author_email"], c["committer_name"], c["committer_email"]))
    if BOT_IDENTITY.search(ident):
        return "agent"
    return "agent" if AGENT_MARKERS.search(c["message"]) else "unmarked"


def text_class(user: dict | None, body: str) -> str:
    if is_bot(user):
        return "bot"
    return "agent_marked" if AGENT_MARKERS.search(body) else "human"


def authorship(commits: list[dict], blame: dict) -> tuple[dict, dict]:
    surviving = blame["surviving_lines"]
    cutoff = datetime.now(timezone.utc) - STABLE_AGE
    by_class: dict[str, Counter] = defaultdict(Counter)
    monthly: dict[str, Counter] = defaultdict(Counter)
    touches: Counter = Counter()
    authors: dict[str, set] = defaultdict(set)

    for c in commits:
        if len(c["parents"]) > 1:
            continue  # merges carry no numstat; their content is counted on the branch
        cls = classify_commit(c)
        added = sum(f["add"] or 0 for f in c["files"] if not NOISE_PATH.search(f["path"]))
        deleted = sum(f["del"] or 0 for f in c["files"] if not NOISE_PATH.search(f["path"]))
        b = by_class[cls]
        b["commits"] += 1
        b["lines_added"] += added
        b["lines_surviving"] += surviving.get(c["sha"], 0)
        if datetime.fromisoformat(c["authored_at"]) < cutoff:
            b["mature_added"] += added
            b["mature_surviving"] += surviving.get(c["sha"], 0)
        authors[cls].add(c["author_email"].lower())
        month = c["authored_at"][:7]
        monthly[month]["added"] += added
        monthly[month]["deleted"] += deleted
        monthly[month][f"{cls}_commits"] += 1
        for f in c["files"]:
            if not NOISE_PATH.search(f["path"]):
                touches[f["path"]] += 1

    total_added = sum(b["lines_added"] for b in by_class.values())
    total_surv = sum(b["lines_surviving"] for b in by_class.values())
    mature_added = sum(b["mature_added"] for b in by_class.values())
    mature_surv = sum(b["mature_surviving"] for b in by_class.values())
    head_lines = sum(surviving.values()) or 1

    code = {
        "classes": {
            cls: {
                "commits": b["commits"],
                "distinct_author_emails": len(authors[cls]),
                "lines_added": b["lines_added"],
                "share_of_lines_added": ratio(b["lines_added"], total_added),
                "lines_surviving_at_head": b["lines_surviving"],
                "share_of_head": ratio(b["lines_surviving"], head_lines),
                "survival_rate_mature": ratio(b["mature_surviving"], b["mature_added"]),
            }
            for cls, b in sorted(by_class.items())
        },
        "unmarked_share_of_head": ratio(by_class["unmarked"]["lines_surviving"], head_lines),
        "blame_files": blame["files_blamed"],
        "blame_truncated": blame["truncated"],
    }
    total_del = sum(m["deleted"] for m in monthly.values())
    stability = {
        "survival_rate_mature": ratio(mature_surv, mature_added),
        "survival_rate_all": ratio(total_surv, total_added),
        "churn_ratio": ratio(total_del, total_added),
        "hotspots": touches.most_common(10),
        "monthly": {m: dict(v) for m, v in sorted(monthly.items())},
    }
    return code, stability


def discourse(issues, issue_comments, review_comments, reviews, pulls) -> dict:
    items = []
    items += [("issue", i["user"], f"{i['title']}\n{i['body']}") for i in issues if not i["is_pr"]]
    items += [("pr_description", p["user"], p["body"]) for p in pulls]
    items += [("issue_comment", c["user"], c["body"]) for c in issue_comments]
    items += [("review_comment", c["user"], c["body"]) for c in review_comments]
    items += [("review", r["user"], r["body"]) for rs in reviews.values() for r in rs if r["body"]]

    per_kind: dict[str, Counter] = defaultdict(Counter)
    human_logins: set[str] = set()
    for kind, user, body in items:
        cls = text_class(user, body)
        n = words(body)
        per_kind[kind][f"{cls}_items"] += 1
        per_kind[kind][f"{cls}_words"] += n
        if cls == "human" and user:
            human_logins.add(user["login"])

    tot = Counter()
    for c in per_kind.values():
        tot.update(c)
    all_words = tot["human_words"] + tot["bot_words"] + tot["agent_marked_words"]
    human_issues = [i for i in issues if not i["is_pr"] and text_class(i["user"], i["body"]) == "human"]
    return {
        "human_word_share": ratio(tot["human_words"], all_words),
        "human_words": tot["human_words"],
        "distinct_human_participants": len(human_logins),
        "issues_total": sum(1 for i in issues if not i["is_pr"]),
        "issues_by_humans": len(human_issues),
        "open_issues_by_humans": sum(1 for i in human_issues if i["state"] == "open"),
        "by_kind": {k: dict(v) for k, v in sorted(per_kind.items())},
    }


def oversight(pulls, reviews, issue_comments, review_comments, issues) -> dict:
    comments_by_pr: dict[int, list] = defaultdict(list)
    for c in issue_comments + review_comments:
        comments_by_pr[c["number"]].append(c)
    human_issue_numbers = {
        i["number"] for i in issues if not i["is_pr"] and text_class(i["user"], i["body"]) == "human"
    }

    merged = [p for p in pulls if p["merged_at"] and str(p["number"]) in reviews]
    reviewed = changes_requested = agent_authored = issue_driven = 0
    large: list[dict] = []
    unapproved: list[dict] = []
    for p in merged:
        author = (p["user"] or {}).get("login")
        others = lambda u: u and u.get("login") != author and not is_bot(u)
        rs = reviews[str(p["number"])]
        human_touch = any(others(r["user"]) for r in rs) or any(
            others(c["user"]) and words(c["body"]) >= 3 for c in comments_by_pr[p["number"]]
        )
        reviewed += human_touch
        changes_requested += any(others(r["user"]) and r["state"] == "CHANGES_REQUESTED" for r in rs)
        agent_authored += text_class(p["user"], p["body"]) != "human"
        issue_driven += any(int(n) in human_issue_numbers for *_, n in CLOSES_ISSUE.findall(p["body"]))

        # Each reviewer's latest decisive review counts; a later CHANGES_REQUESTED or dismissal voids approval.
        verdicts = {r["user"]["login"]: r["state"] for r in rs
                    if others(r["user"]) and r["state"] in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED")}
        ref = {"number": p["number"], "title": p["title"], "author": author,
               "merged_by": (p.get("merged_by") or {}).get("login")}
        if "APPROVED" not in verdicts.values():
            unapproved.append(ref)
        size = (p.get("additions") or 0) + (p.get("deletions") or 0)
        if size > LARGE_PR_LINES:
            large.append({**ref, "lines": size, "changed_files": p.get("changed_files")})

    return {
        "merged_prs_sampled": len(merged),
        "human_review_coverage": ratio(reviewed, len(merged)),
        "changes_requested_rate": ratio(changes_requested, len(merged)),
        "agent_or_bot_authored_pr_rate": ratio(agent_authored, len(merged)),
        "human_issue_driven_pr_rate": ratio(issue_driven, len(merged)),
        "large_pr_rate": ratio(len(large), len(merged)),
        "unapproved_merge_rate": ratio(len(unapproved), len(merged)),
        "large_prs": sorted(large, key=lambda x: -x["lines"]),
        "unapproved_merges": unapproved,
        "prs_total_sampled": len(pulls),
    }


def index(disc, over, code, stab) -> dict:
    parts = {
        "discourse": disc["human_word_share"],
        "oversight": over["human_review_coverage"],
        "authorship": code["unmarked_share_of_head"],
        "stability": stab["survival_rate_mature"],
    }
    present = {k: v for k, v in parts.items() if v is not None}
    wsum = sum(WEIGHTS[k] for k in present)
    score = sum(WEIGHTS[k] * v for k, v in present.items()) / wsum if wsum else None
    rates = {"large_pr": over["large_pr_rate"], "unapproved_merge": over["unapproved_merge_rate"]}
    penalties = {k: round(PENALTIES[k] * r, 1) for k, r in rates.items() if r is not None}
    base = None if score is None else round(100 * score, 1)
    return {
        "human_contribution_index": None if base is None else max(0.0, round(base - sum(penalties.values()), 1)),
        "base_index": base,
        "penalties": penalties,
        "components": parts,
        "weights": WEIGHTS,
        "missing_components": sorted(set(parts) - set(present)),
    }


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    run = Path(sys.argv[1])
    pulls, issues = load(run, "pulls"), load(run, "issues")
    ic, rc, reviews = load(run, "issue_comments"), load(run, "review_comments"), load(run, "reviews")
    code, stab = authorship(load(run, "commits"), load(run, "blame"))
    disc = discourse(issues, ic, rc, reviews, pulls)
    over = oversight(pulls, reviews, ic, rc, issues)
    result = {
        "repo": load(run, "repo"),
        "manifest": load(run, "manifest"),
        "index": index(disc, over, code, stab),
        "discourse": disc,
        "oversight": over,
        "code": code,
        "stability": stab,
    }
    (run / "metrics.json").write_text(json.dumps(result, indent=1))
    print(json.dumps({k: result[k] for k in ("index", "discourse", "oversight", "code")}, indent=1))


if __name__ == "__main__":
    main()

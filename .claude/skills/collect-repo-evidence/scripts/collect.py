#!/usr/bin/env python3
"""Clone a GitHub repo and dump git + GitHub activity evidence as JSON.

Usage: python3 collect.py <repo-url-or-owner/repo> [--out runs] [--max-prs 300] ...

Writes runs/<owner>__<repo>/{repo,pulls,issues,issue_comments,review_comments,
reviews,commits,blame,manifest}.json and keeps the clone in runs/<owner>__<repo>/src.
Stdlib only; needs `git` and an authenticated `gh`.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

URL_RE = re.compile(
    r"^(?:https?://|git@|ssh://git@)?(?P<host>[^/:]+)[/:](?P<owner>[^/]+)/(?P<repo>[^/#?]+?)(?:\.git)?/?(?:[#?].*)?$"
)
SHA_HEADER = re.compile(r"^([0-9a-f]{40}) \d+ \d+")


def parse_repo(arg: str) -> tuple[str, str, str]:
    if re.fullmatch(r"[\w.-]+/[\w.-]+", arg):
        owner, repo = arg.split("/")
        return "github.com", owner, repo
    m = URL_RE.match(arg.strip())
    if not m:
        sys.exit(f"cannot parse repo reference: {arg}")
    return m["host"], m["owner"], m["repo"]


def run(cmd: list[str], cwd: Path | None = None) -> str:
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, errors="replace")
    if res.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed: {res.stderr.strip()}")
    return res.stdout


def gh_api(host: str, endpoint: str, **params) -> object:
    cmd = ["gh", "api", "--hostname", host, "-X", "GET", endpoint]
    for k, v in params.items():
        cmd += ["-f", f"{k}={v}"]
    return json.loads(run(cmd) or "null")


def gh_pages(host: str, endpoint: str, limit: int, **params) -> list[dict]:
    items: list[dict] = []
    page = 1
    while len(items) < limit:
        batch = gh_api(host, endpoint, per_page=100, page=page, **params)
        if not batch:
            break
        items.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return items[:limit]


def actor(u: dict | None) -> dict | None:
    return u and {"login": u.get("login"), "type": u.get("type")}


def slim_pr(p: dict) -> dict:
    return {
        "number": p["number"], "title": p["title"], "body": p.get("body") or "",
        "user": actor(p.get("user")), "state": p["state"], "draft": p.get("draft"),
        "created_at": p["created_at"], "merged_at": p.get("merged_at"),
        "closed_at": p.get("closed_at"), "author_association": p.get("author_association"),
    }


def slim_issue(i: dict) -> dict:
    return {
        "number": i["number"], "title": i["title"], "body": i.get("body") or "",
        "user": actor(i.get("user")), "state": i["state"], "comments": i.get("comments", 0),
        "created_at": i["created_at"], "closed_at": i.get("closed_at"),
        "is_pr": "pull_request" in i, "author_association": i.get("author_association"),
        "labels": [l["name"] for l in i.get("labels", [])],
    }


def slim_comment(c: dict) -> dict:
    url = c.get("issue_url") or c.get("pull_request_url") or ""
    return {
        "id": c["id"], "number": int(url.rsplit("/", 1)[-1]) if url else None,
        "user": actor(c.get("user")), "body": c.get("body") or "",
        "created_at": c["created_at"], "author_association": c.get("author_association"),
        "path": c.get("path"),
    }


def slim_review(r: dict) -> dict:
    return {
        "id": r["id"], "user": actor(r.get("user")), "state": r["state"],
        "body": r.get("body") or "", "submitted_at": r.get("submitted_at"),
        "author_association": r.get("author_association"),
    }


def clone(host: str, owner: str, repo: str, dest: Path) -> None:
    if (dest / ".git").exists():
        run(["git", "fetch", "--prune", "origin"], cwd=dest)
        run(["git", "reset", "--hard", "origin/HEAD"], cwd=dest)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        run(["gh", "repo", "clone", f"{host}/{owner}/{repo}", str(dest)])


def git_commits(src: Path) -> list[dict]:
    fmt = "%x1e%H%x1f%P%x1f%an%x1f%ae%x1f%cn%x1f%ce%x1f%aI%x1f%cI%x1f%B%x1f"
    raw = run(["git", "log", "--numstat", "--no-renames", f"--format={fmt}", "HEAD"], cwd=src)
    commits = []
    for rec in raw.split("\x1e")[1:]:
        sha, parents, an, ae, cn, ce, ad, cd, msg, stats = rec.split("\x1f")
        files = []
        for line in stats.strip().splitlines():
            add, dele, path = line.split("\t", 2)
            files.append({"path": path, "add": None if add == "-" else int(add),
                          "del": None if dele == "-" else int(dele)})
        commits.append({
            "sha": sha, "parents": parents.split(), "author_name": an, "author_email": ae,
            "committer_name": cn, "committer_email": ce, "authored_at": ad,
            "committed_at": cd, "message": msg.strip(), "files": files,
        })
    return commits


def blame_file(src: Path, path: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    try:
        out = run(["git", "blame", "-w", "--porcelain", "HEAD", "--", path], cwd=src)
    except RuntimeError:
        return counts
    for line in out.splitlines():
        m = SHA_HEADER.match(line)
        if m:
            counts[m[1]] = counts.get(m[1], 0) + 1
    return counts


def git_blame(src: Path, max_files: int, max_bytes: int) -> dict:
    # `git grep -I -l ''` lists tracked non-binary, non-empty files.
    files = run(["git", "grep", "-I", "-l", "", "HEAD"], cwd=src).splitlines()
    files = [f.split(":", 1)[1] for f in files]
    files = [f for f in files if (src / f).is_file() and (src / f).stat().st_size <= max_bytes]
    truncated = len(files) > max_files
    files = files[:max_files]
    totals: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for counts in pool.map(lambda f: blame_file(src, f), files):
            for sha, n in counts.items():
                totals[sha] = totals.get(sha, 0) + n
    return {"files_blamed": len(files), "truncated": truncated, "surviving_lines": totals}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo")
    ap.add_argument("--out", default="runs")
    ap.add_argument("--max-prs", type=int, default=300)
    ap.add_argument("--max-issues", type=int, default=500)
    ap.add_argument("--max-comments", type=int, default=3000)
    ap.add_argument("--max-review-prs", type=int, default=150, help="most recent merged PRs to fetch reviews for")
    ap.add_argument("--max-blame-files", type=int, default=3000)
    ap.add_argument("--max-blame-bytes", type=int, default=512_000)
    args = ap.parse_args()

    host, owner, repo = parse_repo(args.repo)
    out = Path(args.out) / f"{owner}__{repo}"
    src = out / "src"
    base = f"repos/{owner}/{repo}"

    def dump(name: str, data: object) -> None:
        (out / f"{name}.json").write_text(json.dumps(data, indent=1))
        n = len(data) if isinstance(data, (list, dict)) else 1
        print(f"  {name}.json ({n})", file=sys.stderr)

    print(f"cloning {host}/{owner}/{repo} -> {src}", file=sys.stderr)
    clone(host, owner, repo, src)

    meta = gh_api(host, base)
    dump("repo", {k: meta.get(k) for k in (
        "full_name", "description", "private", "fork", "default_branch", "created_at",
        "pushed_at", "stargazers_count", "forks_count", "open_issues_count", "language")})

    pulls = [slim_pr(p) for p in gh_pages(host, f"{base}/pulls", args.max_prs,
                                          state="all", sort="created", direction="desc")]
    issues = [slim_issue(i) for i in gh_pages(host, f"{base}/issues", args.max_issues,
                                              state="all", sort="created", direction="desc")]
    dump("issues", issues)
    dump("issue_comments", [slim_comment(c) for c in gh_pages(
        host, f"{base}/issues/comments", args.max_comments, sort="created", direction="desc")])
    dump("review_comments", [slim_comment(c) for c in gh_pages(
        host, f"{base}/pulls/comments", args.max_comments, sort="created", direction="desc")])

    # The list endpoint has no size or merger; fetch both per merged PR alongside its reviews.
    def merged_pr_evidence(p: dict) -> list[dict]:
        d = gh_api(host, f"{base}/pulls/{p['number']}")
        p.update(additions=d["additions"], deletions=d["deletions"],
                 changed_files=d["changed_files"], merged_by=actor(d.get("merged_by")))
        return [slim_review(r) for r in gh_pages(host, f"{base}/pulls/{p['number']}/reviews", 500)]

    review_prs = [p for p in pulls if p["merged_at"]][: args.max_review_prs]
    with ThreadPoolExecutor(max_workers=6) as pool:
        fetched = list(pool.map(merged_pr_evidence, review_prs))
    dump("reviews", {str(p["number"]): r for p, r in zip(review_prs, fetched)})
    dump("pulls", pulls)

    print("reading git history", file=sys.stderr)
    dump("commits", git_commits(src))
    print("blaming HEAD (line survival)", file=sys.stderr)
    dump("blame", git_blame(src, args.max_blame_files, args.max_blame_bytes))

    dump("manifest", {
        "host": host, "owner": owner, "repo": repo,
        "head": run(["git", "rev-parse", "HEAD"], cwd=src).strip(),
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "limits": {k: v for k, v in vars(args).items() if k.startswith("max_")},
    })
    print(out)


if __name__ == "__main__":
    main()

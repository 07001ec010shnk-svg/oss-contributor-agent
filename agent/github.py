from __future__ import annotations
import base64
import datetime as dt
import re
import statistics
import time
from urllib.parse import quote
import requests
from .config import LiveConfig, settings
from . import db

API = "https://api.github.com"
NAME = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

class GitHubError(RuntimeError):
    pass

class GitHub:
    def __init__(self, live: LiveConfig):
        self.live = live

    def request(self, method, path, *, params=None, payload=None, ok=(200, 201, 202, 204)):
        token = self.live.snapshot().get("GITHUB_TOKEN", "").strip()
        if not token:
            raise GitHubError("Set GITHUB_TOKEN in api_keys.env")
        if not path.startswith("/"):
            raise ValueError("GitHub API path must start with /")
        try:
            r = requests.request(method, API+path, params=params, json=payload,
                headers={"Authorization": f"Bearer {token}",
                         "Accept": "application/vnd.github+json",
                         "X-GitHub-Api-Version": "2022-11-28",
                         "User-Agent": "OSS-Contributor-Agent"}, timeout=35)
            db.usage("github", "REST", 0, 0, r.status_code in ok, r.status_code if r.status_code not in ok else "")
            remaining = r.headers.get("X-RateLimit-Remaining", "unknown")
            db.execute("INSERT INTO api_state(name,value,updated) VALUES('github_remaining',?,?) "
                       "ON CONFLICT(name) DO UPDATE SET value=excluded.value,updated=excluded.updated",
                       (remaining, time.time()))
            if r.status_code not in ok:
                msg = r.json().get("message", "") if "json" in r.headers.get("content-type", "") else ""
                raise GitHubError(f"GitHub HTTP {r.status_code}: {msg[:180]}")
            return r.json() if r.content else {}
        except requests.RequestException as e:
            # Token is in a header, never a URL. Avoid logging remote response bodies.
            raise GitHubError(f"GitHub network error: {type(e).__name__}") from None

    def me(self):
        return self.request("GET", "/user")["login"]

    def token_info(self):
        """(login, token_kind) for the doctor command. Classic tokens report their scopes."""
        token = self.live.snapshot().get("GITHUB_TOKEN", "").strip()
        if not token:
            raise GitHubError("Set GITHUB_TOKEN in api_keys.env")
        try:
            r = requests.get(API+"/user", timeout=30, headers={
                "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                "User-Agent": "OSS-Contributor-Agent"})
        except requests.RequestException as e:
            raise GitHubError(f"GitHub network error: {type(e).__name__}") from None
        if r.status_code != 200:
            raise GitHubError(f"GitHub rejected the token (HTTP {r.status_code})")
        scopes = r.headers.get("X-OAuth-Scopes")
        kind = ("classic token, permissions: " + (scopes or "none")) if scopes is not None else "fine-grained token"
        return r.json()["login"], kind

    def repo(self, full_name):
        if not NAME.fullmatch(full_name):
            raise ValueError("Invalid repository name")
        return self.request("GET", f"/repos/{full_name}")

    def recent_closed_prs(self, full_name, days=90):
        items = self.request("GET", f"/repos/{full_name}/pulls",
                             params={"state":"closed", "sort":"updated", "direction":"desc", "per_page":100})
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
        return [x for x in items if dt.datetime.fromisoformat(x["updated_at"].replace("Z", "+00:00")) >= cutoff]

    def scan(self):
        cfg = settings()
        my_login = self.me().lower()
        results = []
        seen = set()
        per_language = max(1, int(cfg["repos_per_scan"])//max(1,len(cfg["languages"])))
        pushed = (dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=30)).date().isoformat()
        for language in cfg["languages"]:
            q = f'language:{language} stars:>={cfg["min_stars"]} archived:false fork:false pushed:>={pushed}'
            page = self.request("GET", "/search/repositories", params={"q":q,"sort":"updated", "per_page":min(100,per_language)})
            for item in page.get("items", []):
                full = item["full_name"]
                if full in seen or item["owner"]["login"].lower() == my_login:
                    continue
                if item.get("size", 0) > 300_000:   # size is in KB; skip repos over ~300 MB (clones time out)
                    continue
                seen.add(full)
                try:
                    prs = self.recent_closed_prs(full)
                    merged = [p for p in prs if p.get("merged_at")]
                    external = [p for p in merged if p.get("author_association") not in ("OWNER","MEMBER","COLLABORATOR")
                                and p.get("user",{}).get("login","").lower() != item["owner"]["login"].lower()]
                    ages = [(dt.datetime.fromisoformat(p["merged_at"].replace("Z","+00:00"))-
                             dt.datetime.fromisoformat(p["created_at"].replace("Z","+00:00"))).total_seconds()/86400
                            for p in external]
                    median = round(statistics.median(ages),1) if ages else None
                    recent = sum(1 for p in external if dt.datetime.fromisoformat(p["merged_at"].replace("Z","+00:00")) >=
                                 dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=30))
                    # Heuristic, not a true acceptance probability. Closed PR sample is capped at 100.
                    proportion = len(external) / max(1,len(prs))
                    score = round(min(40, recent*8) + min(30, proportion*60) +
                                  (20 if median is not None and median<=14 else 8 if median is not None and median<=30 else 0) +
                                  (10 if len(merged)>=5 else 0), 1)
                    record = dict(full_name=full,url=item["html_url"],language=item.get("language") or language,
                                  score=score,external_merged=len(external),closed_sample=len(prs),
                                  median_merge_days=median,merged_30d=recent,
                                  last_push=item.get("pushed_at"),updated=time.time())
                    db.store_repo(record)
                    results.append(record)
                except (GitHubError, KeyError, ValueError) as e:
                    print(f"Skipped {full}: {e}")
        return sorted(results, key=lambda r:r["score"], reverse=True)

    def list_issues(self, full_name, limit=8):
        issues = self.request("GET", f"/repos/{full_name}/issues",
                              params={"state":"open", "per_page":min(100, max(limit*4,20)), "sort":"updated"})
        # A GitHub PR is also returned by the Issues endpoint; filter it out.
        candidate = [x for x in issues if "pull_request" not in x and not x.get("assignee") and not x.get("locked")]
        def rank(x):
            labels = " ".join(label["name"].lower() for label in x.get("labels",[]))
            return (int("good first issue" in labels or "help wanted" in labels)*3 +
                    int("bug" in labels)*2 + int("fix" in x.get("title","").lower()))
        picked = []
        for issue in sorted(candidate, key=rank, reverse=True):
            if len(picked) >= limit:
                break
            if self.has_linked_pr(full_name, issue["number"]):
                continue  # somebody already opened a PR for it; a duplicate annoys maintainers
            picked.append(issue)
        return picked

    def has_linked_pr(self, full_name, number):
        """True if an open or merged pull request already mentions this issue."""
        try:
            events = self.request("GET", f"/repos/{full_name}/issues/{number}/timeline",
                                  params={"per_page": 100})
        except GitHubError:
            return False  # cannot tell; keep the issue, the human review catches it
        for ev in events if isinstance(events, list) else []:
            if ev.get("event") != "cross-referenced":
                continue
            src = (ev.get("source") or {}).get("issue") or {}
            pr = src.get("pull_request")
            if pr is not None and (src.get("state") == "open" or pr.get("merged_at")):
                return True
        return False

    def discover_issues(self):
        cfg = settings()
        repos = db.rows("SELECT * FROM repos WHERE policy='allowed' ORDER BY score DESC")
        total = 0
        for repo in repos:
            try:
                for issue in self.list_issues(repo["full_name"],cfg["issues_per_repo"]):
                    db.store_issue(dict(id=issue["id"],repo=repo["full_name"],number=issue["number"],
                                       title=issue["title"],body=(issue.get("body") or "")[:15000],
                                       url=issue["html_url"],updated=time.time()))
                    total+=1
            except GitHubError as e:
                print(f"Issue discovery failed for {repo['full_name']}: {e}")
        return total

    def repository_policy(self, full_name):
        snippets=[]
        for path in ("CONTRIBUTING.md",".github/CONTRIBUTING.md","docs/CONTRIBUTING.md","CONTRIBUTING.rst",
                     "AI_POLICY.md",".github/AI_POLICY.md","AGENTS.md",".github/PULL_REQUEST_TEMPLATE.md"):
            try:
                doc = self.request("GET",f"/repos/{full_name}/contents/{path}")
                if doc.get("encoding")=="base64":
                    snippets.append(f"## {path}\n"+base64.b64decode(doc["content"]).decode("utf-8","replace")[:10000])
            except GitHubError:
                continue
        return "\n\n".join(snippets) or "No top-level contribution file found. Manually inspect repository website and policies."

    def ensure_fork(self, base_name, user):
        repo_name = base_name.split("/")[1]
        fork_name = f"{user}/{repo_name}"
        try:
            existing = self.repo(fork_name)
            if not existing.get("fork") or existing.get("parent",{}).get("full_name","").lower()!=base_name.lower():
                raise GitHubError(f"{fork_name} exists but is not a fork of {base_name}")
            return fork_name
        except GitHubError as e:
            if "HTTP 404" not in str(e):
                raise
        try:
            self.request("POST", f"/repos/{base_name}/forks", payload={})
        except GitHubError as e:
            raise GitHubError(f"Cannot create fork with this PAT. Create the fork in GitHub UI and retry. {e}") from None
        for _ in range(12):
            time.sleep(3)
            try:
                self.repo(fork_name)
                return fork_name
            except GitHubError:
                continue
        raise GitHubError("Fork is still being created; retry later")

    def read_file(self, fork, path, ref):
        try:
            return self.request("GET", f"/repos/{fork}/contents/{quote(path, safe='/')}", params={"ref":ref})
        except GitHubError as e:
            if "HTTP 404" in str(e):
                return None
            raise

    def sync_fork(self, fork, branch):
        """Ask GitHub to bring the fork up to date with upstream (replaces the manual 'Sync fork' click)."""
        try:
            self.request("POST", f"/repos/{fork}/merge-upstream", payload={"branch": branch})
        except GitHubError as e:
            raise GitHubError(f"Could not sync your fork automatically ({e}). Click 'Sync fork' on GitHub and retry.") from None

    def publish(self, issue, paths, expected=None):
        """Explicitly invoked only for approved issues. One PR per command.

        expected maps path -> git blob sha the file had when the patch was written
        (None for a brand new file). If upstream changed the file since, we stop,
        because uploading our whole copy would silently undo their change."""
        full = issue["repo"]
        base = self.repo(full)
        branch = issue["branch"]
        user = self.me()
        fork = self.ensure_fork(full, user)
        fork_info = self.repo(fork)
        base_branch = base["default_branch"]
        fork_default = fork_info["default_branch"]
        base_sha = self.request("GET", f"/repos/{full}/git/ref/heads/{quote(base_branch)}")["object"]["sha"]
        fork_sha = self.request("GET", f"/repos/{fork}/git/ref/heads/{quote(fork_default)}")["object"]["sha"]
        if fork_sha != base_sha:
            self.sync_fork(fork, fork_default)
            fork_sha = self.request("GET", f"/repos/{fork}/git/ref/heads/{quote(fork_default)}")["object"]["sha"]
            if fork_sha != base_sha:
                raise GitHubError("Fork is still behind upstream. Click 'Sync fork' on GitHub and retry.")
        for path, blob in (expected or {}).items():
            upstream = self.read_file(full, path, base_branch)
            upstream_blob = upstream.get("sha") if upstream else None
            if upstream_blob != blob:
                raise GitHubError(f"Upstream changed {path} since the fix was written. "
                                  "Re-create the fix (delete its workspaces folder and run again) so nothing gets undone.")
        # Avoid re-creating only when the exact branch already exists.
        try:
            existing_ref=self.request("GET", f"/repos/{fork}/git/ref/heads/{quote(branch)}")
            if existing_ref["object"]["sha"] != fork_sha:
                # An earlier publication may have partially committed files; inspect manually.
                raise GitHubError("Existing fork branch diverged from base. Inspect the branch before retrying.")
        except GitHubError as e:
            if "HTTP 404" not in str(e):
                raise
            self.request("POST", f"/repos/{fork}/git/refs", payload={"ref":f"refs/heads/{branch}","sha":fork_sha})
        from pathlib import Path
        work = Path(issue["workdir"]).resolve()
        for path in paths:
            current = self.read_file(fork,path,branch)
            content = (work/path).read_bytes()
            payload = {"message":f"fix: address {full}#{issue['number']} ({path})",
                       "content":base64.b64encode(content).decode("ascii"),"branch":branch}
            if current and current.get("sha"):
                payload["sha"]=current["sha"]
            self.request("PUT", f"/repos/{fork}/contents/{quote(path,safe='/')}", payload=payload)
        title = f"fix: {issue['title'][:90]}"
        body = (f"Closes #{issue['number']}\n\n"
                f"Proposed fix prepared with AI assistance and reviewed by the submitting contributor.\n\n"
                f"Changed files: {', '.join(paths)}\n\n"
                f"Test result: {issue['test_result'][:1500]}\n\n"
                "Please review correctness and whether this follows project contribution requirements.")
        try:
            pr = self.request("POST", f"/repos/{full}/pulls", payload={
                "title":title,"head":f"{user}:{branch}","base":base_branch,"body":body,
                "maintainer_can_modify":True})
        except GitHubError as e:
            compare_url=f"https://github.com/{full}/compare/{quote(base_branch)}...{quote(user)}:{quote(branch)}?expand=1"
            raise GitHubError(f"Branch uploaded but PR API failed ({e}). Create the PR manually: {compare_url}") from None
        return pr

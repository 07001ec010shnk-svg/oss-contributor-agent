"""One-click cleanup: run  python reset.py

For every issue that failed or whose pull request was closed it will:
  1. close the pull request on GitHub if it is still open
  2. delete the old branch from YOUR fork on GitHub
  3. delete the local workspace folder
  4. mark the issue so the agent can try it again
"""
import os
import shutil
import stat
from pathlib import Path

from agent import db
from agent.config import LiveConfig
from agent.github import GitHub, GitHubError

db.init()
gh = GitHub(LiveConfig())


def force(func, path, _):
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


try:
    me = gh.me()
except GitHubError as e:
    print('Cannot reach GitHub:', e)
    me = None

rows = db.rows("SELECT * FROM issues WHERE state IN ('failed','closed')")
if not rows:
    print('Nothing to reset.')

for r in rows:
    label = f"{r['repo']}#{r['number']}"

    # 1 + 2: clean up GitHub (only if a pull request was ever sent)
    if me and r.get('pr_number'):
        try:
            pr = gh.request('GET', f"/repos/{r['repo']}/pulls/{r['pr_number']}")
            if pr.get('state') == 'open':
                gh.request('PATCH', f"/repos/{r['repo']}/pulls/{r['pr_number']}", payload={'state': 'closed'})
                print(f'  closed pull request for {label}')
        except GitHubError as e:
            print(f'  could not check/close pull request for {label}: {e}')
        if r.get('branch'):
            fork = f"{me}/{r['repo'].split('/')[1]}"
            try:
                gh.request('DELETE', f"/repos/{fork}/git/refs/heads/{r['branch']}")
                print(f"  deleted old branch {r['branch']} from your fork")
            except GitHubError as e:
                print(f"  old branch already gone or not deletable ({e})")

    # 3: local folder
    shutil.rmtree(Path('workspaces') / str(r['id']), onerror=force)
    if (Path('workspaces') / str(r['id'])).exists():
        print(f"STILL LOCKED, close VS Code / Explorer windows using it: workspaces\\{r['id']}")
        continue

    # 4: let the agent try again
    db.execute("UPDATE issues SET state='analyzed', pr_number=NULL, pr_url=NULL, submitted_at=NULL, "
               "workdir='', diff_path='', branch='', last_error='' WHERE id=?", (r['id'],))
    print(f'reset {label}')

print('done')

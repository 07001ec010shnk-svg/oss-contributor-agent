"""Plain-language helpers: turns raw numbers into words and keeps the daily flow short."""
from __future__ import annotations
import time
from . import db
from .config import settings

def rating(repo: dict):
    """Turn the repo numbers into one word plus a sentence anyone can read."""
    recent = repo.get('merged_30d') or 0
    median = repo.get('median_merge_days')
    total = repo.get('external_merged') or 0
    wait = f", usually reviewed in about {median:g} days" if median is not None else ''
    # Nearly 100 outside PRs merged almost instantly is a sign of an auto-merge / PR-farming repo.
    # Contributing there would not be real, reviewed work, so never recommend it.
    if recent >= 60 and median is not None and median <= 0.5:
        return 'SKIP', 'Looks automated: too many outside PRs merged instantly, not real review'
    if recent >= 3 and median is not None and median <= 14:
        word = 'GREAT'
    elif recent >= 1 and (median is None or median <= 30):
        word = 'GOOD'
    elif total > 0:
        word = 'SLOW'
    else:
        word = 'SKIP'
    if word == 'SKIP':
        return word, 'No outside contributor had a PR merged recently'
    return word, f"{recent} outside PR(s) merged in the last 30 days{wait}"

def bar(done: int, goal: int, width: int = 20) -> str:
    goal = max(1, goal)
    filled = min(width, round(width * done / goal))
    return '[' + '#' * filled + '-' * (width - filled) + f'] {done}/{goal}'

def counts() -> dict:
    states = {r['state']: r['n'] for r in db.rows('SELECT state,COUNT(*) AS n FROM issues GROUP BY state')}
    return {
        'merged': states.get('merged', 0),
        'waiting': states.get('submitted', 0),
        'closed': states.get('closed', 0),
        'ready': states.get('prepared', 0),
        'working': states.get('analyzed', 0) + states.get('discovered', 0),
        'dropped': states.get('rejected', 0) + states.get('failed', 0) + states.get('test_failed', 0),
    }

def report() -> str:
    cfg = settings()
    c = counts()
    goal = int(cfg.get('goal_merged_prs', 100))
    sent = c['merged'] + c['waiting'] + c['closed']
    day = db.one('SELECT COUNT(*) AS n FROM issues WHERE submitted_at>=?', (time.time() - 86400,))['n']
    cap = int(cfg.get('max_prs_per_day', 3))
    ai_today = db.one("SELECT COUNT(*) AS n FROM usage WHERE provider NOT IN ('github') AND ts>=?",
                      (time.time() - 86400,))['n']
    failed_ai = db.one("SELECT COUNT(*) AS n FROM usage WHERE provider NOT IN ('github') AND success=0 AND ts>=?",
                       (time.time() - 86400,))['n']
    good = db.one("SELECT COUNT(*) AS n FROM repos WHERE policy='allowed'")['n']
    lines = [
        '',
        '=== YOUR PROGRESS ===',
        f"Pull requests sent:        {sent}",
        f"  merged (accepted):       {c['merged']}",
        f"  waiting for an answer:   {c['waiting']}",
        f"  closed without merge:    {c['closed']}",
        f"Fixes ready for you:       {c['ready']}   <- run 'python main.py go' to review them",
        f"Projects you approved:     {good}",
        '',
        'Goal (Claude for Open Source, "100 merged PRs in 12 months" route):',
        '  ' + bar(c['merged'], goal),
        f"  Needed pace: about {goal / 52:.1f} merged PRs per week. Easy-looking or automated PRs may not count; quality matters.",
        '',
        f"Sent in the last 24h: {day} (daily limit {cap})",
        f"AI calls in the last 24h: {ai_today}" + (f" ({failed_ai} failed - check your keys/limits)" if failed_ai else ''),
    ]
    return '\n'.join(lines)

def ask(question: str, options: str = 'y/n', default: str | None = None) -> str:
    """Ask a question in the console; returns the lowercase answer."""
    while True:
        try:
            answer = input(f"{question} [{options}] ").strip().lower()
        except EOFError:
            return default or 'n'
        if not answer and default:
            return default
        if answer:
            return answer

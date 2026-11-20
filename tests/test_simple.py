import json
import time
import pytest
from agent import db, simple
from agent.github import GitHub, GitHubError
from agent.workflow import Agent, WorkflowError, safe_path, ai_mention_lines, ban_words, apply_changes


@pytest.fixture
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    db.init()
    return tmp_path


def repo(**kw):
    base = dict(full_name='o/r', url='u', language='Python', score=70, external_merged=5,
                closed_sample=20, median_merge_days=3.0, merged_30d=4, last_push='x', updated=time.time())
    base.update(kw)
    return base


def test_rating_words():
    assert simple.rating(repo())[0] == 'GREAT'
    assert simple.rating(repo(merged_30d=1, median_merge_days=20))[0] == 'GOOD'
    assert simple.rating(repo(merged_30d=0, external_merged=2))[0] == 'SLOW'
    assert simple.rating(repo(merged_30d=0, external_merged=0, median_merge_days=None))[0] == 'SKIP'
    assert 'days' in simple.rating(repo())[1]


def test_progress_bar():
    assert simple.bar(0, 100).endswith('0/100')
    assert simple.bar(50, 100).count('#') == 10


def test_build_files_blocked(tmp_path):
    for name in ('package.json', 'setup.py', 'requirements-dev.txt', 'pyproject.toml', 'Dockerfile', 'sub/Makefile'):
        with pytest.raises(WorkflowError):
            safe_path(tmp_path, name)
    (tmp_path / 'app.py').write_text('x=1\n')
    assert safe_path(tmp_path, 'app.py')


def test_policy_keyword_helpers():
    text = "Welcome!\nWe do not accept AI-generated pull requests.\nRun make test."
    assert ban_words(text)
    assert ai_mention_lines(text) == ['We do not accept AI-generated pull requests.']
    assert not ban_words('Please do not commit generated files.')
    assert ai_mention_lines('Plain rules only.') == []


class FakeRouter:
    def __init__(self, result=None, fail=False):
        self.result, self.fail = result, fail
    def generate(self, prompt, task='analysis'):
        if self.fail:
            raise RuntimeError('down')
        return self.result, 'fake/model'


class FakeGH:
    def __init__(self, text): self.text = text
    def repository_policy(self, name): return self.text


def test_screen_policy_paths():
    assert Agent(FakeGH('No top-level contribution file found. x'), FakeRouter()).screen_policy('o/r')[0] == 'no_docs'
    assert Agent(FakeGH('Be nice.'), FakeRouter()).screen_policy('o/r')[0] == 'no_rules'
    ai = Agent(FakeGH('We welcome AI assisted PRs.'), FakeRouter({'ai_prs': 'allowed', 'reason': 'ok'}))
    assert ai.screen_policy('o/r')[0] == 'allowed'
    down = Agent(FakeGH('No AI generated code is accepted. Do not submit AI-generated PRs.'), FakeRouter(fail=True))
    assert down.screen_policy('o/r')[0] == 'banned'


def test_review_patch_never_crashes():
    issue = dict(number=1, repo='o/r', title='t', body='b')
    assert Agent(None, FakeRouter(fail=True)).review_patch(issue, 'diff')['verdict'] == 'unchecked'
    assert Agent(None, FakeRouter({'verdict': 'bad', 'reason': 'nope'})).review_patch(issue, 'diff')['verdict'] == 'bad'
    assert Agent(None, FakeRouter({'verdict': 'weird'})).review_patch(issue, 'diff')['verdict'] == 'unchecked'


def test_report_on_empty_and_filled_db(tmpdb):
    assert 'YOUR PROGRESS' in simple.report()
    db.store_issue(dict(id=1, repo='o/r', number=1, title='t', body='', url='u', updated=time.time()))
    db.update_issue(1, state='merged')
    assert '0/100' not in simple.report() and '1/100' in simple.report()


class RecordingGH(GitHub):
    """GitHub client with a scripted fake of the web API."""
    def __init__(self, upstream_blob='AAA', fork_behind=True):
        self.calls = []
        self.upstream_blob = upstream_blob
        self.synced = not fork_behind
    def me(self): return 'me'
    def repo(self, name):
        return {'default_branch': 'main', 'fork': True, 'parent': {'full_name': 'o/r'}}
    def ensure_fork(self, base, user): return 'me/r'
    def request(self, method, path, **kw):
        self.calls.append((method, path))
        if path.startswith('/repos/o/r/git/ref/heads/main'): return {'object': {'sha': 'S1'}}
        if path.startswith('/repos/me/r/git/ref/heads/main'): return {'object': {'sha': 'S1' if self.synced else 'S0'}}
        if path == '/repos/me/r/merge-upstream': self.synced = True; return {}
        if path.startswith('/repos/me/r/git/ref/heads/oss'): raise GitHubError('GitHub HTTP 404: nf')
        if path == '/repos/me/r/git/refs': return {}
        if path.startswith('/repos/o/r/contents/'): return {'sha': self.upstream_blob}
        if path.startswith('/repos/me/r/contents/'):
            if method == 'GET': raise GitHubError('GitHub HTTP 404: nf')
            return {}
        if path == '/repos/o/r/pulls': return {'number': 9, 'html_url': 'https://x/pull/9'}
        raise AssertionError(path)


def issue_for(tmp_path):
    (tmp_path / 'a.py').write_text('x=2\n')
    return dict(repo='o/r', number=3, title='t', branch='oss-issue-3-1', workdir=str(tmp_path), test_result='')


def test_publish_syncs_fork_and_opens_pr(tmp_path):
    gh = RecordingGH('AAA')
    pr = gh.publish(issue_for(tmp_path), ['a.py'], {'a.py': 'AAA'})
    assert pr['number'] == 9
    assert ('POST', '/repos/me/r/merge-upstream') in gh.calls


def test_publish_refuses_when_upstream_file_changed(tmp_path):
    gh = RecordingGH('NEWER')
    with pytest.raises(GitHubError, match='Upstream changed'):
        gh.publish(issue_for(tmp_path), ['a.py'], {'a.py': 'AAA'})
    assert not any(c[1] == '/repos/o/r/pulls' for c in gh.calls)


def test_linked_pr_detection():
    class G(GitHub):
        def __init__(self, events): self.events = events
        def request(self, *a, **k): return self.events
    pr_open = {'event': 'cross-referenced', 'source': {'issue': {'state': 'open', 'pull_request': {}}}}
    pr_closed = {'event': 'cross-referenced', 'source': {'issue': {'state': 'closed', 'pull_request': {'merged_at': None}}}}
    plain = {'event': 'cross-referenced', 'source': {'issue': {'state': 'open'}}}
    assert G([pr_open]).has_linked_pr('o/r', 1)
    assert not G([pr_closed, plain]).has_linked_pr('o/r', 1)
    assert not G([]).has_linked_pr('o/r', 1)


def test_auto_merge_repo_is_skipped():
    assert simple.rating(repo(merged_30d=98, median_merge_days=0.0))[0] == 'SKIP'
    assert simple.rating(repo(merged_30d=83, median_merge_days=6.5))[0] == 'GREAT'

"""Entry point: py main.py            (same as: py main.py go)
All commands: py main.py --help"""
from __future__ import annotations
import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from agent.config import ROOT,LiveConfig,settings
from agent import db,simple
from agent.github import GitHub,GitHubError
from agent.providers import AIRouter
from agent.workflow import Agent,WorkflowError

for _stream in (sys.stdout,sys.stderr):      # Windows consoles often cannot print every character
    try: _stream.reconfigure(encoding='utf-8',errors='replace')
    except Exception: pass


def make_app():
    db.init()
    cfg=LiveConfig()
    gh=GitHub(cfg)
    return gh,Agent(gh,AIRouter(cfg))

def status(details=False):
    print(simple.report())
    if not details:
        print("\n(Want the technical tables? run: python main.py status --details)")
        return
    print('\nRepositories:')
    for r in db.rows('SELECT full_name,score,external_merged,merged_30d,policy FROM repos ORDER BY score DESC LIMIT 25'):
        print(f"  {r['full_name']:<43} score={r['score']:<5} external={r['external_merged']:<3} 30d={r['merged_30d']:<3} policy={r['policy']}")
    print('\nIssue states:')
    for r in db.rows('SELECT state,COUNT(*) AS n FROM issues GROUP BY state ORDER BY n DESC'):
        print(f"  {r['state']}: {r['n']}")
    print('\nAPI usage last 24h (locally observed):')
    for r in db.rows('''SELECT provider,model,COUNT(*) AS requests,
               SUM(request_tokens) AS input_tokens,SUM(response_tokens) AS output_tokens,
               SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) AS errors
               FROM usage WHERE ts>=? GROUP BY provider,model ORDER BY provider''',(time.time()-86400,)):
        print(f"  {r['provider']}/{r['model']}: {r['requests']} calls, {r['input_tokens']} in, {r['output_tokens']} out, {r['errors']} errors")

def doctor(gh,agent):
    """Check everything the program needs and say in plain words what is missing. Returns True if ready."""
    ok=True
    print('\n=== CHECKING YOUR SETUP ===')
    if shutil.which('git'): print('[OK]   Git is installed')
    else: print('[FIX]  Git is not installed. Get it from https://git-scm.com/download/win'); ok=False
    try:
        login,kind=gh.token_info()
        print(f'[OK]   GitHub token works (logged in as {login}; {kind})')
        if kind.startswith('fine-grained'):
            print('[NOTE] Fine-grained tokens often cannot open pull requests on other people\'s projects.\n'
                  '       If sending fails, create a classic token with the "public_repo" permission.')
    except GitHubError as e:
        print(f'[FIX]  GitHub: {e}. Put a valid GITHUB_TOKEN in api_keys.env'); ok=False
    env=agent.router.live.snapshot()
    providers=agent.router.configured(env)
    if not providers:
        print('[FIX]  No AI key found. Put at least one key (for example GEMINI_API_KEY) in api_keys.env'); ok=False
    else:
        try:
            _,used=agent.router.generate('Reply with this JSON only: {"ok": true}')
            print(f'[OK]   AI works (answered by {used})')
        except Exception as e:
            print(f'[FIX]  AI keys are set but the test call failed: {str(e)[:200]}'); ok=False
    if ok: print('Everything looks good.')
    return ok

def show_projects(limit=25):
    repos=db.rows('SELECT * FROM repos ORDER BY score DESC LIMIT ?',(limit,))
    if not repos: print('No projects yet. Run: python main.py go'); return
    print(f"{'PROJECT':<42} {'FIT':<6} {'YOUR DECISION':<12} WHY")
    for r in repos:
        word,why=simple.rating(r)
        decision={'allowed':'approved','blocked':'never','unreviewed':'not yet'}.get(r['policy'],r['policy'])
        print(f"{r['full_name']:<42} {word:<6} {decision:<12} {why}")

def review_projects(agent,max_new=5):
    """Walk through new projects one by one: read their AI rules, then you say yes/no."""
    cfg=settings()
    todo=[r for r in db.rows("SELECT * FROM repos WHERE policy='unreviewed' ORDER BY score DESC")
          if r['score']>=cfg['min_repo_score'] and simple.rating(r)[0] in ('GREAT','GOOD')][:max_new]
    if not todo:
        print('No new projects to review today.')
        return
    print(f'\n=== {len(todo)} PROJECT(S) TO REVIEW ===')
    for r in todo:
        word,why=simple.rating(r)
        print(f"\n{r['full_name']}  ->  {word}: {why}\n{r['url']}")
        verdict,note=agent.screen_policy(r['full_name'])
        db.execute('UPDATE repos SET policy_notes=? WHERE full_name=?',(f'{verdict}: {note}',r['full_name']))
        if verdict=='banned':
            db.execute("UPDATE repos SET policy='blocked' WHERE full_name=?",(r['full_name'],))
            print(f'  AI-written pull requests are NOT welcome here ({note}). Skipped for good.')
            continue
        label={'allowed':'AI-assisted PRs look allowed','no_rules':'No AI rules found','unclear':'AI rules are unclear',
               'no_docs':'No contribution document found'}[verdict]
        print(f'  Rules check: {label}. {note}')
        if verdict in ('unclear','no_docs'):
            print('  Please open the link above and read the contributing rules before saying yes.')
        answer=simple.ask('  Work on this project?  y = yes, n = never, s = skip for now','y/n/s',default='s')
        if answer.startswith('y'):
            db.execute("UPDATE repos SET policy='allowed' WHERE full_name=?",(r['full_name'],)); print('  Approved.')
        elif answer.startswith('n'):
            db.execute("UPDATE repos SET policy='blocked' WHERE full_name=?",(r['full_name'],)); print('  Blocked.')

def review_fixes(agent):
    """Show each ready fix in plain words and send it only when you type 'yes'."""
    ready=db.rows("SELECT * FROM issues WHERE state IN ('prepared','approved') ORDER BY fit_score DESC")
    if not ready:
        print('\nNo fixes are waiting for your review.')
        return
    print(f'\n=== {len(ready)} FIX(ES) READY FOR YOUR REVIEW ===')
    for issue in ready:
        print('\n'+'-'*70)
        print(f"{issue['repo']}  issue #{issue['number']}: {issue['title']}")
        print(f"Issue page: {issue['url']}")
        try: proposal=json.loads(Path(issue['workdir'],'proposal.json').read_text(encoding='utf-8'))
        except Exception: proposal={}
        if proposal.get('explanation'): print('What the AI did: '+str(proposal['explanation'])[:700])
        try: review=json.loads(issue.get('ai_review') or '{}')
        except ValueError: review={}
        if review: print(f"Second-opinion check: {str(review.get('verdict','?')).upper()} - {review.get('reason','')}")
        if issue['test_result'] and not issue['test_result'].startswith('Not run'):
            print('Tests: '+issue['test_result'].splitlines()[0])
        try: patch=Path(issue['diff_path']).read_text(encoding='utf-8')
        except OSError: print('The patch file is missing; skipping.'); continue
        print('\nTHE CHANGE:\n'+patch[:8000]+('\n... (cut; full file: '+issue['diff_path']+')' if len(patch)>8000 else ''))
        print('\nBefore saying yes: read the change, and check the issue page - some projects want you to comment\n'
              '"I would like to work on this" first.')
        answer=simple.ask("Type 'yes' to send this as a pull request, 'no' to throw it away, or press Enter to decide later",'yes/no/Enter',default='later')
        if answer=='yes':
            try:
                if issue['state']=='prepared': db.update_issue(issue['id'],state='approved')
                print('Sent! '+agent.submit(issue['id']))
            except (GitHubError,WorkflowError) as e:
                print(f'Not sent: {e}')
        elif answer=='no':
            db.update_issue(issue['id'],state='rejected'); print('Thrown away.')
        else: print('Okay, it will be here next time.')

def go(gh,agent):
    """The whole daily routine with simple questions. Nothing is sent unless you type 'yes'."""
    if not doctor(gh,agent):
        print('\nFix the items marked [FIX] above, then run this again.')
        return
    newest=db.one('SELECT MAX(updated) AS t FROM repos')['t']
    if not newest or time.time()-newest>7*86400:
        print('\nLooking for active, friendly projects (takes a minute)...')
        found=gh.scan()
        print(f'Found {len(found)} projects.')
    review_projects(agent)
    if not db.one("SELECT 1 AS x FROM repos WHERE policy='allowed'"):
        print('\nNo approved projects yet, so there is nothing to fix. Run this again tomorrow, or say y to a project.')
        print(simple.report()); return
    print('\nLooking for issues you can fix...')
    print(f'  {gh.discover_issues()} open issues found (ones that already have a pull request are skipped).')
    print(f"  {len(agent.analyze(15))} issues checked by the AI.")
    print(f"  {len(agent.fix(3))} fix(es) prepared.")
    watched=agent.watch()
    if watched: print(f'  Checked {len(watched)} pull requests you sent earlier.')
    review_fixes(agent)
    print(simple.report())

def cycle(gh,agent,limit=10,run_tests=False,scan=False):
    if scan:
        print('Scanning repositories...')
        for repo in gh.scan()[:15]:
            print(f"  {repo['full_name']}: {repo['score']}/100 (external merged 30d: {repo['merged_30d']})")
    count=gh.discover_issues()
    print(f'Found/upserted {count} issues in allowlisted repositories.')
    analyzed=agent.analyze(limit)
    print(f'Analyzed {len(analyzed)} issues.')
    fixed=agent.fix(min(limit,5),run_tests=run_tests)
    print(f'Prepared {len(fixed)} fixes. Review in dashboard or status; PRs NOT submitted.')
    watched=agent.watch()
    print(f'Checked {len(watched)} previously submitted PRs.')

def cli():
    parser=argparse.ArgumentParser(description='AI-assisted GitHub contribution agent (manual PR approval required)')
    sub=parser.add_subparsers(dest='command')
    sub.add_parser('go',help='EASIEST: do the whole routine step by step with yes/no questions (default)')
    sub.add_parser('report',help='Plain-language progress report')
    sub.add_parser('doctor',help='Check that git, GitHub and AI keys work')
    sub.add_parser('projects',help='List projects with a simple fit rating')
    sub.add_parser('review',help='Review fixes that are ready and send the ones you say yes to')
    sub.add_parser('scan',help='Discover active repositories and score recent external merges')
    p=sub.add_parser('status',help='Same as report; --details shows technical tables')
    p.add_argument('--details',action='store_true')
    p=sub.add_parser('policy',help='Show upstream contribution-policy documents')
    p.add_argument('repo',help='owner/repo')
    p=sub.add_parser('allow-repo',help='Mark repository eligible after reading its AI and contribution policies')
    p.add_argument('repo'); p.add_argument('--reviewed',action='store_true',help='Confirm that you read policies and AI-assisted contributions are permitted')
    p=sub.add_parser('block-repo',help='Block a repository')
    p.add_argument('repo')
    sub.add_parser('issues',help='Discover issues in reviewed/allowlisted repositories')
    p=sub.add_parser('analyze',help='AI-rank issues')
    p.add_argument('--limit',type=int,default=10)
    p=sub.add_parser('fix',help='Generate patches; never publishes PRs')
    p.add_argument('--limit',type=int,default=5)
    p.add_argument('--run-tests',action='store_true',help='UNSAFE on untrusted repos: explicitly permit running project tests')
    p=sub.add_parser('approve',help='Approve one patch after independent review')
    p.add_argument('id',type=int); p.add_argument('--reviewed',action='store_true')
    p=sub.add_parser('submit',help='Publish approved patch to fork and create upstream PR')
    p.add_argument('id',type=int); p.add_argument('--confirm',action='store_true',help='Explicit GitHub write confirmation')
    sub.add_parser('watch',help='Refresh submitted PR merge/closure states')
    p=sub.add_parser('run-once',help='Discover issues, analyze, prepare fixes, watch merges')
    p.add_argument('--scan',action='store_true');p.add_argument('--limit',type=int,default=10)
    p.add_argument('--run-tests',action='store_true')
    p=sub.add_parser('daemon',help='Repeat discovery and analysis; NEVER posts PRs unattended')
    p.add_argument('--minutes',type=int,default=30);p.add_argument('--run-tests',action='store_true')
    args=parser.parse_args()
    args.command=args.command or 'go'
    gh,agent=make_app()
    try:
        if args.command=='go': go(gh,agent)
        elif args.command=='report': print(simple.report())
        elif args.command=='doctor': doctor(gh,agent)
        elif args.command=='projects': show_projects()
        elif args.command=='review': review_fixes(agent)
        elif args.command=='scan':
            for r in gh.scan():
                print(f"{r['score']:5} {r['full_name']:<45} external merges 30d={r['merged_30d']}, sample={r['closed_sample']}")
            print('Scores are heuristics from up to 100 recently updated closed PRs, NOT true acceptance rates.')
        elif args.command=='status': status(args.details)
        elif args.command=='policy': print(gh.repository_policy(args.repo))
        elif args.command=='allow-repo':
            if not args.reviewed: raise WorkflowError('Read policy via: py main.py policy OWNER/REPO; then use --reviewed')
            if not db.one('SELECT * FROM repos WHERE full_name=?',(args.repo,)):
                raise WorkflowError('Repository not in scan results; run scan first')
            db.execute("UPDATE repos SET policy='allowed' WHERE full_name=?",(args.repo,))
            print('Allowed',args.repo,'for issue discovery. Review all patches before publication.')
        elif args.command=='block-repo':
            db.execute("UPDATE repos SET policy='blocked' WHERE full_name=?",(args.repo,))
            print('Blocked',args.repo)
        elif args.command=='issues': print('Issues discovered:',gh.discover_issues())
        elif args.command=='analyze': print('Results:',agent.analyze(args.limit))
        elif args.command=='fix': print('Results:',agent.fix(args.limit,args.run_tests))
        elif args.command=='approve':
            issue=db.one('SELECT * FROM issues WHERE id=?',(args.id,))
            if not args.reviewed or not issue or issue['state']!='prepared':
                raise WorkflowError('Review proposed.patch and confirm --reviewed; state must be prepared')
            print(Path(issue['diff_path']).read_text(encoding='utf-8')[:40000])
            db.update_issue(args.id,state='approved')
            print('Approved',args.id,'; submit remains a separate explicit action')
        elif args.command=='submit':
            if not args.confirm: raise WorkflowError('Use --confirm after reviewing the approved patch')
            print('Created pull request:',agent.submit(args.id))
        elif args.command=='watch':
            for url,state in agent.watch(): print(state,url)
        elif args.command=='run-once': cycle(gh,agent,args.limit,args.run_tests,args.scan)
        elif args.command=='daemon':
            minutes=max(15,args.minutes)
            print('Running periodic cycle. Press Ctrl+C to stop. Publishing PRs is disabled in daemon.')
            while True:
                try: cycle(gh,agent,10,args.run_tests,scan=False)
                except (GitHubError,WorkflowError,RuntimeError) as e: print('Cycle failed:',e)
                time.sleep(minutes*60)
    except (GitHubError,WorkflowError,RuntimeError) as e:
        parser.exit(1,f'ERROR: {e}\n')

if __name__=='__main__':
    cli()

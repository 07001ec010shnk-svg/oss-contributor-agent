from pathlib import Path
import time
import pandas as pd
import streamlit as st
from agent.config import LiveConfig
from agent import db
from agent.github import GitHub
from agent import simple
from agent.config import settings

st.set_page_config(page_title='Open-Source Contributor Agent',page_icon='🔧',layout='wide')
db.init()
config=LiveConfig()
st.title('GitHub Open-Source Contributor Agent')
st.caption('Local dashboard • approval-gated publication • no automated GitHub submissions')

issues=db.rows('SELECT * FROM issues ORDER BY updated DESC')
repos=db.rows('SELECT * FROM repos ORDER BY score DESC')
c=simple.counts()
goal=int(settings().get('goal_merged_prs',100))
a,b,c3,d=st.columns(4)
a.metric('PRs accepted (merged)',c['merged'])
b.metric('Waiting for an answer',c['waiting'])
c3.metric('Fixes ready for you',c['ready'])
d.metric('Projects approved',sum(r['policy']=='allowed' for r in repos))
st.progress(min(1.0,c['merged']/max(1,goal)),text=f"Goal: {c['merged']} / {goal} merged PRs")
st.caption("Easiest way to work: close this page and run  python main.py go  (or double-click start.bat).")

repo_tab,issue_tab,usage_tab=st.tabs(['Repositories','Issues and reviews','API usage'])
with repo_tab:
    st.subheader('Repository scoring')
    st.caption('Score uses a capped sample of recently updated closed PRs; it is not a verified merge probability. External author classification is approximate.')
    if repos:
        st.dataframe([{'project':r['full_name'],'fit':simple.rating(r)[0],'why':simple.rating(r)[1],'your decision':r['policy']}
                      for r in repos],hide_index=True,use_container_width=True)
        with st.expander('Technical numbers'):
            st.dataframe([{k:r[k] for k in ('full_name','score','merged_30d','external_merged','closed_sample','median_merge_days','policy')}
                          for r in repos],hide_index=True,use_container_width=True)
        pick=st.selectbox('Inspect repository policy', [r['full_name'] for r in repos])
        selected=next(r for r in repos if r['full_name']==pick)
        st.markdown(f"[Repository page]({selected['url']})")
        if st.button('Read contribution policies from GitHub'):
            try: st.session_state['policy_text']=GitHub(config).repository_policy(pick)
            except Exception as e: st.error(str(e))
            st.session_state['policy_repo']=pick
        if st.session_state.get('policy_repo')==pick and st.session_state.get('policy_text'):
            st.text_area('Review policies (including AI contribution rules)',st.session_state['policy_text'],height=250)
            reviewed=st.checkbox('I checked the repository policies and AI-assisted contributions are permitted',key=f'checked-{pick}')
            if st.button('Allow repository',disabled=not reviewed):
                db.execute("UPDATE repos SET policy='allowed' WHERE full_name=?",(pick,))
                st.success('Repository allowlisted for discovery. Individual PRs still require your approval.')
                st.rerun()
        if st.button('Block repository'):
            db.execute("UPDATE repos SET policy='blocked' WHERE full_name=?",(pick,))
            st.rerun()
    else: st.info('Run: py main.py scan')
with issue_tab:
    if issues:
        st.dataframe([{k:x[k] for k in ('id','repo','number','title','state','review_state','fit_score','pr_url','last_error')}
                      for x in issues],hide_index=True,use_container_width=True)
        item_id=st.selectbox('Select issue',options=[r['id'] for r in issues],format_func=lambda v:next(f"{r['repo']}#{r['number']} — {r['title'][:45]}" for r in issues if r['id']==v))
        issue=next(i for i in issues if i['id']==item_id)
        st.markdown(f"[Open issue]({issue['url']})")
        st.text_area('AI analysis',issue.get('analysis') or 'Not analyzed',height=130)
        st.text_area('Test results',issue.get('test_result') or 'Not available',height=100)
        if issue.get('diff_path') and Path(issue['diff_path']).exists():
            patch=Path(issue['diff_path']).read_text(encoding='utf-8')
            st.code(patch[:80000],language='diff')
        if issue['state']=='prepared':
            check=st.checkbox('I reviewed the diff, verified correctness and contribution-policy compliance',key=f'review-{item_id}')
            if st.button('Approve patch',disabled=not check):
                db.update_issue(item_id,state='approved')
                st.success(f"Approved. To publish run: py main.py submit {item_id} --confirm")
                st.rerun()
        elif issue['state']=='approved':
            st.info(f"Approved. Publication is a separate step: py main.py submit {item_id} --confirm")
        if issue.get('pr_url'): st.markdown(f"[View pull request]({issue['pr_url']})")
    else: st.info('Discover and analyze issues after allowing repositories.')
with usage_tab:
    st.caption('Locally observed usage only. Provider quotas may be shared with other apps/keys. Key changes are loaded on the next request.')
    values=config.snapshot()
    raw=db.rows('''SELECT provider,model,COUNT(*) AS requests,
         COALESCE(SUM(request_tokens),0) AS input_tokens,
         COALESCE(SUM(response_tokens),0) AS output_tokens,
         SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) AS errors
         FROM usage WHERE ts>=? GROUP BY provider,model ORDER BY provider,model''',(time.time()-86400,))
    if raw:
        st.dataframe(raw,hide_index=True,use_container_width=True)
        grouped={}
        for r in raw:
            v=grouped.setdefault(r['provider'],{'requests':0,'tokens':0})
            v['requests']+=r['requests'];v['tokens']+=r['input_tokens']+r['output_tokens']
        for provider,v in grouped.items():
            day_limit=int(values.get(provider.upper()+'_RPD','0') or '0')
            token_limit=int(values.get(provider.upper()+'_TPD','0') or '0')
            st.write(f"**{provider}** — {v['requests']} requests in past 24 hours"+
                     (f" / configured limit {day_limit}" if day_limit else ' / provider limit unknown'))
            if day_limit: st.progress(min(1.0,v['requests']/day_limit))
            if token_limit: st.caption(f"Locally observed tokens: {v['tokens']:,} / configured daily cap {token_limit:,}")
            if provider=='cloudflare':
                details=[r for r in raw if r['provider']=='cloudflare']
                rate_in=float(values.get('CLOUDFLARE_INPUT_NEURONS_PER_M','0') or 0)
                rate_out=float(values.get('CLOUDFLARE_OUTPUT_NEURONS_PER_M','0') or 0)
                neurons=sum((r['input_tokens']*rate_in+r['output_tokens']*rate_out)/1_000_000 for r in details)
                limit=int(values.get('CLOUDFLARE_DAILY_NEURONS','0') or 0)
                st.caption(f'Estimated Cloudflare neurons used (last 24h): {neurons:.1f}'+(f' / {limit:,} configured daily' if limit else ''))
    else: st.info('No API calls recorded yet.')
    r=db.one("SELECT value FROM api_state WHERE name='github_remaining'")
    if r: st.caption('Last reported GitHub API core/search response rate-limit remaining: '+r['value']+' (resource-dependent)')
    st.markdown('Secrets are deliberately never displayed.')

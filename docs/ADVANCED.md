# GitHub Open-Source Contributor Agent — Windows MVP

An AI-assisted contribution pipeline: find receptive public projects, review their AI contribution policies, discover issues, analyze with LLMs, prepare patches, review manually, and publish PRs only with separate explicit approval. Tracks merges and locally observed provider usage. No Ponytail required.

**This is an initial functional implementation, not a production-hardened autonomous developer.** Patches need human review, external APIs/models change, and tests can be unsafe in untrusted repositories.

## Prerequisites

- Windows with Python 3.11+ and Git on `PATH` (no WSL).
- A **valid** GitHub PAT (and optionally Gemini/Groq/Cloudflare/OpenRouter API keys); Ollama can be local.
- Configure Git author identity if you intend to make commits elsewhere: `git config --global user.name "..."` and `git config --global user.email "..."`.
- A fine-grained PAT must be able to access your fork with **Contents: write**, and GitHub may require **Administration: write** on the destination for API forking. It may **not** be able to create PRs on arbitrary third-party base repos; if so, create the fork/PR manually using the URLs reported by the agent or use an appropriately authorized credential. GitHub will not grant third-party repo write access merely because you have a PAT.

## Install (PowerShell, in this folder)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item api_keys.env.example api_keys.env
notepad api_keys.env
python main.py --help
python main.py scan
streamlit run dashboard.py
```

If PowerShell blocks venv activation, call `.\.venv\Scripts\python.exe main.py scan` directly instead of activating.

Keep your existing `api_keys.env` instead of copying the example if you already have it. Ensure it is located **in the same folder as main.py**. Never place your actual secrets in chat or in source control.

## Routine

```powershell
# 1: Locate repos with recent external merged PRs. Scores are only heuristics.
python main.py scan
python main.py status
# 2: Check policies explicitly (including AI policy and how to claim issues):
python main.py policy OWNER/REPO
python main.py allow-repo OWNER/REPO --reviewed
# 3: Discover genuine open issues and have AI select the best:
python main.py issues
python main.py analyze --limit 15
# 4: Generate patches (by default NO untrusted tests are executed):
python main.py fix --limit 5
# 5: Review proposed.patch under workspaces/ISSUE_ID, plus the actual changes.
#    Optionally run tests ONLY in a sandbox/container you trust. 
python main.py approve 1234567 --reviewed
# 6: Explicitly publish ONE approved PR:
python main.py submit 1234567 --confirm
# 7: Later, update merges:
python main.py watch
```

A local Streamlit dashboard can approve patches after review. It does **not** publish PRs itself. Run `python main.py run-once` after you have allowlisted repositories to automate issue discovery, analysis, patch preparation, and PR monitoring. To repeat every 30 minutes, use `python main.py daemon --minutes 30`; stop with Ctrl+C. Only the explicit `submit --confirm` command writes branches and creates PRs.

### Tests

```powershell
python -m pytest -q tests
```

`python main.py fix --run-tests` is deliberately **opt-in and dangerous on untrusted repositories**: invoking pytest/npm commands can execute hostile project code, install scripts, plugins, and tests. Use a throwaway sandbox/container/VM without your secrets for real testing. The agent's patch validator checks Python syntax and staged diffs but **does not prove a fix is correct or safe**. Never treat tests merely passing as sufficient approval.

### Key changes while running

Change and save `api_keys.env`. Before every new AI or GitHub request, the application re-reads changed configuration; already-running requests finish using their old credential snapshot. It does not print keys. If you save an incomplete file, a request may fail; edit atomically for best results.

API usage is stored in `agent.db`. Usage and remaining quota are **estimates**, not provider-account balances: quota resets, other applications and provider-defined model limits can differ. Rate-limit errors cool down their provider. Existing v13 variable names are read, but old model IDs/limits may no longer be valid. Provider routing falls back if the upstream provider rejects an ID.

### Important limitations

- Repo score samples **up to 100** recently updated closed PRs, which biases the ratio: it is *not* a true acceptance rate. Automated issue discovery prioritizes bug/help-wanted labels but may find unsuitable issues.
- Requires your explicit repository policy review and separate per-patch approval. Verify maintainer consent and claim/coordinate on an issue where appropriate.
- Large codebases and bugs needing extensive runtime investigation often cannot be solved with the limited source context. Test/inspect every generated patch.
- Publishing multiple files produces multiple commits on the fork; partial publication can occur on API failure. Retry only after inspecting the branch.
- The application does not auto-address review comments or sync stale fork branches. Both are deliberate next-version features.
- Anthropic's open-source eligibility is discretionary; trivial or automated merged PRs may not qualify.
- For sensitive tokens, revoke/rotate any credentials that were previously disclosed. `api_keys.env` is ignored by Git.

### Dashboard note

Install Streamlit from requirements.txt before running the dashboard. It binds to your machine by default; keep it local and do not publicly expose an approval interface without access control.

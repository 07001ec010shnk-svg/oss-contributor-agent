# Open-Source Contributor Agent (beginner friendly)

A helper that **finds friendly open-source projects, finds small bugs, drafts a fix with AI, and sends it as a pull request only after YOU read it and type `yes`.**

It is a helper, not an autopilot. You are the author of every pull request, so you must read and understand each change before you send it.

> **Be a good guest.** Maintainers are volunteers. Send only changes you understand, keep them small, answer their comments politely, and follow each project's rules (especially about AI). Never use this tool to spam projects.

## What you need
- Windows 10/11 (the commands also work on Mac/Linux, see the end)
- [Python 3.11 or newer](https://www.python.org/downloads/) (tick **Add to PATH** when installing)
- [Git](https://git-scm.com/downloads)
- A free GitHub account with **two-factor authentication (2FA)** switched on
- A free Google AI key (Gemini)

## Set up (about 10 minutes, once)

**1. Download this project.** On the GitHub page click the green **Code** button, then **Download ZIP**, and unzip it to a folder such as `D:\oss-contributor-agent`.

**2. Get a Gemini key.** Open https://aistudio.google.com/apikey, sign in and click **Create API key**. Copy it.

**3. Get a GitHub token.** On GitHub go to **Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token (classic)**. Tick only **`public_repo`**, set an expiry (for example 30 days), generate it and copy it. *(Use a classic token: fine-grained tokens often cannot open pull requests on other people's projects.)*

**4. Run `start.bat`** (double-click). The first time it installs what it needs and opens a file called `api_keys.env`.

**5. Paste your keys** after `GITHUB_TOKEN=` and `GEMINI_API_KEY=`, save the file, and close it.

> **Never share `api_keys.env`.** Do not post it, send it in chat, or upload it to GitHub. If a key leaks, delete it on the website and make a new one.

## Daily routine (15 minutes)
Double-click **`start.bat`** and answer the questions:

- **"Work on this project? y / n / s"**: Open the link and read the project's contributing rules first. Say `n` if they ban AI-written contributions, or if the project looks suspicious (for example, almost 100 outside pull requests merged instantly).
- **"Type 'yes' to send this fix"**: Read the change. Say `yes` only if you understand it and it is small. Press Enter to decide later, or type `no` to throw it away.

After you send a pull request, open it on GitHub and check:
1. **Files changed** shows only a few lines, not hundreds.
2. The project's checks are not failing because of your change.
3. The description follows the project's pull-request template (many projects require it).

Then watch your email for replies and answer them.

## Built-in safety
- Nothing is sent without your typed `yes`.
- At most **1 pull request per day** by default (`max_prs_per_day` in `settings.json`).
- Skips projects whose rules ban AI contributions, and issues that already have a pull request.
- Refuses to edit **security-sensitive code** (logins, permissions, secrets, crypto, payments) and build/dependency files.
- A second AI reviews each fix and throws away bad ones.
- Checks that the project did not change the same file since the fix was written.

## Handy commands
Run these in PowerShell inside the project folder:

| Command | What it does |
|---|---|
| `.\.venv\Scripts\python.exe main.py go` | The daily routine (same as `start.bat`) |
| `.\.venv\Scripts\python.exe main.py report` | Your progress (sent, merged, waiting) |
| `.\.venv\Scripts\python.exe main.py doctor` | Checks Git, your GitHub token and your AI key |
| `.\.venv\Scripts\python.exe main.py scan` | Looks for new projects now |
| `.\.venv\Scripts\python.exe main.py watch` | Updates the status of your pull requests |
| `.\.venv\Scripts\python.exe reset.py` | Cleans up failed or closed attempts so they can be retried |

## If something goes wrong
- **Run `doctor` first.** It tells you what to fix.
- **"search text not found" or "0 fixes prepared"** is normal. The AI sometimes misquotes a file and the agent safely refuses. Try again another day.
- **A clone times out** on a very large project: block it with `main.py block-repo OWNER/REPO`.
- **"Existing workspace" or "STILL LOCKED"**: close VS Code and File Explorer windows on this folder, then run `reset.py`.
- **A pull request gets closed:** read the maintainer's reason. It is feedback, not a punishment.

## What counts as success
Merged pull requests, not sent ones. Small, correct, well-explained fixes get merged. Many rushed ones get closed.

## Mac / Linux
Create a virtual environment and run the same Python commands:
```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp api_keys.env.example api_keys.env   # then edit it
.venv/bin/python main.py go
```

## Contributing to this project
Pull requests are welcome. Good first contributions: improve this README, add a test, make an error message clearer. Run the tests with `.venv\Scripts\python.exe -m pytest -q tests` before you send a pull request.

More technical details: [docs/ADVANCED.md](docs/ADVANCED.md).

## License
MIT. See [LICENSE](LICENSE).

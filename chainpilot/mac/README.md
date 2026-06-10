# ChainPilot, macOS Quick Start

These three `.command` files let you run ChainPilot on a Mac without ever opening Terminal manually. Just double-click them in Finder.

---

## First time (≈ 3 minutes)

1. **Edit `.env`** in the project root and paste your Anthropic API key in place of `your_api_key_here`. Slack and SMTP entries are optional, the app shows draft messages in the UI when those credentials aren't set.

2. **Double-click `setup.command`.**
   It checks Python and Node, creates a virtual environment, and installs all dependencies. You only need to do this once.
   - If macOS blocks the script with "cannot be opened because it is from an unidentified developer," right-click it instead and choose **Open**, then click **Open** in the dialog.

3. **Double-click `start.command`.**
   Two Terminal windows open (backend and frontend), and your browser opens to `http://localhost:3002`.

---

## Every other time

Just double-click `start.command`. Setup is already done.

---

## Stopping the app

Close the two Terminal windows ("ChainPilot, Backend" and "ChainPilot, Frontend").

If something weird happens, a window crashed, or the ports stay busy, double-click `stop.command` to force-kill anything still holding ports `8002` and `3002`.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| "python3 not found" | Install Python 3 from <https://www.python.org/downloads/macos/> or run `brew install python` |
| "Node.js not found" | Install Node LTS from <https://nodejs.org/> or run `brew install node` |
| Browser opens but page is blank | Wait 10 seconds and refresh, Vite is still building |
| "Address already in use" on port 8002 or 3002 | Run `stop.command`, then try `start.command` again |
| App won't connect to Anthropic | Re-check your `.env` API key and your internet connection |
| "cannot be opened because it is from an unidentified developer" | Right-click the file → Open → Open |

---

## What's running where

| Service | URL | What it does |
|---|---|---|
| FastAPI backend | <http://localhost:8002> | Multi-agent pipeline, monitor loop, trust + uncertainty engines |
| React frontend | <http://localhost:3002> | The dashboard you actually look at |

API docs while it's running: <http://localhost:8002/docs>

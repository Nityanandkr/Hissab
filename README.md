# Hisaab

Understand your UPI spending. Hisaab reads a bank or UPI-app statement (CSV / Excel), works out who each payment went to - including local
shops and QR payments - tells shops from people, puts every payment in a category with a reason and a confidence, learns from your
corrections, finds recurring and unusual payments, and answers plain-language questions about your money.

Everything runs on your own computer. Your data is one SQLite file in `data/`; nothing is uploaded anywhere.

## Run it

* Double-click **Hisaab** (the icon `Hisaab.desktop` in this folder) - it starts the app from this folder and opens your browser.
* Or in a terminal: `python launch.py` (start + open the browser), `python launch.py --stop` (stop it), `python launch.py --status`.
* Or run the server directly: `.venv/bin/python run.py` and open http://127.0.0.1:5056

Moved the folder? Run `python launch.py --make-desktop` once to rebuild the icon for the new location.

## What is inside

| page | what it does |
|---|---|
| Dashboard | this period's spending, month-end pace, categories, insights |
| Transactions | every payment; click a category to correct it; click a row to see how it was decided |
| Review | the few questions where your answer helps most - each answer teaches the app |
| Merchants | resolved shops / people / brands; rename, merge, shop-or-person |
| Patterns | recurring payments (stopped / price changes), daily habits, unusual payments with reasons |
| Ask | plain-language questions ("How much did I spend on groceries last month?") |
| Import | upload one or several statements at once, check the detected columns, load demo data, delete a profile's data |
| Learning | what the app has learned, undo a lesson, label a month in Excel and measure it on your own data |
| Under the hood | paste any bank narration and watch each step of the analysis |
| Profile | several profiles (one per person, or one for the demo), each with its own data; name, monthly budget, income, goal |

## Install somewhere else

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt     # Python 3.11 or newer
.venv/bin/python run.py
```

Optional: `pip install -r requirements-optional.txt`, then set `ANTHROPIC_API_KEY` and `HISAAB_LLM=anthropic` to let a language model help
with questions the built-in rules cannot parse. It is off by default and only ever receives the text of your question.

## Your data

* Everything is stored in `data/` (a SQLite file and the model files). The folder starts empty.
* **Import -> Delete all my data** removes it; deleting the folder's contents does the same.
* Each extra profile is its own folder in `data/profiles/`; the first profile uses `data/` itself. Deleting a profile deletes its folder.
* The server only listens on 127.0.0.1, rejects requests addressed to any other host name, and does not write request logs.

## Using it beyond one computer

Hisaab is a single-user, local-first app: it has no login, it keeps one person's data, and it only accepts requests addressed to localhost.
Do not expose it to a network as it is. To host it for other people, add authentication, per-user storage, HTTPS and a configurable
list of allowed host names first.

### Demo on Render

`render.yaml` and `app.py` let you run a public **demo** on Render: New -> Blueprint -> this repo, or a Web Service with build command
`pip install -r requirements.txt` and start command `gunicorn app:app --workers 1 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT`.
The demo has no login and Render's free disk is wiped on every restart, so use **Import -> Load demo data** only, never a real statement.

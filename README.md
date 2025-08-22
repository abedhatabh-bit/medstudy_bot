# MedStudy Telegram Bot (Free Tier Friendly)

A medical study helper bot for Telegram. Upload PDFs/DOCX/TXT and get **summaries**, **flashcards**, and **quizzes**. Exports flashcards to CSV for Anki/Quizlet.

## Features
- 📄 Parse PDF / DOCX / TXT
- ✂️ Summarize important sentences (lightweight, no paid APIs)
- 🗂️ Generate cloze flashcards
- 🧠 Generate multiple-choice quizzes (Telegram quiz polls)
- 📤 Export flashcards to CSV
- 🧰 SQLite storage (simple & free)

> Note: summarization/flashcard logic is heuristic (no LLM). You can later plug in any LLM API key to improve results.

---

## 1) Create your bot with BotFather
1. Open Telegram and start a chat with **@BotFather**.
2. `/newbot` → choose a **name** and a **username` (must end with `bot`).  
3. Copy the **Bot Token** it gives you.

Optional: `/setcommands` and paste:
```
start - Start the bot
help - How to use
list - Show recent files
summary - Summarize last file
flashcards - Generate flashcards
quiz - Create a quiz
export - Export flashcards CSV
```

---

## 2) Run locally (polling)
1. Install Python 3.10+
2. Clone or unzip this project
3. `pip install -r requirements.txt`
4. Copy `.env.example` to `.env` and set `BOT_TOKEN=...`
5. Run: `python bot.py`

Send your bot a PDF/DOCX/TXT and try:
```
/summary 5
/flashcards 10
/quiz 5
/export
```

---

## 3) Deploy for 24/7 (free-tier friendly)

### Option A: Render (free web service)
1. Create a new **Web Service** from this repo/zip.
2. Set environment variables:
   - `BOT_TOKEN` = your token from BotFather
   - `APP_URL` = your Render URL (e.g., `https://<service>.onrender.com`)
   - `WEBHOOK_MODE` = `1`
3. Render will run `python bot.py` (see `render.yaml`).
4. The bot auto-sets its webhook to `APP_URL/<BOT_TOKEN>` on start.

> Free tiers may sleep after inactivity (cold starts). Telegram webhooks should wake it up automatically.

### Option B: Any VPS
- Run with `WEBHOOK_MODE=1` and set `APP_URL=https://your.domain` (use HTTPS).

---

## 4) Usage Tips
- Keep uploads ≤ 32 MB; prefer **text-based** PDFs (not scanned images).
- The first summary is a preview; tweak `/summary N` for more/less detail.
- Use `/export` to get a CSV you can import into Anki/Quizlet.
- For better Arabic support, provide well-structured text (headings, bullets).

---

## 5) Extend with LLMs (optional)
If you wish to use an LLM for higher quality summaries/quizzes:
- In `bot.py`, after extracting text, call your LLM API.
- Cache generated content in SQLite to save tokens.

---

## Security
- Never share your `BOT_TOKEN`.
- Consider rotating tokens with BotFather if leaked.

---

## License
MIT

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MedStudy Telegram Bot (Free-tier friendly)
Features:
- Upload PDFs/DOCX/TXT, extract text, store per-user in SQLite
- Summaries, flashcards (cloze), and MCQ quizzes
- Export flashcards to CSV (Anki-importable)
- Works with polling (local dev) or webhook (24/7 on free-tier hosts)
"""

import os
import re
import csv
import sqlite3
import time
from datetime import datetime
from typing import List, Tuple, Dict

from telegram import Update, InputFile
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters
)

# ---------------------- Configuration ----------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
APP_URL = os.getenv("APP_URL", "").strip()  # e.g., https://your-service.onrender.com
PORT = int(os.getenv("PORT", "8080"))
WEBHOOK_MODE = os.getenv("WEBHOOK_MODE", "0") == "1"  # "1" to enable webhook

DB_PATH = os.getenv("DB_PATH", "medstudy.db")
MAX_SUMMARY_SENTENCES = int(os.getenv("MAX_SUMMARY_SENTENCES", "8"))
MAX_FLASHCARDS = int(os.getenv("MAX_FLASHCARDS", "30"))
MAX_QUIZ = int(os.getenv("MAX_QUIZ", "15"))
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "32"))  # Telegram limit for standard bots is higher; keep conservative

# ---------------------- Lightweight NLP utils ----------------------

AR_STOP = set("""
و في من على إلى عن ثم أو أم بل إذا إن أن كان تكون تكونوا كانوا كانت كما لكن لأن
ما لا لم لن مع هذا هذه ذلك تلك هناك هنا أي أين كيف لماذا الذي التي الذين اللواتي اللائي
""".split())

EN_STOP = set("""
a an and are as at be but by for if in into is it no not of on or such that the their then there these they this to was will with
""".split())

def normalize(text: str) -> str:
    text = text.replace('\xa0', ' ').replace('\u200f', '').strip()
    # unify Arabic/English punctuation
    text = re.sub(r'[“”«»]', '"', text)
    text = re.sub(r"[’']", "'", text)
    return text

def split_sentences(text: str) -> List[str]:
    # naive sentence splitter handling Arabic/English punctuation
    text = normalize(text)
    # Protect abbreviations
    text = re.sub(r"(\w)\.(\w)\.", r"\1.\2.", text)
    parts = re.split(r"(?<=[\.!\؟\?])\s+|\n+", text)
    return [s.strip() for s in parts if len(s.strip()) > 0]

def tokens(text: str) -> List[str]:
    t = re.findall(r"[A-Za-z\u0600-\u06FF]+", text)
    return [w.lower() for w in t]

def keyword_scores(text: str) -> Dict[str, float]:
    toks = tokens(text)
    freq: Dict[str, int] = {}
    for w in toks:
        if w in EN_STOP or w in AR_STOP or len(w) < 3:
            continue
        freq[w] = freq.get(w, 0) + 1
    if not freq:
        return {}
    maxv = max(freq.values())
    return {k: v / maxv for k, v in freq.items()}

def summarize(text: str, n: int = 5) -> List[str]:
    sents = split_sentences(text)
    if not sents:
        return []
    scores = keyword_scores(text)
    scored = []
    for s in sents:
        toks = tokens(s)
        if not toks:
            continue
        sc = sum(scores.get(w, 0.0) for w in toks) / max(1, len(toks))
        scored.append((sc, s))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in scored[:max(1, n)]]

def pick_keywords(text: str, k: int = 20) -> List[str]:
    scores = keyword_scores(text)
    # prefer longer medical-like terms
    items = sorted(scores.items(), key=lambda kv: (kv[1], len(kv[0])), reverse=True)
    return [w for w,_ in items[:k]]

def make_cloze(sentence: str, term: str) -> Tuple[str, str]:
    pattern = re.compile(re.escape(term), re.IGNORECASE)
    cloze = pattern.sub("____", sentence, count=1)
    # Capitalize trimmed cloze nicely
    return cloze.strip(), term

def generate_flashcards_from_text(text: str, n: int = 10) -> List[Tuple[str, str]]:
    sents = split_sentences(text)
    kws = pick_keywords(text, k=min(50, n*5 if n >= 5 else 20))
    cards = []
    used = set()
    for kw in kws:
        for s in sents:
            if re.search(rf"\b{re.escape(kw)}\b", s, flags=re.IGNORECASE):
                q, a = make_cloze(s, kw)
                key = (q.lower(), a.lower())
                if key not in used:
                    cards.append((q, a))
                    used.add(key)
                    break
        if len(cards) >= n:
            break
    # fallback: if not enough, just use top sentences with blanks on top words
    if len(cards) < n:
        extra = summarize(text, n)
        for s in extra:
            toks = [t for t in tokens(s) if t not in EN_STOP | AR_STOP and len(t) >= 4]
            if not toks:
                continue
            q, a = make_cloze(s, toks[0])
            key = (q.lower(), a.lower())
            if key not in used:
                cards.append((q, a))
            if len(cards) >= n:
                break
    return cards[:n]

def generate_quiz(text: str, n: int = 5) -> List[Dict]:
    # Returns list of quiz dicts with 'question','options','correct_index'
    cards = generate_flashcards_from_text(text, n*2)  # extra to ensure variety
    kws = pick_keywords(text, k=50)
    quizzes = []
    idx = 0
    for q, a in cards:
        distractors = [w for w in kws if w.lower() != a.lower()][:10]
        # pick 3 distractors
        ds = []
        for w in distractors:
            if w.lower() != a.lower() and w.lower() not in {d.lower() for d in ds}:
                ds.append(w)
            if len(ds) == 3:
                break
        opts = ds + [a]
        # shuffle deterministically per idx (avoid importing random for reproducibility)
        if len(opts) < 4:
            continue
        # simple swap logic
        swap_pos = idx % 4
        opts[swap_pos], opts[-1] = opts[-1], opts[swap_pos]
        quizzes.append({
            "question": q,
            "options": opts,
            "correct_index": swap_pos
        })
        idx += 1
        if len(quizzes) >= n:
            break
    return quizzes[:n]

# ---------------------- File extraction ----------------------
def extract_text_from_pdf(path: str) -> str:
    try:
        from pdfminer.high_level import extract_text as pdf_extract_text
        return pdf_extract_text(path) or ""
    except Exception as e:
        return ""

def extract_text_from_docx(path: str) -> str:
    try:
        import docx
        d = docx.Document(path)
        return "\n".join(p.text for p in d.paragraphs if p.text)
    except Exception as e:
        return ""

def extract_text_from_txt(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""

def extract_text(path: str) -> str:
    if path.lower().endswith(".pdf"):
        return extract_text_from_pdf(path)
    elif path.lower().endswith(".docx"):
        return extract_text_from_docx(path)
    elif path.lower().endswith(".txt"):
        return extract_text_from_txt(path)
    return ""

# ---------------------- DB helpers ----------------------
def db_init():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS documents(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        filename TEXT,
        text TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS cards(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        doc_id INTEGER,
        front TEXT,
        back TEXT
    )
    """)
    con.commit()
    con.close()

def db_add_document(user_id: int, filename: str, text: str) -> int:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        "INSERT INTO documents(user_id, filename, text, created_at) VALUES(?,?,?,?)",
        (user_id, filename, text, datetime.utcnow().isoformat())
    )
    doc_id = cur.lastrowid
    con.commit()
    con.close()
    return doc_id

def db_get_last_doc(user_id: int) -> Tuple[int, str, str]:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT id, filename, text FROM documents WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,))
    row = cur.fetchone()
    con.close()
    if row:
        return row[0], row[1], row[2]
    return 0, "", ""

def db_list_docs(user_id: int, limit: int = 10) -> List[Tuple[int,str]]:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT id, filename FROM documents WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit))
    rows = cur.fetchall()
    con.close()
    return rows

def db_save_cards(user_id: int, doc_id: int, cards: List[Tuple[str, str]]):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    for front, back in cards:
        cur.execute("INSERT INTO cards(user_id, doc_id, front, back) VALUES(?,?,?,?)", (user_id, doc_id, front, back))
    con.commit()
    con.close()

def db_get_cards(user_id: int, doc_id: int) -> List[Tuple[str, str]]:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT front, back FROM cards WHERE user_id=? AND doc_id=? ORDER BY id", (user_id, doc_id))
    rows = cur.fetchall()
    con.close()
    return [(r[0], r[1]) for r in rows]

# ---------------------- Handlers ----------------------

WELCOME = (
    "مرحباً 👋 أنا بوت دراسي طبي.\n\n"
    "أرسل لي ملف PDF أو DOCX أو TXT (مثلاً محاضرة)، وسأقوم بـ:\n"
    "• استخراج النص ✅\n"
    "• عمل ملخص ✂️\n"
    "• إنشاء بطاقات Flashcards 🗂️\n"
    "• إنشاء Quiz تفاعلي 🧠\n\n"
    "الأوامر:\n"
    "/summary <n> — ملخص من n جمل (افتراضياً 5)\n"
    "/flashcards <n> — إنشاء بطاقات (افتراضياً 10)\n"
    "/quiz <n> — اختبار من n أسئلة (افتراضياً 5)\n"
    "/export — تحميل بطاقاتك كملف CSV\n"
    "/list — عرض آخر الملفات المرفوعة\n"
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME)

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    docs = db_list_docs(uid, limit=10)
    if not docs:
        await update.message.reply_text("لا يوجد ملفات بعد. أرسل ملف PDF/DOCX/TXT أولاً.")
        return
    lines = [f"{doc_id}: {name}" for doc_id, name in docs]
    await update.message.reply_text("آخر ملفاتك:\n" + "\n".join(lines))

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    doc = msg.document or msg.audio or msg.video or msg.voice or msg.photo
    if not msg.document:
        await msg.reply_text("أرسل كـ Document (PDF/DOCX/TXT) من فضلك.")
        return
    file = msg.document
    size_mb = (file.file_size or 0) / (1024*1024)
    if size_mb > MAX_FILE_SIZE_MB:
        await msg.reply_text(f"الملف كبير ({size_mb:.1f} MB). الرجاء إرسال ملف ≤ {MAX_FILE_SIZE_MB} MB.")
        return
    filename = file.file_name or f"file_{int(time.time())}"
    file_path = os.path.join("uploads", f"{int(time.time())}_{filename}")
    os.makedirs("uploads", exist_ok=True)
    tgfile = await context.bot.get_file(file.file_id)
    await tgfile.download_to_drive(file_path)

    text = extract_text(file_path)
    if not text or len(text.strip()) < 50:
        await msg.reply_text("تعذر استخراج نص مفيد من الملف. تأكد أنه PDF نصي/Docx/TXT.")
        return

    doc_id = db_add_document(update.effective_user.id, filename, text)
    # quick summary preview
    summary_sents = summarize(text, n=5)
    preview = "• " + "\n• ".join(summary_sents[:5]) if summary_sents else "لم أتمكن من التلخيص."
    await msg.reply_text(
        f"تم حفظ الملف: {filename} (ID: {doc_id}) ✅\n\n"
        f"ملخص سريع:\n{preview}\n\n"
        "استخدم:\n"
        "/summary 5 — ملخص مفصل\n"
        "/flashcards 10 — إنشاء بطاقات\n"
        "/quiz 5 — اختبار تفاعلي\n"
        "/export — تصدير البطاقات"
    )

async def summary_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    n = 5
    if context.args:
        try:
            n = min(MAX_SUMMARY_SENTENCES, max(1, int(context.args[0])))
        except Exception:
            pass
    doc_id, fname, text = db_get_last_doc(uid)
    if not doc_id:
        await update.message.reply_text("لا يوجد ملف حديث. أرسل ملفاً أولاً.")
        return
    sents = summarize(text, n=n)
    if not sents:
        await update.message.reply_text("تعذر إنشاء ملخص.")
        return
    out = "ملخص:\n" + "\n".join(f"• {s}" for s in sents)
    await update.message.reply_text(out)

async def flashcards_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    n = 10
    if context.args:
        try:
            n = min(MAX_FLASHCARDS, max(1, int(context.args[0])))
        except Exception:
            pass
    doc_id, fname, text = db_get_last_doc(uid)
    if not doc_id:
        await update.message.reply_text("لا يوجد ملف حديث. أرسل ملفاً أولاً.")
        return
    cards = generate_flashcards_from_text(text, n=n)
    if not cards:
        await update.message.reply_text("تعذر إنشاء بطاقات من هذا النص.")
        return
    db_save_cards(uid, doc_id, cards)
    preview = "\n\n".join([f"Q: {q}\nA: {a}" for q,a in cards[:5]])
    await update.message.reply_text(f"تم إنشاء {len(cards)} بطاقة ✅\n\nأمثلة:\n{preview}")

async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    doc_id, fname, text = db_get_last_doc(uid)
    if not doc_id:
        await update.message.reply_text("لا يوجد ملف حديث. أرسل ملفاً أولاً.")
        return
    cards = db_get_cards(uid, doc_id)
    if not cards:
        await update.message.reply_text("لا يوجد بطاقات محفوظة لهذا الملف. استخدم /flashcards أولاً.")
        return
    os.makedirs("exports", exist_ok=True)
    path = os.path.join("exports", f"flashcards_{uid}_{doc_id}.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Front", "Back"])
        for q, a in cards:
            w.writerow([q, a])
    await update.message.reply_document(document=InputFile(path), filename=os.path.basename(path), caption="CSV جاهز للاستيراد في Anki/Quizlet")

async def quiz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    n = 5
    if context.args:
        try:
            n = min(MAX_QUIZ, max(1, int(context.args[0])))
        except Exception:
            pass
    doc_id, fname, text = db_get_last_doc(uid)
    if not doc_id:
        await update.message.reply_text("لا يوجد ملف حديث. أرسل ملفاً أولاً.")
        return
    quizzes = generate_quiz(text, n=n)
    if not quizzes:
        await update.message.reply_text("تعذر إنشاء أسئلة.")
        return
    for q in quizzes:
        await update.message.reply_poll(
            question=q["question"][:300],  # Telegram limit
            options=q["options"][:10],
            type="quiz",
            correct_option_id=q["correct_index"],
            is_anonymous=False
        )

# ---------------------- Main ----------------------
def main():
    assert BOT_TOKEN, "Please set BOT_TOKEN environment variable"
    db_init()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("summary", summary_cmd))
    app.add_handler(CommandHandler("flashcards", flashcards_cmd))
    app.add_handler(CommandHandler("export", export_cmd))
    app.add_handler(CommandHandler("quiz", quiz_cmd))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    if WEBHOOK_MODE:
        # run webhook server (no Flask needed)
        url_path = BOT_TOKEN  # obscure path
        webhook_url = f"{APP_URL}/{url_path}"
        print(f"Starting webhook at 0.0.0.0:{PORT}, set webhook => {webhook_url}")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=url_path,
            webhook_url=webhook_url
        )
    else:
        print("Running in polling mode...")
        app.run_polling()

if __name__ == "__main__":
    main()

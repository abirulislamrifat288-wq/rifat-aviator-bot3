
import os
import json
import logging
import threading
from pathlib import Path
from statistics import median
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

DATA_FILE = Path(os.getenv("DATA_FILE", "/tmp/history.json"))
MAX_HISTORY = 5000


# Render Web Service health-check server
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Telegram Aviator Bot is running!")

    def log_message(self, format, *args):
        pass


def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    logging.info("Web server running on port %s", port)
    server.serve_forever()


def load_history():
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        return [float(x) for x in data if float(x) > 0]
    except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
        return []


def save_history(values):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(
        json.dumps(values[-MAX_HISTORY:], indent=2),
        encoding="utf-8"
    )


def fmt(x):
    return f"{x:.2f}x"


def range_summary(values):
    if not values:
        return "History নেই। আগে /add 1.45 দিয়ে ফলাফল যোগ করো।"

    ordered = sorted(values)
    n = len(ordered)

    low = ordered[max(0, int((n - 1) * 0.25))]
    mid = median(ordered)
    high = ordered[min(n - 1, int((n - 1) * 0.75))]

    under2 = sum(x < 2 for x in values) / n * 100
    over5 = sum(x >= 5 for x in values) / n * 100

    return (
        f"Past reference range: {fmt(low)}–{fmt(high)}\n"
        f"Median: {fmt(mid)}\n"
        f"Past rounds below 2x: {under2:.1f}%\n"
        f"Past rounds at/above 5x: {over5:.1f}%\n"
        f"Sample size: {n} rounds"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "স্বাগতম! এটি Aviator History Analysis Bot।\n\n"
        "কমান্ড:\n"
        "/add 1.45 — ফলাফল যোগ\n"
        "/history — শেষ ২০টি ফলাফল\n"
        "/analyze — History বিশ্লেষণ\n"
        "/stats — পরিসংখ্যান\n"
        "/clear — সব History মুছবে\n\n"
        "সতর্কতা: অতীতের ফলাফল দিয়ে পরের multiplier "
        "নির্ভরযোগ্যভাবে বলা যায় না।"
    )


async def add_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("ব্যবহার: /add 1.45")
        return

    try:
        value = float(context.args[0].lower().replace("x", ""))

        if not 1.0 <= value <= 100000:
            raise ValueError

    except ValueError:
        await update.message.reply_text(
            "সঠিক multiplier দাও। উদাহরণ: /add 1.45"
        )
        return

    values = load_history()
    values.append(value)
    save_history(values)

    await update.message.reply_text(
        f"যোগ হয়েছে: {fmt(value)}\nমোট History: {len(values)}"
    )


async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    values = load_history()

    if not values:
        await update.message.reply_text(
            "এখনো কোনো History যোগ করা হয়নি।"
        )
        return

    recent = values[-20:]
    lines = [
        f"{i}. {fmt(v)}"
        for i, v in enumerate(reversed(recent), 1)
    ]

    await update.message.reply_text(
        "সর্বশেষ ফলাফল (নতুনটি আগে):\n" + "\n".join(lines)
    )


async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    values = load_history()

    if len(values) < 10:
        await update.message.reply_text(
            f"বিশ্লেষণের জন্য অন্তত ১০টি ফলাফল দরকার। এখন আছে {len(values)}টি।"
        )
        return

    recent = values[-30:]
    past = range_summary(values)

    recent_low = sum(x < 2 for x in recent) / len(recent) * 100
    recent_high = sum(x >= 5 for x in recent) / len(recent) * 100

    await update.message.reply_text(
        "📊 HISTORY ANALYSIS\n\n"
        f"{past}\n\n"
        f"শেষ {len(recent)} রাউন্ডে:\n"
        f"Below 2x: {recent_low:.1f}%\n"
        f"5x বা বেশি: {recent_high:.1f}%\n\n"
        "এটি শুধু অতীতের ফলাফলের সারাংশ, "
        "পরবর্তী রাউন্ডের নিশ্চিত Prediction নয়।"
    )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    values = load_history()

    if not values:
        await update.message.reply_text("এখনো কোনো History নেই।")
        return

    buckets = [
        ("1.00x–1.99x", lambda x: 1 <= x < 2),
        ("2.00x–4.99x", lambda x: 2 <= x < 5),
        ("5.00x–9.99x", lambda x: 5 <= x < 10),
        ("10x+", lambda x: x >= 10),
    ]

    lines = [f"মোট রাউন্ড: {len(values)}"]

    for label, test in buckets:
        count = sum(test(x) for x in values)
        lines.append(
            f"{label}: {count} ({count / len(values) * 100:.1f}%)"
        )

    await update.message.reply_text(
        "📈 HISTORY STATS\n" + "\n".join(lines)
    )


async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_history([])
    await update.message.reply_text("সব History মুছে ফেলা হয়েছে।")


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")

    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN environment variable সেট করো।"
        )

    # Start HTTP server for Render
    threading.Thread(
        target=start_web_server,
        daemon=True
    ).start()

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add_result))
    app.add_handler(CommandHandler("history", history_cmd))
    app.add_handler(CommandHandler("analyze", analyze))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("clear", clear_history))

    app.run_polling()


if __name__ == "__main__":
    main()

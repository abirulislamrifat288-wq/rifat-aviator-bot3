
import os
import re
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


# Render health server
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Aviator Bot is running!")

    def log_message(self, format, *args):
        pass


def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    logging.info("Web server running on port %s", port)
    server.serve_forever()


# History: oldest -> newest
def load_history():
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        return [
            float(x) for x in data
            if 1 <= float(x) <= 100000
        ]
    except (FileNotFoundError, json.JSONDecodeError,
            TypeError, ValueError):
        return []


def save_history(values):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(
        json.dumps(values[-MAX_HISTORY:], indent=2),
        encoding="utf-8"
    )


def fmt(x):
    return f"{x:.2f}x"


def bucket(x):
    if x < 2:
        return "1.00x–1.99x"
    if x < 5:
        return "2.00x–4.99x"
    if x < 10:
        return "5.00x–9.99x"
    return "10x+"


def percentile(values, fraction):
    ordered = sorted(values)
    index = int((len(ordered) - 1) * fraction)
    return ordered[index]


def range_summary(values):
    if not values:
        return "কোনো History নেই।"

    return (
        f"Median: {fmt(median(values))}\n"
        f"Below 2x: "
        f"{sum(x < 2 for x in values) / len(values) * 100:.1f}%\n"
        f"5x বা বেশি: "
        f"{sum(x >= 5 for x in values) / len(values) * 100:.1f}%\n"
        f"মোট রাউন্ড: {len(values)}"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✈️ স্বাগতম Rifat Aviator Analysis Bot-এ!\n\n"
        "কমান্ড:\n"
        "/add 1.45 — একটি ফলাফল যোগ\n"
        "/bulk 7.14 6.17 3.51 — অনেক ফলাফল যোগ\n"
        "/history — শেষ ২০টি ফলাফল\n"
        "/analyze — History বিশ্লেষণ\n"
        "/predict — পরবর্তী রাউন্ডের পরিসংখ্যানভিত্তিক অনুমান\n"
        "/stats — পরিসংখ্যান\n"
        "/clear — History মুছে ফেলা\n\n"
        "নোট: অনুমান নিশ্চিত ভবিষ্যদ্বাণী নয়।"
    )


async def add_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("ব্যবহার: /add 1.45")
        return

    try:
        value = float(context.args[0].lower().replace("x", ""))
        if not 1 <= value <= 100000:
            raise ValueError
    except ValueError:
        await update.message.reply_text("সঠিক Multiplier দাও।")
        return

    values = load_history()
    values.append(value)
    save_history(values)

    await update.message.reply_text(
        f"✅ যোগ হয়েছে: {fmt(value)}\n"
        f"মোট History: {len(values)}"
    )


async def bulk_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "ব্যবহার:\n"
            "/bulk 7.14 6.17 3.51 3.73\n\n"
            "প্রথমে নতুন ফলাফল, পরে পুরোনো ফলাফল দেবে।"
        )
        return

    raw = " ".join(context.args)
    parts = re.split(r"[\s,;]+", raw.strip())

    if len(parts) > 1000:
        await update.message.reply_text(
            "একবারে সর্বোচ্চ ১০০০টি ফলাফল যোগ করা যাবে।"
        )
        return

    try:
        new_results = [
            float(item.lower().replace("x", ""))
            for item in parts if item
        ]

        if not new_results or any(
            x < 1 or x > 100000 for x in new_results
        ):
            raise ValueError

    except ValueError:
        await update.message.reply_text(
            "Multiplier সঠিকভাবে দাও। যেমন:\n"
            "/bulk 7.14 6.17 3.51 3.73"
        )
        return

    values = load_history()

    # User sends newest -> oldest.
    # Internally store oldest -> newest.
    values.extend(reversed(new_results))
    save_history(values)

    await update.message.reply_text(
        f"✅ {len(new_results)}টি ফলাফল যোগ হয়েছে!\n"
        f"মোট History: {len(values)}টি\n"
        "নতুন ফলাফলগুলো সঠিক ক্রমে সংরক্ষিত হয়েছে।"
    )


async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    values = load_history()

    if not values:
        await update.message.reply_text("এখনো কোনো History নেই।")
        return

    recent = list(reversed(values[-20:]))

    lines = [
        f"{i}. {fmt(v)}"
        for i, v in enumerate(recent, 1)
    ]

    await update.message.reply_text(
        "📜 সর্বশেষ ফলাফল (নতুনটি আগে):\n\n"
        + "\n".join(lines)
    )


async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    values = load_history()

    if len(values) < 10:
        await update.message.reply_text(
            f"কমপক্ষে ১০টি ফলাফল দরকার। এখন আছে {len(values)}টি।"
        )
        return

    recent = values[-30:]

    await update.message.reply_text(
        "📊 HISTORY ANALYSIS\n\n"
        + range_summary(values)
        + "\n\nশেষ "
        + str(len(recent))
        + " রাউন্ডের বিশ্লেষণ:\n"
        + range_summary(recent)
    )


async def predict(update: Update, context: ContextTypes.DEFAULT_TYPE):
    values = load_history()

    if len(values) < 20:
        await update.message.reply_text(
            f"বিশ্লেষণের জন্য অন্তত ২০টি History দরকার।\n"
            f"এখন আছে: {len(values)}টি"
        )
        return

    latest = values[-1]
    latest_bucket = bucket(latest)

    # Find historical rounds in the same multiplier category.
    # Examine the result immediately after each matching round.
    following = []

    for i in range(len(values) - 1):
        if bucket(values[i]) == latest_bucket:
            following.append(values[i + 1])

    if len(following) < 5:
        await update.message.reply_text(
            "🔎 PREDICTION ANALYSIS\n\n"
            f"সর্বশেষ ফলাফল: {fmt(latest)}\n"
            f"ক্যাটাগরি: {latest_bucket}\n"
            f"একই ক্যাটাগরির পরের রাউন্ডের নমুনা: "
            f"{len(following)}টি\n\n"
            "এখনো পর্যাপ্ত মিল পাওয়া যায়নি। "
            "আরও History যোগ করো।\n\n"
            "কোনো নির্ভরযোগ্য Prediction দেওয়া যাচ্ছে না।"
        )
        return

    low = percentile(following, 0.25)
    mid = median(following)
    high = percentile(following, 0.75)

    labels = [
        ("1.00x–1.99x", lambda x: x < 2),
        ("2.00x–4.99x", lambda x: 2 <= x < 5),
        ("5.00x–9.99x", lambda x: 5 <= x < 10),
        ("10x+", lambda x: x >= 10),
    ]

    distribution = []

    for label, test in labels:
        count = sum(test(x) for x in following)
        pct = count / len(following) * 100
        distribution.append(f"{label}: {pct:.1f}%")

    await update.message.reply_text(
        "🔎 NEXT ROUND ANALYSIS\n\n"
        f"সর্বশেষ ফলাফল: {fmt(latest)}\n"
        f"সর্বশেষ ক্যাটাগরি: {latest_bucket}\n\n"
        "📊 আগের একই ক্যাটাগরির পরের ফলাফল:\n"
        f"নমুনা: {len(following)}টি\n"
        f"ঐতিহাসিক 25%-75% রেঞ্জ: "
        f"{fmt(low)} – {fmt(high)}\n"
        f"Median: {fmt(mid)}\n\n"
        "পরের ফলাফলের ঐতিহাসিক বণ্টন:\n"
        + "\n".join(distribution)
        + "\n\n⚠️ এটি শুধু অতীতের পরিসংখ্যান। "
        "এটি পরের রাউন্ডের নির্ভরযোগ্য ভবিষ্যদ্বাণী নয়।"
    )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    values = load_history()

    if not values:
        await update.message.reply_text("এখনো কোনো History নেই।")
        return

    labels = [
        ("1.00x–1.99x", lambda x: x < 2),
        ("2.00x–4.99x", lambda x: 2 <= x < 5),
        ("5.00x–9.99x", lambda x: 5 <= x < 10),
        ("10x+", lambda x: x >= 10),
    ]

    lines = [f"মোট রাউন্ড: {len(values)}"]

    for label, test in labels:
        count = sum(test(x) for x in values)
        lines.append(
            f"{label}: {count} "
            f"({count / len(values) * 100:.1f}%)"
        )

    await update.message.reply_text(
        "📈 HISTORY STATS\n\n" + "\n".join(lines)
    )


async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_history([])
    await update.message.reply_text("সব History মুছে ফেলা হয়েছে।")


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")

    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN সেট করা নেই।")

    threading.Thread(
        target=start_web_server,
        daemon=True
    ).start()

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add_result))
    app.add_handler(CommandHandler("bulk", bulk_add))
    app.add_handler(CommandHandler("history", history_cmd))
    app.add_handler(CommandHandler("analyze", analyze))
    app.add_handler(CommandHandler("predict", predict))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("clear", clear_history))

    app.run_polling()


if __name__ == "__main__":
    main()

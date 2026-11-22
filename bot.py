
import os
import re
import json
import logging
import threading
import hashlib
from pathlib import Path
from statistics import median
from http.server import BaseHTTPRequestHandler, HTTPServer

from PIL import Image, ImageOps, ImageEnhance
import pytesseract

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

DATA_FILE = Path(os.getenv("DATA_FILE", "/tmp/history.json"))
MAX_HISTORY = 5000

# একই ছবি বারবার পাঠালে আবার যোগ হওয়া আটকাতে
LAST_IMAGE_HASH = {}


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


def prediction_text(values):
    if len(values) < 30:
        return (
            "বিশ্লেষণের জন্য অন্তত ৩০টি History দরকার।\n"
            f"বর্তমানে আছে: {len(values)}টি"
        )

    latest = values[-1]
    current_bucket = bucket(latest)

    estimates = []

    for round_no in range(1, 5):
        samples = []

        for i in range(len(values) - round_no):
            if bucket(values[i]) == current_bucket:
                samples.append(values[i + round_no])

        if len(samples) < 5:
            estimates.append(
                f"Round {round_no}: পর্যাপ্ত তথ্য নেই "
                f"({len(samples)}টি নমুনা)"
            )
            continue

        low = percentile(samples, 0.25)
        high = percentile(samples, 0.75)
        mid = median(samples)

        estimates.append(
            f"Round {round_no}\n"
            f"আনুমানিক Range: {fmt(low)} – {fmt(high)}\n"
            f"Median: {fmt(mid)}\n"
            f"নমুনা: {len(samples)}টি"
        )

    return (
        "🤖 AVIATOR AI ANALYSIS\n\n"
        f"সর্বশেষ ফলাফল: {fmt(latest)}\n\n"
        "🔮 পরবর্তী ৪ রাউন্ডের আনুমানিক বিশ্লেষণ:\n\n"
        + "\n\n".join(estimates)
        + "\n\n⚠️ এটি অতীতের ডেটাভিত্তিক পরিসংখ্যান। "
        "নিশ্চিত ভবিষ্যদ্বাণী নয়।"
    )


# Screenshot থেকে Multiplier পড়া
def read_multipliers(image_path):
    image = Image.open(image_path).convert("RGB")

    image = ImageOps.grayscale(image)
    image = ImageOps.autocontrast(image)
    image = ImageEnhance.Contrast(image).enhance(2)

    image = image.resize(
        (image.width * 2, image.height * 2)
    )

    data = pytesseract.image_to_data(
        image,
        config="--psm 11",
        output_type=pytesseract.Output.DICT
    )

    found = []

    for i, word in enumerate(data["text"]):
        word = word.strip().replace(",", ".")

        matches = re.findall(
            r"(?<!\d)(\d{1,5}\.\d{1,2})(?!\d)",
            word
        )

        for match in matches:
            try:
                value = float(match)

                if 1 <= value <= 100000:
                    found.append((
                        data["top"][i],
                        data["left"][i],
                        value
                    ))
            except ValueError:
                pass

    # ওপর থেকে নিচে, একই সারিতে বাম থেকে ডানে
    found.sort(key=lambda item: (item[0], item[1]))

    results = [item[2] for item in found]

    # একই multiplier পরপর পড়া হলে একবার রাখা
    cleaned = []

    for value in results:
        if not cleaned or value != cleaned[-1]:
            cleaned.append(value)

    return cleaned


# Start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✈️ স্বাগতম Rifat Aviator Analysis Bot-এ!\n\n"
        "শুধু Aviator History Screenshot পাঠাও।\n"
        "বট নিজে থেকে ছবি পড়ে পরবর্তী ৪ রাউন্ডের "
        "আনুমানিক বিশ্লেষণ দেখাবে।\n\n"
        "কমান্ড:\n"
        "/add 1.45\n"
        "/bulk 7.14 6.17 3.51\n"
        "/history\n"
        "/analyze\n"
        "/predict\n"
        "/stats\n"
        "/clear\n\n"
        "⚠️ অনুমান নিশ্চিত ফলাফল নয়।"
    )


# Add one result
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


# Add many results
async def bulk_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "ব্যবহার:\n"
            "/bulk 7.14 6.17 3.51 3.73\n\n"
            "প্রথমে নতুন ফলাফল, পরে পুরোনো ফলাফল দেবে।"
        )
        return

    parts = re.split(r"[\s,;]+", " ".join(context.args).strip())

    try:
        results = [
            float(x.lower().replace("x", ""))
            for x in parts if x
        ]

        if not results or len(results) > 1000 or any(
            x < 1 or x > 100000 for x in results
        ):
            raise ValueError

    except ValueError:
        await update.message.reply_text("Multiplier সঠিকভাবে দাও।")
        return

    values = load_history()
    values.extend(reversed(results))
    save_history(values)

    await update.message.reply_text(
        f"✅ {len(results)}টি ফলাফল যোগ হয়েছে!\n"
        f"মোট History: {len(values)}টি"
    )


# Automatic screenshot handler
async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message

    await message.reply_text(
        "🖼️ Screenshot পড়ছি এবং বিশ্লেষণ করছি..."
    )

    try:
        photo = message.photo[-1]

        telegram_file = await context.bot.get_file(photo.file_id)

        image_path = (
            f"/tmp/aviator_{message.from_user.id}_"
            f"{message.message_id}.jpg"
        )

        await telegram_file.download_to_drive(image_path)

        # Duplicate screenshot detection
        image_bytes = Path(image_path).read_bytes()
        image_hash = hashlib.sha256(image_bytes).hexdigest()

        user_id = message.from_user.id

        if LAST_IMAGE_HASH.get(user_id) == image_hash:
            await message.reply_text(
                "এই Screenshot আগেই পাঠানো হয়েছে। "
                "একই ছবি আবার যোগ করা হয়নি।"
            )
            return

        results = read_multipliers(image_path)

        if len(results) < 10:
            await message.reply_text(
                "ছবি থেকে যথেষ্ট Multiplier পড়তে পারিনি।\n"
                "আরও পরিষ্কার Screenshot পাঠাও।"
            )
            return

        # Screenshot থেকে পাওয়া ফলাফল নতুন -> পুরোনো
        values = load_history()
        values.extend(reversed(results))
        save_history(values)

        LAST_IMAGE_HASH[user_id] = image_hash

        preview = " ".join(fmt(x) for x in results[:30])

        await message.reply_text(
            f"✅ Screenshot থেকে {len(results)}টি ফলাফল পড়েছি।\n"
            f"পড়া সংখ্যাগুলো: {preview}\n\n"
            + prediction_text(values)
        )

    except Exception:
        logging.exception("Screenshot analysis error")

        await message.reply_text(
            "Screenshot বিশ্লেষণ করতে সমস্যা হয়েছে। "
            "আরও পরিষ্কার Screenshot পাঠিয়ে আবার চেষ্টা করো।"
        )


# History command
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


# Analyze command
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
        f"মোট রাউন্ড: {len(values)}\n"
        f"Median: {fmt(median(values))}\n"
        f"Below 2x: "
        f"{sum(x < 2 for x in values) / len(values) * 100:.1f}%\n"
        f"5x বা বেশি: "
        f"{sum(x >= 5 for x in values) / len(values) * 100:.1f}%\n\n"
        f"শেষ {len(recent)} রাউন্ডের Median: "
        f"{fmt(median(recent))}"
    )


# Predict command
async def predict(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        prediction_text(load_history())
    )


# Stats command
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


# Clear history
async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_history([])
    await update.message.reply_text(
        "সব History মুছে ফেলা হয়েছে।"
    )


# Main function
def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")

    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN সেট করা নেই।"
        )

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

    # Screenshot automatically process
    app.add_handler(
        MessageHandler(filters.PHOTO, photo_handler)
    )

    logging.info("Aviator Bot started!")

    app.run_polling()


if __name__ == "__main__":
    main()


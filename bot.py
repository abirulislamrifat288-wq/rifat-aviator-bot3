
import os
import re
import json
import logging
import hashlib
import threading
from io import BytesIO
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from collections import Counter
from statistics import median

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

# ---------------- CONFIG ----------------

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DATA_FILE = Path(os.getenv("DATA_FILE", "/tmp/history.json"))
MAX_HISTORY = 1000

LAST_IMAGE_HASH = {}

# ---------------- HEALTH SERVER ----------------

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")

    def log_message(self, format, *args):
        pass


def start_health_server():
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()


# ---------------- DATA ----------------

def load_data():
    if not DATA_FILE.exists():
        return {}

    try:
        with open(DATA_FILE, "r") as f:
            data = json.load(f)

        if isinstance(data, dict):
            return data

        if isinstance(data, list):
            return {"legacy": data}

        return {}

    except Exception:
        logging.exception("Could not load history")
        return {}


def save_data(data):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(DATA_FILE, "w") as f:
        json.dump(data, f)


DATA = load_data()


def get_history(user_id):
    key = str(user_id)

    if key not in DATA:
        DATA[key] = DATA.pop("legacy", [])

    return DATA[key]


def add_results(user_id, values):
    history = get_history(user_id)
    history.extend(values)
    DATA[str(user_id)] = history[-MAX_HISTORY:]
    save_data(DATA)


# ---------------- MULTIPLIER ANALYSIS ----------------

def get_bucket(value):
    if value < 1.5:
        return "1.00x–1.49x"
    elif value < 2:
        return "1.50x–1.99x"
    elif value < 3:
        return "2.00x–2.99x"
    elif value < 5:
        return "3.00x–4.99x"
    elif value < 10:
        return "5.00x–9.99x"
    else:
        return "10.00x+"


def percentile(values, p):
    values = sorted(values)

    if not values:
        return 0

    index = int((len(values) - 1) * p)
    return values[index]


def estimate_next_20(history):
    """
    Historical comparison only.
    It does not know or guarantee future Aviator results.
    """

    if len(history) < 10:
        return (
            "২০ রাউন্ডের পরিসংখ্যান তৈরি করতে অন্তত "
            "১০টি ফলাফল দরকার। আরও ইতিহাস যোগ করো।"
        )

    latest_bucket = get_bucket(history[-1])

    lines = [
        "📊 পরবর্তী ২০ রাউন্ডের ইতিহাসভিত্তিক অনুমান",
        "",
        f"সর্বশেষ ফলাফল: {history[-1]:.2f}x",
        f"সর্বশেষ রেঞ্জ: {latest_bucket}",
        "",
    ]

    for round_no in range(1, 21):
        similar_results = []

        # Find older occasions with the same previous-result bucket.
        for i in range(len(history) - round_no):
            if get_bucket(history[i]) == latest_bucket:
                similar_results.append(history[i + round_no])

        # If there are too few matching examples, use all available
        # historical results at that distance.
        if len(similar_results) < 5:
            similar_results = history[round_no:]

        if not similar_results:
            similar_results = history

        low = percentile(similar_results, 0.25)
        high = percentile(similar_results, 0.75)
        middle = median(similar_results)

        lines.append(
            f"রাউন্ড {round_no:02d}: "
            f"{low:.2f}x – {high:.2f}x "
            f"(মাঝামাঝি: {middle:.2f}x)"
        )

    lines.extend([
        "",
        "📌 এটি অতীতের ফলাফলের পরিসংখ্যান,",
        "ভবিষ্যতের নিশ্চিত ফলাফল নয়।",
        "প্রতিটি রাউন্ড স্বাধীন ও অনিশ্চিত হতে পারে।",
    ])

    return "\n".join(lines)


def analyze_history(history):
    if not history:
        return "এখনো কোনো ইতিহাস নেই।"

    total = len(history)
    average = sum(history) / total
    low = sum(1 for x in history if x < 2)
    medium = sum(1 for x in history if 2 <= x < 5)
    high = sum(1 for x in history if x >= 5)

    return (
        "📊 ইতিহাস বিশ্লেষণ\n\n"
        f"মোট ফলাফল: {total}\n"
        f"গড় মাল্টিপ্লায়ার: {average:.2f}x\n"
        f"2x-এর নিচে: {low}\n"
        f"2x থেকে 5x: {medium}\n"
        f"5x বা তার বেশি: {high}\n"
    )


# ---------------- OCR SCREENSHOT ----------------

def read_multipliers(image_bytes):
    image = Image.open(BytesIO(image_bytes)).convert("RGB")

    # Improve image readability
    image = ImageOps.grayscale(image)
    image = ImageEnhance.Contrast(image).enhance(2.0)

    width, height = image.size
    image = image.resize((width * 2, height * 2))

    image = image.point(lambda p: 255 if p > 150 else 0)

    text = pytesseract.image_to_string(
        image,
        config="--psm 11"
    )

    # Read values followed by x, such as 1.25x or 12.50x
    matches = re.findall(
        r"(?<![\d.])(\d{1,4}(?:[.,]\d{1,2})?)\s*[xX×]",
        text
    )

    results = []

    for match in matches:
        try:
            value = float(match.replace(",", "."))

            if 1.0 <= value <= 10000:
                results.append(value)

        except ValueError:
            continue

    # Remove consecutive duplicate OCR readings
    cleaned = []

    for value in results:
        if not cleaned or value != cleaned[-1]:
            cleaned.append(value)

    return cleaned


# ---------------- TELEGRAM COMMANDS ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 স্বাগতম!\n\n"
        "Aviator History Analysis Bot চালু আছে।\n\n"
        "📸 Aviator history-এর screenshot পাঠাও।\n"
        "আমি multiplier পড়ার চেষ্টা করব এবং "
        "পরবর্তী ২০ রাউন্ডের ইতিহাসভিত্তিক বিশ্লেষণ দেখাব।\n\n"
        "Commands:\n"
        "/add 1.50 - একটি ফলাফল যোগ\n"
        "/bulk 1.20,2.50,3.00 - একাধিক ফলাফল যোগ\n"
        "/history - শেষের ফলাফল দেখো\n"
        "/analyze - ইতিহাস বিশ্লেষণ\n"
        "/predict - ২০ রাউন্ডের অনুমান\n"
        "/stats - মোট পরিসংখ্যান\n"
        "/clear - ইতিহাস মুছবে"
    )


async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("ব্যবহার: /add 1.50")
        return

    try:
        value = float(context.args[0].lower().replace("x", ""))

        if value < 1 or value > 10000:
            raise ValueError

        add_results(update.effective_user.id, [value])

        await update.message.reply_text(
            f"✅ {value:.2f}x ইতিহাসে যোগ হয়েছে।"
        )

    except ValueError:
        await update.message.reply_text("সঠিক multiplier দাও।")


async def bulk_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "ব্যবহার: /bulk 1.20,2.50,3.00"
        )
        return

    text = " ".join(context.args)
    matches = re.findall(r"\d+(?:[.,]\d+)?", text)

    values = []

    for item in matches:
        try:
            value = float(item.replace(",", "."))

            if 1 <= value <= 10000:
                values.append(value)

        except ValueError:
            pass

    if not values:
        await update.message.reply_text("কোনো সঠিক multiplier পাওয়া যায়নি।")
        return

    add_results(update.effective_user.id, values)

    await update.message.reply_text(
        f"✅ {len(values)}টি ফলাফল ইতিহাসে যোগ হয়েছে।"
    )


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    history = get_history(update.effective_user.id)

    if not history:
        await update.message.reply_text("এখনো কোনো ইতিহাস নেই।")
        return

    recent = history[-20:]

    text = "📜 শেষের ফলাফল:\n\n"

    for i, value in enumerate(reversed(recent), 1):
        text += f"{i}. {value:.2f}x\n"

    text += f"\nমোট ফলাফল: {len(history)}"

    await update.message.reply_text(text)


async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    history = get_history(update.effective_user.id)
    await update.message.reply_text(analyze_history(history))


async def predict_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    history = get_history(update.effective_user.id)
    await update.message.reply_text(estimate_next_20(history))


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    history = get_history(update.effective_user.id)

    if not history:
        await update.message.reply_text("এখনো কোনো ইতিহাস নেই।")
        return

    counts = Counter(get_bucket(x) for x in history)

    text = (
        "📈 History Statistics\n\n"
        f"মোট ফলাফল: {len(history)}\n"
        f"সর্বোচ্চ: {max(history):.2f}x\n"
        f"সর্বনিম্ন: {min(history):.2f}x\n\n"
        "রেঞ্জ অনুযায়ী ফলাফল:\n"
    )

    for bucket, count in counts.items():
        percentage = count / len(history) * 100
        text += f"{bucket}: {count} ({percentage:.1f}%)\n"

    await update.message.reply_text(text)


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)

    DATA[user_id] = []
    save_data(DATA)

    LAST_IMAGE_HASH.pop(user_id, None)

    await update.message.reply_text("🗑️ তোমার ইতিহাস মুছে ফেলা হয়েছে।")


# ---------------- AUTOMATIC PHOTO HANDLER ----------------

async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)

    try:
        photo = update.message.photo[-1]
        telegram_file = await photo.get_file()
        image_bytes = bytes(await telegram_file.download_as_bytearray())

        image_hash = hashlib.sha256(image_bytes).hexdigest()

        if LAST_IMAGE_HASH.get(user_id) == image_hash:
            await update.message.reply_text(
                "এই একই screenshot আগে পাঠানো হয়েছে। "
                "নতুন screenshot পাঠাও।"
            )
            return

        await update.message.reply_text(
            "📸 Screenshot পেয়েছি। ফলাফল পড়ার চেষ্টা করছি..."
        )

        values = read_multipliers(image_bytes)

        if not values:
            await update.message.reply_text(
                "❌ Screenshot থেকে multiplier পড়তে পারিনি।\n\n"
                "আরও পরিষ্কার screenshot পাঠাও। "
                "Multiplier-এর পাশে x দেখা যেতে হবে।"
            )
            return

        # Assumption: screenshot shows newest result first.
        # Reverse it before appending to oldest-to-newest history.
        values.reverse()

        add_results(user_id, values)
        LAST_IMAGE_HASH[user_id] = image_hash

        history = get_history(user_id)

        result_text = (
            "✅ Screenshot থেকে ফলাফল পাওয়া গেছে!\n\n"
            f"নতুন যোগ হয়েছে: {len(values)}টি\n"
            f"মোট ইতিহাস: {len(history)}টি\n\n"
            "পড়া multiplier:\n"
        )

        result_text += ", ".join(
            f"{value:.2f}x" for value in reversed(values)
        )

        await update.message.reply_text(result_text)

        await update.message.reply_text(
            estimate_next_20(history)
        )

    except Exception:
        logging.exception("Photo processing failed")

        await update.message.reply_text(
            "❌ Screenshot বিশ্লেষণ করতে সমস্যা হয়েছে। "
            "আবার চেষ্টা করো।"
        )


# ---------------- MAIN ----------------

def main():
    if not TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN environment variable is missing!"
        )

    threading.Thread(
        target=start_health_server,
        daemon=True
    ).start()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add_command))
    app.add_handler(CommandHandler("bulk", bulk_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("analyze", analyze_command))
    app.add_handler(CommandHandler("predict", predict_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("clear", clear_command))

    app.add_handler(
        MessageHandler(filters.PHOTO, photo_handler)
    )

    logging.info("Aviator analysis bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()

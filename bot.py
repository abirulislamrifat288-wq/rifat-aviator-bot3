
import os
import re
import json
import logging
import threading
from pathlib import Path
from statistics import median
from http.server import BaseHTTPRequestHandler, HTTPServer

from PIL import Image, ImageOps, ImageEnhance
import pytesseract

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

DATA_FILE = Path(os.getenv("DATA_FILE", "/tmp/history.json"))
MAX_HISTORY = 5000


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
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()


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
    if len(values) < 20:
        return (
            "বিশ্লেষণের জন্য অন্তত ২০টি History দরকার।\n"
            f"এখন আছে: {len(values)}টি"
        )

    latest = values[-1]
    latest_bucket = bucket(latest)

    following = [
        values[i + 1]
        for i in range(len(values) - 1)
        if bucket(values[i]) == latest_bucket
    ]

    if len(following) < 5:
        return (
            "পর্যাপ্ত মিল পাওয়া যায়নি। আরও History যোগ করো।\n"
            f"সর্বশেষ ফলাফল: {fmt(latest)}\n"
            f"মিল পাওয়া নমুনা: {len(following)}টি"
        )

    low = percentile(following, 0.25)
    mid = median(following)
    high = percentile(following, 0.75)

    return (
        "🔎 NEXT ROUND ANALYSIS\n\n"
        f"সর্বশেষ ফলাফল: {fmt(latest)}\n"
        f"ক্যাটাগরি: {latest_bucket}\n"
        f"নমুনা: {len(following)}টি\n\n"
        f"📊 ঐতিহাসিক 25%-75% রেঞ্জ:\n"
        f"{fmt(low)} – {fmt(high)}\n"
        f"Median: {fmt(mid)}\n\n"
        "⚠️ এটি অতীতের ডেটাভিত্তিক অনুমান। "
        "পরের রাউন্ডের নিশ্চিত ভবিষ্যদ্বাণী নয়।"
    )


def read_multipliers(image_path):
    image = Image.open(image_path).convert("RGB")

    # OCR-এর জন্য ছবির কনট্রাস্ট ও সাইজ বাড়ানো
    image = ImageOps.grayscale(image)
    image = ImageOps.autocontrast(image)
    image = ImageEnhance.Contrast(image).enhance(2)
    image = image.resize(
        (image.width * 2, image.height * 2)
    )

    data = pytesseract.image_to_data(
        image,
        config="--psm 6",
        output_type=pytesseract.Output.DICT
    )

    found = []

    for i, word in enumerate(data["text"]):
        word = word.strip().replace(",", ".")

        matches = re.findall(
            r"(?<!\d)(\d{1,5}\.\d{1,2})",
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

    # ওপরের সারি আগে, একই সারিতে বাম থেকে ডানে
    found.sort(key=lambda item: (item[0], item[1]))

    return [item[2] for item in found]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✈️ স্বাগতম Rifat Aviator Analysis Bot-এ!\n\n"
        "Screenshot পাঠালে বট Multiplier পড়ার চেষ্টা করবে।\n"
        "পড়া সংখ্যা নিশ্চিত করলে History-তে যোগ হবে।\n\n"
        "কমান্ড:\n"
        "/add 1.45\n"
        "/bulk 7.14 6.17 3.51\n"
        "/history\n"
        "/analyze\n"
        "/predict\n"
        "/stats\n"
        "/clear\n\n"
        "⚠️ Prediction নিশ্চিত নয়।"
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
        f"✅ যোগ হয়েছে: {fmt(value)}\nমোট History: {len(values)}"
    )


async def bulk_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "ব্যবহার: /bulk 7.14 6.17 3.51\n"
            "প্রথমে নতুন, পরে পুরোনো ফলাফল দেবে।"
        )
        return

    parts = re.split(r"[\s,;]+", " ".join(context.args).strip())

    try:
        results = [float(x.lower().replace("x", "")) for x in parts if x]
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


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    photo = message.photo[-1]

    await message.reply_text("🖼️ Screenshot পড়ছি, একটু অপেক্ষা করো...")

    try:
        telegram_file = await context.bot.get_file(photo.file_id)
        image_path = "/tmp/aviator_screenshot.jpg"
        await telegram_file.download_to_drive(image_path)

        results = read_multipliers(image_path)

        if len(results) < 3:
            await message.reply_text(
                "ছবি থেকে যথেষ্ট Multiplier পড়তে পারিনি।\n"
                "আরও পরিষ্কার Screenshot পাঠাও।"
            )
            return

        context.user_data["pending_results"] = results

        preview = " ".join(fmt(x) for x in results[:60])

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ নিশ্চিত, History-তে যোগ করো",
                    callback_data="ocr_confirm"
                ),
                InlineKeyboardButton(
                    "❌ বাতিল",
                    callback_data="ocr_cancel"
                )
            ]
        ])

        await message.reply_text(
            f"ছবি থেকে {len(results)}টি Multiplier পড়েছি।\n\n"
            f"{preview}\n\n"
            "ক্রমটি নতুন ফলাফল থেকে পুরোনো ফলাফল হিসেবে পড়া হয়েছে।\n"
            "সংখ্যাগুলো ঠিক থাকলে Confirm চাপো।",
            reply_markup=keyboard
        )

    except Exception:
        logging.exception("OCR error")
        await message.reply_text(
            "ছবি পড়তে সমস্যা হয়েছে। আবার পরিষ্কার Screenshot পাঠাও।"
        )


async def ocr_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "ocr_cancel":
        context.user_data.pop("pending_results", None)
        await query.edit_message_text("ছবি থেকে পাওয়া ফলাফল বাতিল করা হয়েছে।")
        return

    results = context.user_data.pop("pending_results", None)

    if not results:
        await query.edit_message_text("কোনো অপেক্ষমাণ ফলাফল পাওয়া যায়নি।")
        return

    values = load_history()
    values.extend(reversed(results))
    save_history(values)

    await query.edit_message_text(
        f"✅ {len(results)}টি ফলাফল History-তে যোগ হয়েছে!\n"
        f"মোট History: {len(values)}টি\n\n"
        + prediction_text(values)
    )


async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    values = load_history()

    if not values:
        await update.message.reply_text("এখনো কোনো History নেই।")
        return

    recent = list(reversed(values[-20:]))
    lines = [f"{i}. {fmt(v)}" for i, v in enumerate(recent, 1)]

    await update.message.reply_text(
        "📜 সর্বশেষ ফলাফল:\n\n" + "\n".join(lines)
    )


async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    values = load_history()

    if len(values) < 10:
        await update.message.reply_text(
            f"কমপক্ষে ১০টি ফলাফল দরকার। এখন আছে {len(values)}টি।"
        )
        return

    await update.message.reply_text(
        "📊 HISTORY ANALYSIS\n\n"
        f"Median: {fmt(median(values))}\n"
        f"Below 2x: {sum(x < 2 for x in values) / len(values) * 100:.1f}%\n"
        f"5x বা বেশি: {sum(x >= 5 for x in values) / len(values) * 100:.1f}%\n"
        f"মোট রাউন্ড: {len(values)}"
    )


async def predict(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(prediction_text(load_history()))


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
            f"{label}: {count} ({count / len(values) * 100:.1f}%)"
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

    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(CallbackQueryHandler(ocr_callback))

    app.run_polling()


if __name__ == "__main__":
    main()

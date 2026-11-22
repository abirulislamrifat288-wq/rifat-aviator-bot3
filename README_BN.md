# RIFAT AVIATOR HISTORY ANALYSIS BOT

## বটটি কী করে
- `/add 1.45` দিয়ে আগের multiplier যোগ করা যায়।
- `/history` শেষ ২০টি ফলাফল দেখায়।
- `/analyze` অতীতের median, quartile range ও সাম্প্রতিক অনুপাত দেখায়।
- `/stats` multiplier range অনুযায়ী সংখ্যা ও শতাংশ দেখায়।
- `/clear` history মুছে দেয়।

## চালানোর নিয়ম
1. Telegram-এ `@BotFather` খুলে `/newbot` পাঠাও।
2. BotFather থেকে পাওয়া token কপি করো।
3. কম্পিউটারে Python 3.12+ ইনস্টল করো।
4. টার্মিনালে চালাও:
   `pip install -r requirements.txt`
5. `TELEGRAM_BOT_TOKEN` নামে environment variable-এ token সেট করো।
   Windows PowerShell:
   `$env:TELEGRAM_BOT_TOKEN="তোমার_টোকেন"`
6. চালাও: `python bot.py`

## Render-এ চালানো
GitHub-এ ফাইলগুলো upload করে Render-এ New Blueprint নির্বাচন করো, তারপর
`TELEGRAM_BOT_TOKEN` secret হিসেবে দাও। Persistent disk ছাড়া history restart-এর
পর হারিয়ে যেতে পারে; render.yaml-এ disk কনফিগার করা আছে।

## গুরুত্বপূর্ণ সীমাবদ্ধতা
Aviator-এর পরের multiplier আগের history থেকে নির্ভরযোগ্যভাবে জানা যায় না।
এই বট prediction বা betting signal দেয় না; শুধু দেওয়া historical data-র
পরিসংখ্যান ও অতীতের reference range দেখায়। কোনো wager করার নিশ্চয়তা নেই।

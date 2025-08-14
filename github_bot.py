#نسخه ۳
cat > /root/github_bot/github_bot.py << 'EOF'
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)
from io import BytesIO

# ========================
TOKEN = "8386392184:AAHSnMDyrB7w3-q6xI3dam44SaGi8FG6LhY"
CHAT_ID = "8062924341"
# ========================

GITHUB_REPO = "rezajavadi995/Sshmanagerbot"

# فایل‌هایی که در فایل نهایی گنجانده نشوند
EXCLUDE_FILES = ["Sshmanagerbot.py", "example_ignore.py", "README.md", 
                 "updater_bot.py", "مسیر ها", 
                 "اجرای سکریپت راه اندازی کامل سرور.sh"]

# مرحله مکالمه افزودن فایل
ADD_EXCLUDE = 1

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("دریافت فایل‌ها از گیت‌هاب", callback_data='fetch_files')],
        [InlineKeyboardButton("نمایش فایل‌های استثنا", callback_data='show_exclude')],
        [InlineKeyboardButton("افزودن فایل به استثنا", callback_data='add_exclude')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("سلام! یکی از گزینه‌ها را انتخاب کن:", reply_markup=reply_markup)

def fetch_files_recursive(path=""):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    response = requests.get(url)
    if response.status_code != 200:
        return []

    items = response.json()
    all_files = []

    for f in items:
        if f['name'] in EXCLUDE_FILES:
            continue
        if f['type'] == 'file':
            all_files.append((f['path'], f['download_url']))
        elif f['type'] == 'dir':
            all_files.extend(fetch_files_recursive(f['path']))
    return all_files

async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == 'fetch_files':
        await query.edit_message_text("در حال جمع‌آوری فایل‌ها...")
        files = fetch_files_recursive()
        if not files:
            await query.edit_message_text("هیچ فایلی پیدا نشد یا دسترسی مشکل دارد.")
            return

        content_txt = ""
        for path, url in files:
            file_resp = requests.get(url)
            if file_resp.status_code == 200:
                content_txt += f"\n\n===== {path} =====\n\n"
                content_txt += file_resp.text
            else:
                content_txt += f"\n\n===== {path} =====\n\nخطا در دریافت فایل!\n"

        file_bytes = BytesIO(content_txt.encode("utf-8"))
        file_bytes.name = "github_files.txt"
        await context.bot.send_document(chat_id=CHAT_ID, document=file_bytes)
        await query.edit_message_text("تمام فایل‌ها ارسال شد.")

    elif query.data == 'show_exclude':
        message = "فایل‌های استثنا:\n" + "\n".join(EXCLUDE_FILES)
        await query.edit_message_text(message)

    elif query.data == 'add_exclude':
        await query.edit_message_text("نام فایل مورد نظر برای افزودن به استثنا را ارسال کن:")
        return ADD_EXCLUDE

async def add_exclude_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file_name = update.message.text.strip()
    if file_name in EXCLUDE_FILES:
        await update.message.reply_text(f"{file_name} قبلاً در استثناها بود.")
    else:
        EXCLUDE_FILES.append(file_name)
        await update.message.reply_text(f"{file_name} به لیست استثنا اضافه شد!")

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("عملیات لغو شد.")
    return ConversationHandler.END

app = ApplicationBuilder().token(TOKEN).build()

# مکالمه افزودن فایل
conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(handle_button, pattern='add_exclude')],
    states={
        ADD_EXCLUDE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_exclude_file)]
    },
    fallbacks=[CommandHandler('cancel', cancel)]
)

app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(handle_button, pattern='^(fetch_files|show_exclude)$'))
app.add_handler(conv_handler)

print("ربات آماده است.")
app.run_polling()
EOF

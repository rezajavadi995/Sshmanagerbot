#cat > /root/updater_bot.py << 'EOF'
import os
import subprocess
import traceback
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

ADMIN_ID = 8062924341
REPO_PATH = "/root/sshmanager_repo"

# مراحل مکالمه اضافه کردن فایل جدید و ویرایش/حذف
ASK_NAME, ASK_SOURCE, ASK_DEST, ASK_SERVICE, ASK_CHMOD = range(5)
EDIT_CHOOSE_FIELD, EDIT_NEW_VALUE, CONFIRM_DELETE = range(5, 8)

FILES_AND_SERVICES = {
    "Sshmanagerbot.py": {
        "source": f"{REPO_PATH}/Sshmanagerbot.py",
        "dest": "/root/sshmanagerbot.py",
        "service": "sshmanagerbot.service",
        "chmod": None,
    },
    "check_user_usage.py": {
        "source": f"{REPO_PATH}/check_user_usage.py",
        "dest": "/usr/local/bin/check_user_usage.py",
        "service": None,
        "chmod": None,
    },
    "check_users_expire.py": {
        "source": f"{REPO_PATH}/check_users_expire.py",
        "dest": "/usr/local/bin/check_users_expire.py",
        "service": None,
        "chmod": None,
    },
    "lock_user.py": {
        "source": f"{REPO_PATH}/lock_user.py",
        "dest": "/root/sshmanager/lock_user.py",
        "service": None,
        "chmod": None,
    },
    "log_user_traffic.py": {
        "source": f"{REPO_PATH}/log_user_traffic.py",
        "dest": "/usr/local/bin/log_user_traffic.py",
        "service": None,
        "chmod": None,
    },
}

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

def fix_service_name(svc):
    if svc is None:
        return None
    svc = svc.strip()
    if svc.lower() == "none" or svc == "":
        return None
    if not svc.endswith(".service"):
        svc += ".service"
    return svc

# نمایش منوی اصلی دکمه‌ها
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    keyboard = [
        [
            InlineKeyboardButton(f"⭕️ آپدیت {name}", callback_data=f"update_{name}"),
            InlineKeyboardButton("✏️ ویرایش", callback_data=f"edit_{name}"),
            InlineKeyboardButton("❌ حذف", callback_data=f"delete_{name}")
        ]
        for name in FILES_AND_SERVICES.keys()
    ]
    keyboard.append([InlineKeyboardButton("➕ افزودن فایل جدید", callback_data="add_new_file")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "کدام فایل را می‌خواهید آپدیت، ویرایش یا حذف کنید؟",
        reply_markup=reply_markup,
    )

# هندلر کلی دکمه‌ها
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not is_admin(user_id):
        await query.edit_message_text("شما ادمین نیستید.")
        return ConversationHandler.END

    data = query.data

    # افزودن فایل جدید
    if data == "add_new_file":
        await query.edit_message_text("مرحله 1/5\nلطفاً نام فایل را وارد کنید (مثلاً my_script.py):")
        return ASK_NAME

    # آپدیت فایل
    if data.startswith("update_"):
        filename = data[len("update_"):]
        info = FILES_AND_SERVICES.get(filename)
        if not info:
            await query.edit_message_text("فایل مورد نظر یافت نشد.")
            return ConversationHandler.END
        try:
            git_pull = subprocess.run(
                ["git", "-C", REPO_PATH, "pull"], capture_output=True, text=True
            )
            if git_pull.returncode != 0:
                await query.edit_message_text(f"❌ خطا در دریافت آخرین تغییرات از گیت:\n{git_pull.stderr}")
                return ConversationHandler.END

            cp_cmd = subprocess.run(
                ["cp", info["source"], info["dest"]], capture_output=True, text=True
            )
            if cp_cmd.returncode != 0:
                await query.edit_message_text(f"❌ خطا در کپی فایل:\n{cp_cmd.stderr}")
                return ConversationHandler.END

            chmod_output = ""
            if info.get("chmod") and info["chmod"].lower() != "none" and info["chmod"] is not None:
                chmod_cmd = subprocess.run(
                    ["chmod", info["chmod"], info["dest"]], capture_output=True, text=True
                )
                if chmod_cmd.returncode != 0:
                    await query.edit_message_text(f"❌ خطا در اعمال chmod:\n{chmod_cmd.stderr}")
                    return ConversationHandler.END
                chmod_output = f"chmod output:\n{chmod_cmd.stdout}{chmod_cmd.stderr}\n"

            systemctl_output = ""
            svc_name = fix_service_name(info.get("service"))
            if svc_name:
                check_cmd = subprocess.run(
                    ["systemctl", "status", svc_name], capture_output=True, text=True
                )
                if check_cmd.returncode != 0:
                    await query.edit_message_text(f"❌ سرویس {svc_name} وجود ندارد یا فعال نیست:\n{check_cmd.stderr}")
                    return ConversationHandler.END

                subprocess.run(["systemctl", "daemon-reload"], check=True)
                restart_cmd = subprocess.run(
                    ["systemctl", "restart", svc_name], capture_output=True, text=True
                )
                enable_cmd = subprocess.run(
                    ["systemctl", "enable", svc_name], capture_output=True, text=True
                )
                if restart_cmd.returncode != 0:
                    await query.edit_message_text(
                        f"❌ خطا در ریستارت سرویس {svc_name}:\n{restart_cmd.stderr}"
                    )
                    return ConversationHandler.END
                systemctl_output = (
                    f"systemctl restart output:\n{restart_cmd.stdout}{restart_cmd.stderr}\n"
                    f"systemctl enable output:\n{enable_cmd.stdout}{enable_cmd.stderr}\n"
                )

            msg = f"✅ فایل {filename} با موفقیت آپدیت شد.\n\n"
            msg += f"git pull output:\n{git_pull.stdout}{git_pull.stderr}\n"
            msg += f"cp output:\n{cp_cmd.stdout}{cp_cmd.stderr}\n"
            if chmod_output:
                msg += chmod_output
            if systemctl_output:
                msg += systemctl_output

            await query.edit_message_text(msg)
        except Exception:
            await query.edit_message_text(f"❌ خطا در آپدیت فایل {filename}:\n{traceback.format_exc()}")
        return ConversationHandler.END

    # حذف فایل
    if data.startswith("delete_"):
        filename = data[len("delete_"):]
        if filename not in FILES_AND_SERVICES:
            await query.edit_message_text("فایل مورد نظر یافت نشد.")
            return ConversationHandler.END
        context.user_data["delete_file"] = filename
        await query.edit_message_text(
            f"آیا مطمئن هستید که می‌خواهید فایل '{filename}' را حذف کنید؟\n\n"
            f"برای تایید 'بله' را ارسال کنید یا /cancel را برای لغو."
        )
        return CONFIRM_DELETE

    # ویرایش فایل
    if data.startswith("edit_"):
        filename = data[len("edit_"):]
        if filename not in FILES_AND_SERVICES:
            await query.edit_message_text("فایل مورد نظر یافت نشد.")
            return ConversationHandler.END
        context.user_data["edit_file"] = filename

        buttons = [
            [InlineKeyboardButton("نام فایل", callback_data="edit_name")],
            [InlineKeyboardButton("مسیر منبع", callback_data="edit_source")],
            [InlineKeyboardButton("مسیر مقصد", callback_data="edit_dest")],
            [InlineKeyboardButton("سرویس systemd", callback_data="edit_service")],
            [InlineKeyboardButton("سطح دسترسی (chmod)", callback_data="edit_chmod")],
            [InlineKeyboardButton("بازگشت به منوی اصلی", callback_data="main_menu")],
        ]
        reply_markup = InlineKeyboardMarkup(buttons)

        await query.edit_message_text(
            f"مشخصات فعلی فایل '{filename}':\n"
            f"نام: {filename}\n"
            f"مسیر منبع: {FILES_AND_SERVICES[filename]['source']}\n"
            f"مسیر مقصد: {FILES_AND_SERVICES[filename]['dest']}\n"
            f"سرویس: {FILES_AND_SERVICES[filename]['service']}\n"
            f"chmod: {FILES_AND_SERVICES[filename]['chmod']}\n\n"
            "کدام قسمت را می‌خواهید ویرایش کنید؟",
            reply_markup=reply_markup,
        )
        return EDIT_CHOOSE_FIELD

    # بازگشت به منوی اصلی
    if data == "main_menu":
        return await start(update, context)

    return ConversationHandler.END

# انتخاب فیلد برای ویرایش
async def edit_choose_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not is_admin(user_id):
        await query.edit_message_text("شما ادمین نیستید.")
        return ConversationHandler.END

    data = query.data
    filename = context.user_data.get("edit_file")
    if not filename:
        await query.edit_message_text("فایل انتخابی یافت نشد.")
        return ConversationHandler.END

    if data == "main_menu":
        return await start(update, context)

    field_map = {
        "edit_name": "name",
        "edit_source": "source",
        "edit_dest": "dest",
        "edit_service": "service",
        "edit_chmod": "chmod",
    }

    if data in field_map:
        context.user_data["edit_field"] = field_map[data]
        await query.edit_message_text(f"لطفاً مقدار جدید برای '{field_map[data]}' را ارسال کنید:")
        return EDIT_NEW_VALUE

    return ConversationHandler.END

# دریافت مقدار جدید برای ویرایش
async def edit_new_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_val = update.message.text.strip()
    filename = context.user_data.get("edit_file")
    field = context.user_data.get("edit_field")
    if not filename or not field:
        await update.message.reply_text("خطا در ویرایش. لطفا دوباره تلاش کنید.")
        return ConversationHandler.END

    if field == "service":
        new_val = fix_service_name(new_val)

    if field == "name":
        if new_val in FILES_AND_SERVICES and new_val != filename:
            await update.message.reply_text("نام جدید قبلا وجود دارد. نام دیگری انتخاب کنید:")
            return EDIT_NEW_VALUE
        FILES_AND_SERVICES[new_val] = FILES_AND_SERVICES.pop(filename)
        context.user_data["edit_file"] = new_val
        filename = new_val
    else:
        FILES_AND_SERVICES[filename][field] = new_val

    await update.message.reply_text(
        f"مقدار '{field}' با موفقیت به '{new_val}' تغییر کرد.\n\n"
        f"برای ویرایش قسمت دیگر /start را ارسال کنید یا ادامه دهید."
    )
    return ConversationHandler.END

# تایید حذف فایل
async def confirm_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower()
    filename = context.user_data.get("delete_file")
    if not filename:
        await update.message.reply_text("فایل انتخابی یافت نشد.")
        return ConversationHandler.END

    if text == "بله":
        FILES_AND_SERVICES.pop(filename, None)
        await update.message.reply_text(f"✅ فایل '{filename}' با موفقیت حذف شد.\n\nبرای مشاهده لیست جدید /start را ارسال کنید.")
    else:
        await update.message.reply_text("❌ عملیات حذف لغو شد.")

    return ConversationHandler.END

# مراحل اضافه کردن فایل جدید
async def add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    context.user_data["new_file"] = {}
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("نام فایل نمی‌تواند خالی باشد. لطفاً دوباره وارد کنید:")
        return ASK_NAME
    context.user_data["new_file"]["name"] = name
    await update.message.reply_text(
        "مرحله 2/5\nمسیر فایل منبع داخل مخزن (نسبت به پوشه repo) را وارد کنید (مثلاً scripts/my_script.py):"
    )
    return ASK_SOURCE

async def add_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    source = update.message.text.strip()
    if not source:
        await update.message.reply_text("مسیر فایل منبع نمی‌تواند خالی باشد. لطفاً دوباره وارد کنید:")
        return ASK_SOURCE
    context.user_data["new_file"]["source"] = os.path.join(REPO_PATH, source)
    await update.message.reply_text(
        "مرحله 3/5\nمسیر فایل مقصد در سرور را وارد کنید (مثلاً /usr/local/bin/my_script.py):"
    )
    return ASK_DEST

async def add_dest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dest = update.message.text.strip()
    if not dest:
        await update.message.reply_text("مسیر فایل مقصد نمی‌تواند خالی باشد. لطفاً دوباره وارد کنید:")
        return ASK_DEST
    context.user_data["new_file"]["dest"] = dest
    await update.message.reply_text("مرحله 4/5\nنام سرویس systemd (اگر ندارد 'none' وارد کنید):")
    return ASK_SERVICE

async def add_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    svc = update.message.text.strip()
    if svc.lower() == "none":
        svc = None
    context.user_data["new_file"]["service"] = svc
    await update.message.reply_text("مرحله 5/5\nسطح دسترسی فایل (مثلاً 755 یا 'none' اگر نمی‌خواهید تغییر دهید):")
    return ASK_CHMOD

async def add_chmod(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chmod = update.message.text.strip()
    if chmod.lower() == "none":
        chmod = None
    context.user_data["new_file"]["chmod"] = chmod

    new_file = context.user_data["new_file"]

    # اضافه کردن به دیکشنری اصلی
    FILES_AND_SERVICES[new_file["name"]] = {
        "source": new_file["source"],
        "dest": new_file["dest"],
        "service": new_file["service"],
        "chmod": new_file["chmod"],
    }

    keyboard = [
        [
            InlineKeyboardButton(f"⭕️ آپدیت {name}", callback_data=f"update_{name}"),
            InlineKeyboardButton("✏️ ویرایش", callback_data=f"edit_{name}"),
            InlineKeyboardButton("❌ حذف", callback_data=f"delete_{name}")
        ]
        for name in FILES_AND_SERVICES.keys()
    ]
    keyboard.append([InlineKeyboardButton("➕ افزودن فایل جدید", callback_data="add_new_file")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"✅ فایل جدید با موفقیت اضافه شد:\n\n"
        f"نام: {new_file['name']}\n"
        f"source: {new_file['source']}\n"
        f"dest: {new_file['dest']}\n"
        f"service: {new_file['service']}\n"
        f"chmod: {new_file['chmod']}\n\n"
        f"برای بازگشت به لیست دکمه‌ها، /start را ارسال کنید.",
        reply_markup=reply_markup,
    )
    return ConversationHandler.END

# لغو عملیات
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ عملیات لغو شد.")
    return ConversationHandler.END

def main():
    app = ApplicationBuilder().token("7666791827:AAH9o2QxhvT2QbzAHKjbWmDhaieDCiT1ldY").build()

    # یک ConversationHandler واحد که همه مراحل و دکمه‌ها را پوشش می‌دهد
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start), CallbackQueryHandler(button, pattern="^(add_new_file|update_|edit_|delete_|main_menu)$")],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_name)],
            ASK_SOURCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_source)],
            ASK_DEST: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_dest)],
            ASK_SERVICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_service)],
            ASK_CHMOD: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_chmod)],

            EDIT_CHOOSE_FIELD: [CallbackQueryHandler(edit_choose_field, pattern="^edit_.*|main_menu$")],
            EDIT_NEW_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_new_value)],

            CONFIRM_DELETE: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_delete)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(conv_handler)

    app.run_polling()

if __name__ == "__main__":
    main()
#EOF

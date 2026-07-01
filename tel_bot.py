from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from deep_translator import GoogleTranslator
import os
import psycopg2
from agent_main import app as ai_app

# -------------------------
# تنظیمات
# -------------------------

db_pass = os.getenv("DATABASE_PASS")

conn = psycopg2.connect(
    f"postgresql://postgres.suqjseiaqbffvdzyfbud:{db_pass}@aws-1-ap-southeast-1.pooler.supabase.com:6543/postgres?sslmode=require"
)
conn.autocommit = True

# -------------------------
# حافظه موقت (state)
# -------------------------
user_state = {}


# -------------------------
# هندل پیام‌ها
# -------------------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):

    chat_id = update.message.chat_id
    text = update.message.text.strip()

    cursor = conn.cursor()

    # ----------------------------------
    # 1. بررسی کاربر در دیتابیس
    # ----------------------------------
    cursor.execute(
        "SELECT id, name, email FROM users WHERE chat_id = %s",
        (chat_id,)
    )
    user = cursor.fetchone()

    # ----------------------------------
    # 2. اگر کاربر وجود دارد → AI
    # ----------------------------------
    if user:
        user_id = user[0]
        text2 = GoogleTranslator(source='fa', target='en').translate(text)

        result = ai_app.invoke({
            "question": text2,
            "user_id": user_id,
            "conn": conn
        })
        answer = GoogleTranslator(source='en', target='fa').translate(result["answer"])

        await update.message.reply_text(answer)
        return

    # ----------------------------------
    # 3. اگر کاربر جدید است → مدیریت state
    # ----------------------------------

    # شروع فرآیند ثبت‌نام
    if chat_id not in user_state:
        user_state[chat_id] = {"step": "name"}
        await update.message.reply_text("سلام 👋\nاسمت رو بگو:")
        return

    # گرفتن اسم
    if user_state[chat_id]["step"] == "name":
        user_state[chat_id]["name"] = text
        user_state[chat_id]["step"] = "email"

        await update.message.reply_text("ایمیلت رو وارد کن:")
        return

    # گرفتن ایمیل و ذخیره در دیتابیس
    if user_state[chat_id]["step"] == "email":
        name = user_state[chat_id]["name"]
        email = text

        cursor.execute(
            "INSERT INTO users (chat_id, name, email) VALUES (%s, %s, %s) RETURNING id",
            (chat_id, name, email)
        )

        user_id = cursor.fetchone()[0]

        # حذف state چون ثبت‌نام تموم شد
        del user_state[chat_id]

        await update.message.reply_text("✅ ثبت‌نام کامل شد! حالا هرچی میخوای بپرس 😎")
        return


# -------------------------
# اجرای ربات
# -------------------------
token = os.getenv("TEL_TOKEN")
app_tel = Application.builder().token(f"{token}").build()
app_tel.add_handler(MessageHandler(filters.TEXT, handle_message))

app_tel.run_polling()
import logging
import re
from datetime import datetime, timedelta
import pytz
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
import os

TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = int(os.environ.get("CHAT_ID"))
TIMEZONE = os.environ.get("TIMEZONE", "Europe/Bucharest")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

tz = pytz.timezone(TIMEZONE)


def parse_reminder(text: str):
    """Parse natural language Romanian/English reminder text."""
    text_lower = text.lower().strip()

    # Remove trigger phrases
    for phrase in [
        "reaminteste-mi", "reamintește-mi", "reminder", "aminteste-mi", "amintește-mi",
        "nu uita", "remind me", "set reminder", "pune reminder"
    ]:
        text_lower = text_lower.replace(phrase, "").strip()

    now = datetime.now(tz)
    target_time = None
    message = text_lower

    # --- Patterns for time ---

    # "maine la HH:MM" or "mâine la HH:MM"
    m = re.search(r"(m[aâ]ine)\s+la\s+(\d{1,2}):?(\d{2})?", text_lower)
    if m:
        hour = int(m.group(2))
        minute = int(m.group(3)) if m.group(3) else 0
        target_time = (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        message = re.sub(r"(m[aâ]ine)\s+la\s+\d{1,2}:?\d{0,2}", "", text_lower).strip()

    # "azi la HH:MM" or "astazi la HH:MM"
    if not target_time:
        m = re.search(r"(azi|ast[aă]zi|today)\s+la\s+(\d{1,2}):?(\d{2})?", text_lower)
        if m:
            hour = int(m.group(2))
            minute = int(m.group(3)) if m.group(3) else 0
            target_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target_time < now:
                target_time += timedelta(days=1)
            message = re.sub(r"(azi|ast[aă]zi|today)\s+la\s+\d{1,2}:?\d{0,2}", "", text_lower).strip()

    # "la HH:MM" (today)
    if not target_time:
        m = re.search(r"\bla\s+(\d{1,2}):(\d{2})", text_lower)
        if m:
            hour = int(m.group(1))
            minute = int(m.group(2))
            target_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target_time < now:
                target_time += timedelta(days=1)
            message = re.sub(r"\bla\s+\d{1,2}:\d{2}", "", text_lower).strip()

    # "in X minute/ore"
    if not target_time:
        m = re.search(r"[îi]n\s+(\d+)\s*(minut|minute|min|ore|ora|oră|hour|hours?)", text_lower)
        if m:
            amount = int(m.group(1))
            unit = m.group(2)
            if any(x in unit for x in ["minut", "min", "hour"]):
                target_time = now + timedelta(minutes=amount)
            else:
                target_time = now + timedelta(hours=amount)
            message = re.sub(r"[îi]n\s+\d+\s*(minut|minute|min|ore|ora|oră|hour|hours?)", "", text_lower).strip()

    # "dimineata" -> 9:00, "seara" -> 20:00, "pranz" -> 13:00
    if not target_time:
        if any(x in text_lower for x in ["diminea", "morning"]):
            target_time = now.replace(hour=9, minute=0, second=0, microsecond=0)
            if target_time < now:
                target_time += timedelta(days=1)
        elif any(x in text_lower for x in ["seara", "evening", "tonight"]):
            target_time = now.replace(hour=20, minute=0, second=0, microsecond=0)
            if target_time < now:
                target_time += timedelta(days=1)
        elif any(x in text_lower for x in ["pranz", "lunch", "amiaz"]):
            target_time = now.replace(hour=13, minute=0, second=0, microsecond=0)
            if target_time < now:
                target_time += timedelta(days=1)

    # Clean up message
    cleanup_words = [
        "sa ", "să ", "to ", "ca sa ", "că să ",
        "maine", "mâine", "azi", "astazi", "astăzi", "today",
        "dimineata", "dimineață", "seara", "pranz",
    ]
    for w in cleanup_words:
        message = message.replace(w, "")
    message = message.strip(" ,.-")

    return target_time, message if message else text


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Salut! Sunt Valet, asistentul tău personal.\n\n"
        "Îmi poți spune lucruri de genul:\n"
        "• *reamintește-mi mâine la 11 să cumpăr pâine*\n"
        "• *reminder în 30 minute să sun la doctor*\n"
        "• *amintește-mi azi la 18:00 să plătesc factura*\n\n"
        "Scrie /lista să vezi toate reminderele active.",
        parse_mode="Markdown"
    )


async def lista(update: Update, context: ContextTypes.DEFAULT_TYPE):
    jobs = context.job_queue.jobs()
    if not jobs:
        await update.message.reply_text("📭 Nu ai niciun reminder activ.")
        return

    text = "📋 *Remindere active:*\n\n"
    for i, job in enumerate(jobs, 1):
        run_time = job.next_t.astimezone(tz).strftime("%d.%m.%Y %H:%M")
        text += f"{i}. ⏰ {run_time} — {job.name}\n"

    await update.message.reply_text(text, parse_mode="Markdown")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    target_time, reminder_message = parse_reminder(text)

    if not target_time:
        await update.message.reply_text(
            "🤔 Nu am înțeles când să îți amintesc. Încearcă de genul:\n"
            "*reamintește-mi mâine la 11 să cumpăr pâine*",
            parse_mode="Markdown"
        )
        return

    now = datetime.now(tz)
    if target_time < now:
        await update.message.reply_text("⚠️ Ora respectivă a trecut deja. Încearcă altă oră.")
        return

    delay = (target_time - now).total_seconds()
    readable_time = target_time.strftime("%d.%m.%Y la %H:%M")

    context.job_queue.run_once(
        send_reminder,
        when=delay,
        chat_id=update.effective_chat.id,
        name=reminder_message,
        data=reminder_message,
    )

    await update.message.reply_text(
        f"✅ Reminder setat!\n⏰ Te anunț pe *{readable_time}*\n📝 _{reminder_message}_",
        parse_mode="Markdown"
    )


async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    await context.bot.send_message(
        chat_id=job.chat_id,
        text=f"🔔 *Reminder!*\n\n➡️ {job.data}",
        parse_mode="Markdown"
    )


def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("lista", lista))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Valet Bot pornit!")
    app.run_polling()


if __name__ == "__main__":
    main()

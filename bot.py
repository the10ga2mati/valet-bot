import logging, re, json, os, random, string
from datetime import datetime, timedelta
import pytz
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = int(os.environ.get("CHAT_ID"))
TIMEZONE = os.environ.get("TIMEZONE", "Europe/Bucharest")
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
tz = pytz.timezone(TIMEZONE)
LISTS_FILE = "/tmp/lists.json"
HISTORY_FILE = "/tmp/history.json"

def load_json(path):
    try:
        with open(path) as f: return json.load(f)
    except: return {}

def save_json(path, data):
    with open(path, "w") as f: json.dump(data, f, ensure_ascii=False, indent=2)

async def ask_claude(user_message: str, chat_id: int) -> str:
    history = load_json(HISTORY_FILE)
    user_history = history.get(str(chat_id), [])
    lists = load_json(LISTS_FILE)
    now = datetime.now(tz).strftime("%d.%m.%Y %H:%M")
    ctx = f"Data si ora: {now}\n"
    if lists:
        ctx += "Liste cumparaturi:\n"
        for store, items in lists.items():
            ctx += f"- {store}: {', '.join(items) if items else 'goala'}\n"
    system_prompt = f"""Esti ValetBot, asistentul personal al lui Cosmin. Vorbesti in romana, esti prietenos si concis.

{ctx}

Poti ajuta cu:
1. REMINDER - Cand vrea un reminder, include in raspuns: {{REMINDER: "descriere", TIME: "HH:MM", DATE: "DD.MM.YYYY"}}
2. LISTE - Cand vrea sa gestioneze liste: {{LIST_ADD: "produs", STORE: "magazin"}} sau {{LIST_SHOW: "magazin"}} sau {{LIST_RESET: "magazin"}}
3. CONVERSATIE - Raspunde natural la orice intrebare.

Include JSON-ul in raspuns fara sa-l explici."""
    messages = user_history[-10:] + [{"role": "user", "content": user_message}]
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": CLAUDE_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": "claude-haiku-4-5", "max_tokens": 1024, "system": system_prompt, "messages": messages}
            )
            data = resp.json()
            logger.info(f"Claude response status: {resp.status_code}")
            if resp.status_code != 200:
                logger.error(f"Claude error: {data}")
                return "Scuze, problema tehnica. Incearca din nou!"
            msg = data["content"][0]["text"]
            user_history.append({"role": "user", "content": user_message})
            user_history.append({"role": "assistant", "content": msg})
            history[str(chat_id)] = user_history[-20:]
            save_json(HISTORY_FILE, history)
            return msg
    except Exception as e:
        logger.error(f"Claude API error: {e}")
        return f"Eroare: {str(e)[:100]}"

def parse_response(response):
    text = response
    actions = []
    m = re.search(r'\{REMINDER:\s*"([^"]+)",\s*TIME:\s*"(\d{2}:\d{2})",\s*DATE:\s*"(\d{2}\.\d{2}\.\d{4})"\}', response)
    if m:
        actions.append(("reminder", m.group(1), m.group(2), m.group(3)))
        text = re.sub(r'\{REMINDER:[^}]+\}', '', text).strip()
    m = re.search(r'\{LIST_ADD:\s*"([^"]+)",\s*STORE:\s*"([^"]+)"\}', response)
    if m:
        actions.append(("list_add", m.group(1), m.group(2).lower()))
        text = re.sub(r'\{LIST_ADD:[^}]+\}', '', text).strip()
    m = re.search(r'\{LIST_SHOW:\s*"([^"]+)"\}', response)
    if m:
        actions.append(("list_show", m.group(1).lower()))
        text = re.sub(r'\{LIST_SHOW:[^}]+\}', '', text).strip()
    m = re.search(r'\{LIST_RESET:\s*"([^"]+)"\}', response)
    if m:
        actions.append(("list_reset", m.group(1).lower()))
        text = re.sub(r'\{LIST_RESET:[^}]+\}', '', text).strip()
    return text.strip(), actions

async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    await context.bot.send_message(chat_id=job.chat_id, text=f"Reminder!\n\n{job.data}", parse_mode="Markdown")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("|")
    if parts[0] == "check":
        store, idx = parts[1], int(parts[2])
        ls = load_json(LISTS_FILE)
        if store in ls and idx < len(ls[store]):
            item = ls[store].pop(idx)
            save_json(LISTS_FILE, ls)
            await query.edit_message_text(f"*{item.capitalize()}* bifat!", parse_mode="Markdown")
    elif parts[0] == "reset":
        store = parts[1]
        ls = load_json(LISTS_FILE)
        if store in ls: ls[store] = []
        save_json(LISTS_FILE, ls)
        await query.edit_message_text(f"Lista *{store.capitalize()}* golita!", parse_mode="Markdown")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Salut! Sunt *ValetBot* cu AI!\n\nVorbeste-mi natural in romana:\n- Reaminteste-mi diseara la 18:00 sa sun\n- Adauga lapte pe lista Lidl\n- Orice intrebare\n\nSunt conectat la Claude AI!", parse_mode="Markdown")

async def cmd_liste(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ls = load_json(LISTS_FILE)
    if not ls: await update.message.reply_text("Nu ai nicio lista."); return
    text = "*Listele tale:*\n\n"
    for store, items in ls.items():
        text += f"{'OK' if items else '--'} *{store.capitalize()}* - {len(items)} produse\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    claude_response = await ask_claude(user_message, chat_id)
    text, actions = parse_response(claude_response)
    for action in actions:
        if action[0] == "reminder":
            _, desc, time_str, date_str = action
            try:
                dt = tz.localize(datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M"))
                delay = (dt - datetime.now(tz)).total_seconds()
                if delay > 0:
                    context.job_queue.run_once(send_reminder, when=delay, chat_id=chat_id, name=desc, data=desc)
            except Exception as e: logger.error(f"Reminder error: {e}")
        elif action[0] == "list_add":
            _, item, store = action
            ls = load_json(LISTS_FILE)
            if store not in ls: ls[store] = []
            ls[store].append(item); save_json(LISTS_FILE, ls)
        elif action[0] == "list_show":
            _, store = action
            items = load_json(LISTS_FILE).get(store, [])
            if items:
                num = "\n".join(f"{i+1}. {it}" for i, it in enumerate(items))
                kb = [[InlineKeyboardButton(f"OK {it}", callback_data=f"check|{store}|{idx}")] for idx, it in enumerate(items)]
                kb.append([InlineKeyboardButton("Goleste", callback_data=f"reset|{store}")])
                await update.message.reply_text(f"*Lista {store.capitalize()}:*\n\n{num}", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
        elif action[0] == "list_reset":
            ls = load_json(LISTS_FILE); ls[action[1]] = []; save_json(LISTS_FILE, ls)
    if text:
        try: await update.message.reply_text(text, parse_mode="Markdown")
        except: await update.message.reply_text(text)

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("liste", cmd_liste))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("ValetBot AI pornit!")
    app.run_polling()

if __name__ == "__main__": main()

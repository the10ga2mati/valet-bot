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
SHARED_FILE = "/tmp/shared_lists.json"
HISTORY_FILE = "/tmp/history.json"

def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_context_for_claude():
    lists = load_json(LISTS_FILE)
    shared = load_json(SHARED_FILE)
    now = datetime.now(tz).strftime("%d.%m.%Y %H:%M")
    ctx = f"Data si ora curenta: {now} (fusul orar: Europe/Bucharest)\n\n"
    if lists:
        ctx += "Liste de cumparaturi ale utilizatorului:\n"
        for store, items in lists.items():
            ctx += f"- {store.capitalize()}: {', '.join(items) if items else '(goala)'}\n"
        ctx += "\n"
    if shared:
        ctx += "Liste partajate:\n"
        for code, lst in shared.items():
            ctx += f"- {lst['name'].capitalize()} (cod: {code}): {', '.join(lst['items']) if lst['items'] else '(goala)'}\n"
        ctx += "\n"
    return ctx

async def ask_claude(user_message: str, chat_id: int) -> str:
    history = load_json(HISTORY_FILE)
    user_history = history.get(str(chat_id), [])
    context = get_context_for_claude()
    system_prompt = f"""Esti ValetBot, asistentul personal inteligent al lui Cosmin. Vorbesti in romana, esti prietenos si concis.

{context}

Poti ajuta cu:
1. REMINDER-URI - Cand utilizatorul vrea un reminder, raspunde cu un JSON special in mesajul tau:
   {{REMMINDER: "descriere", TIME: "HH:MM", DATE: "DD.MM.YYYY"}}
   Exemplu: Daca zicii "reaminteste-mi diseara la 18:00 sa sun la hidroelectrica", raspunzi normal SI incluzi {{REMINDER: "suna la hidroelectrica", TIME: "18:00", DATE: "23.04.2026"}}

2. LISTE - Cand utilizatorul vrea sa adauge/sterga/vada liste, raspunde cu JSON special:
   {{LIST_ADD: "produs", STORE: "magazin"}}
   {{LIST_SHOW: "magazin"}}
   {{LIST_RESET: "magazin"}}
   {{LIST_REMOVE: "produs", STORE: "magazin"}}

3. CONVERSATIE GENERALA - Raspunde natural la orice alta intrebare.

IMPORTANT: Poti include atat text normal CAT SI JSON-ul special in acelasi raspuns. Nu explica ce faci cu JSON-ul."""
    messages = user_history[-10:] + [{"role": "user", "content": user_message}]
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": CLAUDE_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                },
                json={
                    "model": "claude-hanku-4-5",
                    "max_tokens": 1024,
                    "system": system_prompt,
                    "messages": messages
                }
            )
            data = response.json()
            assistant_message = data["content"][0]["text"]
            user_history.append({"role": "user", "content": user_message})
            user_history.append({"role": "assistant", "content": assistant_message})
            history[str(chat_id)] = user_history[-20:]
            save_json(HISTORY_FILE, history)
            return assistant_message
    except Exception as e:
        logger.error(f"Claude API error: {e}")
        return "Scuze, am o problema tehnica momentan. Incearca din nou!"

def parse_claude_response(response: str, context):
    text_to_show = response
    actions = []
    reminder_match = re.search(r'\{REMINDER:\s*"([^"]+)",\s*TIME:\s*"(\d{2}:\d{2})",\s*DATE:\s*"(\d{2}\.\d{2}\.\d{4})"\}', response)
    if reminder_match:
        actions.append(("reminder", reminder_match.group(1), reminder_match.group(2), reminder_match.group(3)))
        text_to_show = re.sub(r'\{REMINDER:[^}]+\}', '', text_to_show).strip()
    list_add_match = re.search(r'\{LIST_ADD:\s*"([^"]+)",\s*STORE:\s*"([^"]+)"\}', response)
    if list_add_match:
        actions.append(("list_add", list_add_match.group(1), list_add_match.group(2).lower()))
        text_to_show = re.sub(r'\{LIST_ADD:[^}]+\}', '', text_to_show).strip()
    list_show_match = re.search(r'\{LIST_SHOW:\s*"([^"]+)"\}', response)
    if list_show_match:
        actions.append(("list_show", list_show_match.group(1).lower()))
        text_to_show = re.sub(r'\{LIST_SHOW:[^}]+\}', '', text_to_show).strip()
    list_reset_match = re.search(r'\{LIST_RESET:\s*"([^"]+)"\}', response)
    if list_reset_match:
        actions.append(("list_reset", list_reset_match.group(1).lower()))
        text_to_show = re.sub(r'\{LIST_RESET:[^}]+\}', '', text_to_show).strip()
    list_remove_match = re.search(r'\{LIST_REMOVE:\s*"([^"]+)",\s*STORE:\s*"([^"]+)"\}', response)
    if list_remove_match:
        actions.append(("list_remove", list_remove_match.group(1), list_remove_match.group(2).lower()))
        text_to_show = re.sub(r'\{LIST_REMOVE:[^}]+\}', '', text_to_show).strip()
    return text_to_show.strip(), actions

async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    await context.bot.send_message(chat_id=job.chat_id, text=f"ð *Reminder!*\n\nâ¡ï¸ {job.data}", parse_mode="Markdown")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("|")
    if parts[0] == "check":
        store, idx = parts[1], int(parts[2])
        lists = load_json(LISTS_FILE)
        if store in lists and idx < len(lists[store]):
            item = lists[store].pop(idx)
            save_json(LISTS_FILE, lists)
            await query.edit_message_text(f"â *{item.capitalize()}* bifat!", parse_mode="Markdown")
    elif parts[0] == "reset":
        store = parts[1]
        lists = load_json(LISTS_FILE)
        if store in lists: lists[store] = []; save_json(LISTS_FILE, lists)
        await query.edit_message_text(f"Lista *{store.capitalize()}* golita!", parse_mode="Markdown")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Salut! Sunt *ValetBot* cu AI!\n\nVorbeste-mi natural, in romana:\n\nâ° Reaminteste-mi diseara la 18:00 sa sun la Hidroelectrica\nð Adauga lapte pe lista Lidl\nð¬ Orice intrebare\n\nSunt conectat la Claude AI! ð§ ", parse_mode="Markdown")

async def cmd_liste(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lists = load_json(LISTS_FILE)
    if not lists: await update.message.reply_text("Nu ai nicio lista."); return
    text = "*Listele tale:*\n\n"
    for store, items in lists.items():
        text += f"{'ã¢' if items else 'â¬'} *{store.capitalize()}* - {len(items)} produse\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    claude_response = await ask_claude(user_message, chat_id)
    text_to_show, actions = parse_claude_response(claude_response, context)
    for action in actions:
        if action[0] == "reminder":
            _, desc, time_str, date_str = action
            try:
                dt = tz.localize(datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M"))
                delay = (dt - datetime.now(tz)).total_seconds()
                if delay > 0: context.job_queue.run_once(send_reminder, when=delay, chat_id=chat_id, name=desc, data=desc)
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
                kb = [[InlineKeyboardButton(f"â {it}", callback_data=f"check|{store}|{idx}")] for idx, it in enumerate(items)]
                kb.append([InlineKeyboardButton("Goleste lista", callback_data=f"reset|{store}")])
                await update.message.reply_text(f"*Lista {store.capitalize()}:*\n\n{nem}\n\nApasa pe produs cand l-ai luat!", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
        elif action[0] == "list_reset":
            ls = load_json(LISTS_FILE); ls[action[1]] = []; save_json(LISTS_FILE, ls)
        elif action[0] == "list_remove":
            _, item, store = action
            ls = load_json(LISTS_FILE); lst = ls.get(store, [])
            m = [x for x in lst if item.lower() in x.lower()]
            if m: ls[store].remove(m[0]); save_json(LISTS_FILE, ls)
    if text_to_show:
        try: await update.message.reply_text(text_to_show, parse_mode="Markdown")
        except: await update.message.reply_text(text_to_show)

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("liste", cmd_liste))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("ValetBot AI pornit!")
    app.run_polling()

if __name__ == "__main__": main()

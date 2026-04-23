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
   {{REMINDER: "descriere", TIME: "HH:MM", DATE: "DD.MM.YYYY"}}
   Exemplu: Daca zice "reaminteste-mi diseara la 18:00 sa sun la hidroelectrica", raspunzi normal SI incluzi {{REMINDER: "suna la hidroelectrica", TIME: "18:00", DATE: "23.04.2026"}}

2. LISTE - Cand utilizatorul vrea sa adauge/sterga/vada liste, raspunde cu JSON special:
   {{LIST_ADD: "produs", STORE: "magazin"}} - pentru adaugare
   {{LIST_SHOW: "magazin"}} - pentru afisare
   {{LIST_RESET: "magazin"}} - pentru resetare
   {{LIST_REMOVE: "produs", STORE: "magazin"}} - pentru stergere

3. CONVERSATIE GENERALA - Raspunde natural la orice alta intrebare.

IMPORTANT: Poti include atat text normal CAT SI JSON-ul special in acelasi raspuns. Textul normal va fi afisat utilizatorului, JSON-ul va fi procesat automat.
Nu explica ce faci cu JSON-ul, doar include-l natural in raspuns."""

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
                    "model": "claude-hanku-4-5-20251001",
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
        desc = reminder_match.group(1)
        time_str = reminder_match.group(2)
        date_str = reminder_match.group(3)
        actions.append(("reminder", desc, time_str, date_str))
        text_to_show = re.sub(r'\{REMINDER:[^}]+\}', '', text_to_show).strip()

    list_add_match = re.search(r'\{LIST_ADD:\s*"([^"]+)",\s*STORE:\s*"([^"]+)"\}', response)
    if list_add_match:
        item = list_add_match.group(1)
        store = list_add_match.group(2).lower()
        actions.append(("list_add", item, store))
        text_to_show = re.sub(r'\{LIST_ADD:[^}]+\}', '', text_to_show).strip()

    list_show_match = re.search(r'\{LIST_SHOW:\s*"([^"]+)"\}', response)
    if list_show_match:
        store = list_show_match.group(1).lower()
        actions.append(("list_show", store))
        text_to_show = re.sub(r'\{LIST_SHOW:[^}]+\}', '', text_to_show).strip()

    list_reset_match = re.search(r'\{LIST_RESET:\s*"([^"]+)"\}', response)
    if list_reset_match:
        store = list_reset_match.group(1).lower()
        actions.append(("list_reset", store))
        text_to_show = re.sub(r'\{LIST_RESET:[^}]+\}', '', text_to_show).strip()

    list_remove_match = re.search(r'\{LIST_REMOVE:\s*"([^"]+)",\s*STORE:\s*"([^"]+)"\}', response)
    if list_remove_match:
        item = list_remove_match.group(1)
        store = list_remove_match.group(2).lower()
        actions.append(("list_remove", item, store))
        text_to_show = re.sub(r'\{LIST_REMOVE:[^}]+\}', '', text_to_show).strip()

    return text_to_show.strip(), actions

async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    await context.bot.send_message(
        chat_id=job.chat_id,
        text=f"ð *Reminder!*\n\nâ¡ï¸ {job.data}",
        parse_mode="Markdown"
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("|")
    action = parts[0]
    if action == "check":
        store, idx = parts[1], int(parts[2])
        lists = load_json(LISTS_FILE)
        if store in lists and idx < len(lists[store]):
            item = lists[store].pop(idx)
            save_json(LISTS_FILE, lists)
            await query.edit_message_text(f"â *{item.capitalize()}* bifat!", parse_mode="Markdown")
    elif action == "reset":
        store = parts[1]
        lists = load_json(LISTS_FILE)
        if store in lists:
            lists[store] = []
            save_json(LISTS_FILE, lists)
        await query.edit_message_text(f"ðï¸ Lista *{store.capitalize()}* golita!", parse_mode="Markdown")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ð Salut! Sunt *ValetBot*, asistentul tau personal cu AI!\n\n"
        "Poti sa-mi vorbesti *natural*, in romana:\n\n"
        "â° _Reaminteste-mi diseara la 18:00 sa sun la Hidroelectrica_\n"
        "ð _Adauga lapte si paine pe lista Lidl_\n"
        "ð _Arata-mi lista de la Kaufland_\n"
        "ð¬ _Orice alta intrebare sau conversatie_\n\n"
        "Sunt conectat la Claude AI si te inteleg! ð§ ",
        parse_mode="Markdown"
    )

async def cmd_liste(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lists = load_json(LISTS_FILE)
    if not lists:
        await update.message.reply_text("Nu ai nicio lista. Spune-mi ce vrei sa adaugi!")
        return
    text = "ð *Listele tale:*\n\n"
    for store, items in lists.items():
        e = "ð¢" if items else "â¬"
        text += f"{e} *{store.capitalize()}* - {len(items)} produse\n"
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
                dt = datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")
                dt = tz.localize(dt)
                now = datetime.now(tz)
                delay = (dt - now).total_seconds()
                if delay > 0:
                    context.job_queue.run_once(
                        send_reminder,
                        when=delay,
                        chat_id=chat_id,
                        name=desc,
                        data=desc
                    )
            except Exception as e:
                logger.error(f"Reminder error: {e}")

        elif action[0] == "list_add":
            _, item, store = action
            lists = load_json(LISTS_FILE)
            if store not in lists:
                lists[store] = []
            lists[store].append(item)
            save_json(LISTS_FILE, lists)

        elif action[0] == "list_show":
            _, store = action
            lists = load_json(LISTS_FILE)
            items = lists.get(store, [])
            if items:
                num = "\n".join(f"{i+1}. {it}" for i, it in enumerate(items))
                kb = [[InlineKeyboardButton(f"â {it}", callback_data=f"check|{store}|{idx}")] for idx, it in enumerate(items)]
                kb.append([InlineKeyboardButton("ðï¸ Goleste lista", callback_data=f"reset|{store}")])
                await update.message.reply_text(
                    f"ð *Lista {store.capitalize()}:*\n\n{num}\n\n_Apasa pe produs cand l-ai luat!_",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(kb)
                )

        elif action[0] == "list_reset":
            _, store = action
            lists = load_json(LISTS_FILE)
            lists[store] = []
            save_json(LISTS_FILE, lists)

        elif action[0] == "list_remove":
            _, item, store = action
            lists = load_json(LISTS_FILE)
            lst = lists.get(store, [])
            matches = [x for x in lst if item.lower() in x.lower()]
            if matches:
                lists[store].remove(matches[0])
                save_json(LISTS_FILE, lists)

    if text_to_show:
        await update.message.reply_text(text_to_show, parse_mode="Markdown")

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("liste", cmd_liste))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("ValetBot AI pornit!")
    app.run_polling()

if __name__ == "__main__":
    main()

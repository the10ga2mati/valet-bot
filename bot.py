import logging, re, json, os, random, string
from datetime import datetime, timedelta
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = int(os.environ.get("CHAT_ID"))
TIMEZONE = os.environ.get("TIMEZONE", "Europe/Bucharest")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
tz = pytz.timezone(TIMEZONE)
LISTS_FILE = "/tmp/lists.json"
SHARED_FILE = "/tmp/shared_lists.json"

def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def parse_reminder(text):
    tl = text.lower().strip()
    for p in ["reaminteste-mi","reaminteste mi","reaminteste","reminder",
              "aminteste-mi","aminteste mi","aminteste","nu uita","remind me","pune reminder"]:
        tl = tl.replace(p, "").strip()
    now = datetime.now(tz)
    tt = None
    msg = tl
    m = re.search(r"(maine)\s+la\s+(\d{1,2}):?(\d{2})?", tl)
    if m:
        h = int(m.group(2))
        mi = int(m.group(3)) if m.group(3) else 0
        tt = (now + timedelta(days=1)).replace(hour=h, minute=mi, second=0, microsecond=0)
        msg = re.sub(r"(maine)\s+la\s+\d{1,2}:?\d{0,2}", "", tl).strip()
    if not tt:
        m = re.search(r"(azi|astazi|today)\s+la\s+(\d{1,2}):?(\d{2})?", tl)
        if m:
            h = int(m.group(2))
            mi = int(m.group(3)) if m.group(3) else 0
            tt = now.replace(hour=h, minute=mi, second=0, microsecond=0)
            if tt < now:
                tt += timedelta(days=1)
            msg = re.sub(r"(azi|astazi|today)\s+la\s+\d{1,2}:?\d{0,2}", "", tl).strip()
    if not tt:
        m = re.search(r"\bla\s+(\d{1,2}):(\d{2})", tl)
        if m:
            h = int(m.group(1))
            mi = int(m.group(2))
            tt = now.replace(hour=h, minute=mi, second=0, microsecond=0)
            if tt < now:
                tt += timedelta(days=1)
            msg = re.sub(r"\bla\s+\d{1,2}:\d{2}", "", tl).strip()
    if not tt:
        m = re.search(r"in\s+(\d+)\s*(minut|minute|min|ore|ora|hour|hours?)", tl)
        if m:
            amt = int(m.group(1))
            unit = m.group(2)
            if any(x in unit for x in ["minut", "min", "hour"]):
                tt = now + timedelta(minutes=amt)
            else:
                tt = now + timedelta(hours=amt)
            msg = re.sub(r"in\s+\d+\s*(minut|minute|min|ore|ora|hour|hours?)", "", tl).strip()
    if not tt:
        if any(x in tl for x in ["diminea", "morning"]):
            tt = now.replace(hour=9, minute=0, second=0, microsecond=0)
            if tt < now:
                tt += timedelta(days=1)
        elif any(x in tl for x in ["seara", "evening"]):
            tt = now.replace(hour=20, minute=0, second=0, microsecond=0)
            if tt < now:
                tt += timedelta(days=1)
        elif any(x in tl for x in ["pranz", "lunch"]):
            tt = now.replace(hour=13, minute=0, second=0, microsecond=0)
            if tt < now:
                tt += timedelta(days=1)
    for w in ["sa ", "sa,", "to ", "maine", "azi", "astazi", "today", "dimineata", "seara", "pranz"]:
        msg = msg.replace(w, "")
    return tt, msg.strip(" ,.-") or text

KNOWN_STORES = ["kaufland", "lidl", "mega", "penny", "profi", "auchan", "carrefour", "selgros", "rewe"]

def detect_store(text):
    t = text.lower()
    for s in KNOWN_STORES:
        if s in t:
            return s
    m = re.search(r"(?:pe lista|din lista|la lista|listei|lista)\s+(\w+)", t)
    return m.group(1).lower() if m else None

def detect_item(text, store):
    t = text.lower()
    for p in ["adauga", "pune", "add", "sterge", "scoate", "elimina", "remove", "am luat", "baga"]:
        t = t.replace(p, "")
    if store:
        t = t.replace(store, "")
    for p in ["pe lista", "din lista", "la lista", "in lista"]:
        t = t.replace(p, "")
    return t.strip(" ,.-")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Salut! Sunt *Valet*, asistentul tau personal.\n\n"
        "*Liste de cumparaturi:*\n"
        "- adauga iaurt pe lista lidl\n"
        "- arata lista kaufland\n"
        "- sterge lapte din lista lidl\n"
        "- reseteaza lista lidl\n"
        "- /liste - toate listele\n\n"
        "*Liste partajate:*\n"
        "- /lista\\_noua gratar\n"
        "- /join\\_lista COD\n\n"
        "*Remindere:*\n"
        "- reaminteste-mi maine la 11 sa cumpar paine\n"
        "- reminder in 30 minute sa sun\n"
        "- /remindere - remindere active",
        parse_mode="Markdown"
    )

async def cmd_liste(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lists = load_json(LISTS_FILE)
    shared = load_json(SHARED_FILE)
    if not lists and not shared:
        await update.message.reply_text("Nu ai nicio lista. Ex: adauga lapte pe lista lidl")
        return
    text = "*Listele tale:*\n\n"
    for store, items in lists.items():
        e = "OK" if items else "--"
        text += f"{e} *{store.capitalize()}* - {len(items)} produse\n"
    if shared:
        text += "\n*Liste partajate:*\n"
        for code, lst in shared.items():
            text += f"- *{lst['name'].capitalize()}* (cod: {code}) - {len(lst['items'])} produse\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_lista_noua(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Ex: /lista_noua gratar")
        return
    name = "_".join(context.args).lower()
    shared = load_json(SHARED_FILE)
    code = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    shared[code] = {"name": name, "items": [], "owner": update.effective_user.id}
    save_json(SHARED_FILE, shared)
    await update.message.reply_text(
        f"Lista *{name}* creata!\nCod pentru prieteni: `{code}`\nEi scriu: /join\\_lista {code}",
        parse_mode="Markdown"
    )

async def cmd_join_lista(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Ex: /join_lista ABC123")
        return
    code = context.args[0].upper()
    shared = load_json(SHARED_FILE)
    if code not in shared:
        await update.message.reply_text("Cod invalid.")
        return
    name = shared[code]["name"]
    await update.message.reply_text(
        f"Te-ai alaturat listei *{tst.capitalize()}*!\nAdauga cu: adauga ceva pe lista {name}",
        parse_mode="Markdown"
    )

async def cmd_remindere(update: Update, context: ContextTypes.DEFAULT_TYPE):
    jobs = context.job_queue.jobs()
    if not jobs:
        await update.message.reply_text("Nu ai niciun reminder activ.")
        return
    text = "*Remindere active:*\n\n"
    for i, job in enumerate(jobs, 1):
        rt = job.next_t.astimezone(tz).strftime("%d.%m.%Y %H:%M")
        text += f"{i}. {rt} - {job.name}\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    await context.bot.send_message(
        chat_id=job.chat_id,
        text=f"Reminder!\n\n{job.data}",
        parse_mode="Markdown"
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("|")
    action = parts[0]
    if action == "check":
        store = parts[1]
        idx = int(parts[2])
        lists = load_json(LISTS_FILE)
        shared = load_json(SHARED_FILE)
        if store in lists and idx < len(lists[store]):
            item = lists[store].pop(idx)
            save_json(LISTS_FILE, lists)
            await query.edit_message_text(f"*{item.capitalize()}* bifat!", parse_mode="Markdown")
        else:
            for code, lst in shared.items():
                if lst["name"] == store and idx < len(lst["items"]):
                    item = lst["items"].pop(idx)
                    save_json(SHARED_FILE, shared)
                    await query.edit_message_text(f"*{item.capitalize()}* bifat!", parse_mode="Markdown")
                    break
    elif action == "reset":
        store = parts[1]
        lists = load_json(LISTS_FILE)
        shared = load_json(SHARED_FILE)
        if store in lists:
            lists[store] = []
            save_json(LISTS_FILE, lists)
        else:
            for code, lst in shared.items():
                if lst["name"] == store:
                    lst["items"] = []
            save_json(SHARED_FILE, shared)
        await query.edit_message_text(f"Lista *{store.capitalize()}* golita!", parse_mode="Markdown")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    t = text.lower().strip()

    is_add = any(t.startswith(x) or (" " + x + " ") in (" " + t + " ") for x in ["adauga", "pune", "baga", "bag", "add"])
    is_show = any(x in t for x in ["arata lista", "arata-mi lista", "show list", "ce am pe lista", "ce e pe lista", "ce mai am pe lista"])
    is_remove = any(t.startswith(x) or (" " + x + " ") in (" " + t + " ") for x in ["sterge", "scoate", "elimina", "remove", "am luat"])
    is_reset = any(x in t for x in ["reseteaza lista", "goleste lista", "sterge lista", "clear lista", "gata am fost la", "am terminat la"])
    is_reminder = any(x in t for x in ["reaminteste", "aminteste", "reminder", "remind me", "nu uita"])

    store = detect_store(t)
    shared = load_json(SHARED_FILE)
    sst = None
    for code, lst in shared.items():
        if lst["name"] in t:
            sst = (code, lst["name"])
            break
    tst = sst[1] if sst else store

    if is_reset and tst:
        if sst:
            shared[sst[0]]["items"] = []
            save_json(SHARED_FILE, shared)
        else:
            ls = load_json(LISTS_FILE)
            ls[store] = []
            save_json(LISTS_FILE, ls)
        await update.message.reply_text(f"Lista *{tst.capitalize()}* golita!", parse_mode="Markdown")
        return

    if is_show and tst:
        if sst:
            items = shared[sst[0]]["items"]
        else:
            items = load_json(LISTS_FILE).get(store, [])
        if not items:
            await update.message.reply_text(f"Lista *{tst.capitalize()}* e goala.", parse_mode="Markdown")
        else:
            num = "\n".join(f"{i+1}. {it}" for i, it in enumerate(items))
            kb = [[InlineKeyboardButton(f"OK {it}", callback_data=f"check|{tst}|{idx}")] for idx, it in enumerate(items)]
            kb.append([InlineKeyboardButton("Goleste lista", callback_data=f"reset|{tst}")])
            await update.message.reply_text(
                f"*Lista {tst.capitalize()}:*\n\n{num}\n\nApasa pe produs cand l-ai luat!",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(kb)
            )
        return

    if is_add and tst:
        item = detect_item(text, tst)
        if not item:
            await update.message.reply_text("Nu am inteles ce sa adaug. Ex: adauga lapte pe lista lidl")
            return
        if sst:
            shared[sst[0]]["items"].append(item)
            save_json(SHARED_FILE, shared)
        else:
            ls = load_json(LISTS_FILE)
            if store not in ls:
                ls[store] = []
            ls[store].append(item)
            save_json(LISTS_FILE, ls)
        await update.message.reply_text(
            f"*{item.capitalize()}* adaugat pe lista *{tst.capitalize()}*!",
            parse_mode="Markdown"
        )
        return

    if is_remove and tst:
        item = detect_item(text, tst)
        if sst:
            ls_items = shared[sst[0]]["items"]
        else:
            ls_items = load_json(LISTS_FILE).get(store, [])
        matches = [x for x in ls_items if item in x.lower()]
        if matches:
            if sst:
                shared[sst[0]]["items"].remove(matches[0])
                save_json(SHARED_FILE, shared)
            else:
                ls = load_json(LISTS_FILE)
                ls[store].remove(matches[0])
                save_json(LISTS_FILE, ls)
            await update.message.reply_text(f"*{matches[0].capitalize()}* sters!", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"Nu am gasit {item} pe lista.")
        return

    if is_reminder:
        tt, rem = parse_reminder(text)
        if not tt:
            await update.message.reply_text(
                "Nu am inteles cand. Ex: reaminteste-mi maine la 11 sa cumpar paine"
            )
            return
        now = datetime.now(tz)
        if tt < now:
            await update.message.reply_text("Ora a trecut deja.")
            return
        delay = (tt - now).total_seconds()
        rt = tt.strftime("%d.%m.%Y la %H:%M")
        context.job_queue.run_once(
            send_reminder,
            when=delay,
            chat_id=update.effective_chat.id,
            name=rem,
            data=rem
        )
        await update.message.reply_text(
            f"Reminder setat!\nTe anunt pe *{rt}*\n_{rem}_",
            parse_mode="Markdown"
        )
        return

    await update.message.reply_text(
        "Nu am inteles. Incearca:\n"
        "- adauga lapte pe lista lidl\n"
        "- arata lista kaufland\n"
        "- reaminteste-mi maine la 10 sa...\n"
        "- /start pentru toate comenzile"
    )

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("liste", cmd_liste))
    app.add_handler(CommandHandler("lista_noua", cmd_lista_noua))
    app.add_handler(CommandHandler("join_lista", cmd_join_lista))
    app.add_handler(CommandHandler("remindere", cmd_remindere))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Valet Bot pornit!")
    app.run_polling()

if __name__ == "__main__":
    main()

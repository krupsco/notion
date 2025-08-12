import pytz
from datetime import datetime, date
from dateutil import tz
import streamlit as st
from notion_client import Client

# --- Konfiguracja ---
NOTION_TOKEN = st.secrets["NOTION_TOKEN"]
DB_ID = st.secrets["NOTION_DATABASE_ID"]
LOCAL_TZ = pytz.timezone(st.secrets.get("TIMEZONE", "Europe/Warsaw"))

notion = Client(auth=NOTION_TOKEN)

# --- Stałe dopasowane do Twojej bazy ---
PROP_TITLE = "Episode Title"
PROP_STATUS = "Status"
PROP_RELEASE = "Release Date"
PROP_RECORDING = "Recording Date"
PROP_EP_NO = "Episode Number"
PROP_GUEST = "Guest"
PROP_TOPIC = "Topic"

STATUS_OPTIONS = ["Zaplanowany", "Szkic", "Nagrany", "Zmontowany", "Published"]

DEFAULT_CHECKLIST = [
    "Opracowanie konspektu i scenariusza",
    "Kontakt z gościem / potwierdzenie terminu",
    "Research (osobowy i merytoryczny)",
    "Przygotowanie i test sprzętu",
    "Nagranie odcinka",
    "Zgranie materiału z karty",
    "Montaż i normalizacja głośności",
    "Opis odcinka + metadane",
    "Materiały promocyjne i publikacja"
]

# --- Helpery ---
def fetch_episodes():
    pages, cursor = [], None
    while True:
        kwargs = {"database_id": DB_ID}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = notion.databases.query(
            **kwargs,
            sorts=[{"property": PROP_EP_NO, "direction": "ascending"}]
        )
        pages.extend(resp["results"])
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    return pages

def get_text(prop):
    return "".join([r.get("plain_text","") for r in prop]) if prop else ""

def page_title(p):
    return get_text(p["properties"][PROP_TITLE]["title"]) or "(bez tytułu)"

def page_status(p):
    sel = p["properties"][PROP_STATUS].get("select")
    return sel["name"] if sel else "-"

def page_date(p, prop_name):
    d = p["properties"][prop_name].get("date")
    return d["start"] if d and d.get("start") else None

def page_number(p):
    return p["properties"][PROP_EP_NO].get("number")

def options_map(pages):
    return {f'#{page_number(p) or "-"} {page_title(p)}  [{page_status(p)}]': p["id"] for p in pages}

def update_properties(page_id, *, status=None, release=None, recording=None, topic=None, guest=None):
    props = {}
    if status:
        props[PROP_STATUS] = {"select": {"name": status}}
    if release is not None:
        props[PROP_RELEASE] = {"date": {"start": release.isoformat()}} if isinstance(release, date) else {"date": None}
    if recording is not None:
        props[PROP_RECORDING] = {"date": {"start": recording.isoformat()}} if isinstance(recording, date) else {"date": None}
    if topic:
        props[PROP_TOPIC] = {"select": {"name": topic}}
    if guest is not None:
        # w Twojej bazie 'Guest' wygląda na rich_text
        props[PROP_GUEST] = {"rich_text": [{"type": "text", "text": {"content": guest}}]} if guest else {"rich_text": []}
    if props:
        notion.pages.update(page_id=page_id, properties=props)

def add_todos(page_id, items):
    if not items:
        return
    # Nagłówek + checklista to-do (blokami)
    notion.blocks.children.append(page_id, children=[{
        "object": "block",
        "type": "heading_3",
        "heading_3": {"rich_text": [{"type": "text", "text": {"content": "Checklist produkcyjny"}}]}
    }])
    children = [{
        "object": "block",
        "type": "to_do",
        "to_do": {"rich_text": [{"type": "text", "text": {"content": t}}], "checked": False}
    } for t in items]
    notion.blocks.children.append(page_id, children=children)

def quick_report(pages):
    buckets = {k: [] for k in STATUS_OPTIONS}
    for p in pages:
        buckets.setdefault(page_status(p), []).append(p)
    lines = []
    for st_name in STATUS_OPTIONS:
        arr = buckets.get(st_name, [])
        if not arr: 
            continue
        lines.append(f"**{st_name}** ({len(arr)}):")
        for p in arr:
            rel = page_date(p, PROP_RELEASE) or "-"
            ep_no = page_number(p)
            lines.append(f"- #{ep_no}: {page_title(p)} — data: {rel}")
        lines.append("")
    return "\n".join(lines)

# --- UI ---
st.set_page_config(page_title="Podcast Zamkowy — Produkcja", page_icon="🎙️", layout="wide")
st.title("🎙️ Podcast Zamkowy — sterowanie produkcją")

tab_list, tab_edit, tab_todos, tab_report = st.tabs([
    "Przegląd odcinków", "Aktualizuj właściwości", "Dodaj checklistę", "Mini‑raport"
])

with tab_list:
    pages = fetch_episodes()
    st.caption(f"Ostatnia aktualizacja: {datetime.now(LOCAL_TZ).strftime('%Y-%m-%d %H:%M')}")
    for p in pages:
        c1, c2, c3, c4, c5 = st.columns([6,2,3,3,3])
        with c1: st.markdown(f"**{page_title(p)}**")
        with c2: st.write(page_number(p) or "-")
        with c3: st.write(page_status(p))
        with c4: st.write(page_date(p, PROP_RECORDING) or "-")
        with c5: st.write(page_date(p, PROP_RELEASE) or "-")

with tab_edit:
    pages = fetch_episodes()
    opts = options_map(pages)
    sel = st.selectbox("Wybierz odcinek", list(opts.keys()))
    new_status = st.selectbox("Status", STATUS_OPTIONS, index=STATUS_OPTIONS.index("Szkic") if "Szkic" in STATUS_OPTIONS else 0)
    new_topic = st.text_input("Topic (select) — zostaw pusty, jeśli bez zmian")
    new_guest = st.text_input("Guest (rich_text) — zostaw pusty, jeśli bez zmian")
    colA, colB = st.columns(2)
    with colA:
        new_recording = st.date_input("Recording Date (opcjonalnie)", value=None)
    with colB:
        new_release = st.date_input("Release Date (opcjonalnie)", value=None)
    if st.button("Zapisz zmiany"):
        update_properties(
            opts[sel],
            status=new_status,
            release=new_release if new_release else None,
            recording=new_recording if new_recording else None,
            topic=new_topic if new_topic else None,
            guest=(new_guest if new_guest != "" else None)
        )
        st.success("Zaktualizowano właściwości strony odcinka.")

with tab_todos:
    pages = fetch_episodes()
    opts = options_map(pages)
    sel = st.selectbox("Odcinek do uzupełnienia checklistą", list(opts.keys()), key="todo_sel")
    mode = st.radio("Tryb", ["Domyślna checklista", "Własna lista"])
    if mode == "Domyślna checklista":
        items = DEFAULT_CHECKLIST
        st.write("Zostaną dodane:")
        for i in items:
            st.write(f"• {i}")
    else:
        txt = st.text_area("Wpisz punkty (jeden na linię)", height=180, placeholder="punkt 1\npunkt 2\npunkt 3")
        items = [l.strip() for l in txt.splitlines() if l.strip()]

    if st.button("Dodaj checklistę"):
        if not items:
            st.warning("Brak pozycji do dodania.")
        else:
            add_todos(opts[sel], items)
            st.success("Checklistę dodano.")


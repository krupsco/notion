# app.py — Podcast Zamkowy: sterowanie produkcją + "Command API"
# Wymaga: streamlit, notion-client, pytz
# Sekrety w .streamlit/secrets.toml:
#   NOTION_TOKEN = "secret_xxx"
#   NOTION_DATABASE_ID = "216aaf4924e5802890f4f1235aa8ecc8"
#   TIMEZONE = "Europe/Warsaw"
#   COMMAND_SHARED_SECRET = "<długi-losowy-klucz-HMAC>"
#   APP_BASE_URL = "https://<twoja-aplikacja>.streamlit.app"

import os, json, base64, hmac, hashlib
from datetime import datetime, date
from typing import List, Dict, Optional
import pytz
import streamlit as st
from notion_client import Client
from notion_client.errors import APIResponseError

# ---------- USTAWIENIA UI ----------
st.set_page_config(page_title="Podcast Zamkowy — Produkcja", page_icon="🎙️", layout="wide")
st.title("🎙️ Podcast Zamkowy — sterowanie produkcją")

# ---------- SECRETS / KONFIG ----------
def get_secret(name: str, default: Optional[str] = None) -> Optional[str]:
    if name in st.secrets:
        return st.secrets[name]
    return os.getenv(name, default)

NOTION_TOKEN = get_secret("NOTION_TOKEN")
DB_ID = get_secret("NOTION_DATABASE_ID")
LOCAL_TZ_NAME = get_secret("TIMEZONE", "Europe/Warsaw")
COMMAND_SHARED_SECRET = get_secret("COMMAND_SHARED_SECRET", "PLEASE-CHANGE-ME")
APP_BASE_URL = get_secret("APP_BASE_URL", "")

if not NOTION_TOKEN or not DB_ID:
    st.error("Brakuje sekretów **NOTION_TOKEN** i/lub **NOTION_DATABASE_ID**. Uzupełnij je w Secrets i uruchom ponownie.")
    st.stop()

LOCAL_TZ = pytz.timezone(LOCAL_TZ_NAME)

# ---------- KLIENT NOTION ----------
notion = Client(auth=NOTION_TOKEN)

def retrieve_db_or_fail(db_id: str):
    try:
        return notion.databases.retrieve(db_id)
    except APIResponseError as e:
        st.error("❌ Nie mogę odczytać bazy. Sprawdź ID oraz udzielenie integracji dostępu **Can edit** do TEJ bazy.")
        st.write("Kod błędu Notion:", getattr(e, "code", None))
        st.stop()

DB_META = retrieve_db_or_fail(DB_ID)
DB_PROPS = DB_META.get("properties", {})

def db_title_text(db_meta) -> str:
    t = db_meta.get("title", [])
    return "".join(x.get("plain_text", "") for x in t) if t else "(bez nazwy)"

# ---------- NAZWY WŁAŚCIWOŚCI (DOPASOWANE DO TWOJEJ BAZY) ----------
PROP_TITLE = "Episode Title"
PROP_STATUS = "Status"
PROP_RELEASE = "Release Date"
PROP_RECORDING = "Recording Date"
PROP_EP_NO = "Episode Number"
PROP_GUEST = "Guest"
PROP_TOPIC = "Temat"

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

# ---------- HELPERY ----------
def safe(val):
    # wycina pythonowy Ellipsis i puste wartości
    if val is Ellipsis:
        return "-"
    if val in (None, "", [], {}):
        return "-"
    return str(val)

def get_text(rich: list) -> str:
    return "".join([x.get("plain_text", "") for x in rich]) if rich else ""

def page_title(p) -> str:
    return get_text(p["properties"].get(PROP_TITLE, {}).get("title", [])) or "(bez tytułu)"

def page_status(p) -> str:
    prop = p["properties"].get(PROP_STATUS, {})
    if prop.get("type") == "status":
        val = prop.get("status")
    else:
        val = prop.get("select")
    return val["name"] if val else "-"

def page_topic(p) -> str:
    prop = p["properties"].get(PROP_TOPIC, {})
    t = prop.get("type")
    if t == "multi_select":
        items = prop.get("multi_select", [])
        return ", ".join([i["name"] for i in items]) if items else "-"
    elif t == "select":
        sel = prop.get("select")
        return sel["name"] if sel else "-"
    else:
        return "-"

def page_guest(p) -> str:
    prop = p["properties"].get(PROP_GUEST, {})
    t = prop.get("type")
    if t == "people":
        people = prop.get("people", [])
        return ", ".join([pp.get("name", "—") for pp in people]) if people else "-"
    elif t in ["rich_text", "text"]:
        return get_text(prop.get("rich_text", [])) or "-"
    else:
        return "-"

def page_date(p, prop_name: str) -> Optional[str]:
    d = p["properties"].get(prop_name, {}).get("date")
    return d["start"] if d and d.get("start") else None

def page_number(p) -> Optional[int]:
    return p["properties"].get(PROP_EP_NO, {}).get("number")

def parse_date_any(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])  # YYYY-MM-DD
    except Exception:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
        except Exception:
            return None

def fetch_episodes_safe(notion_client: Client, db_id: str, sort_prop: Optional[str]) -> List[Dict]:
    # 1) bez sortowania — sanity check ID/uprawnienia
    pages, cursor = [], None
    while True:
        kwargs = {"database_id": db_id}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = notion_client.databases.query(**kwargs)
        pages.extend(resp["results"])
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")

    # 2) posortuj, tylko jeśli pole istnieje
    if sort_prop and sort_prop in DB_PROPS:
        pages_sorted, cursor = [], None
        while True:
            kwargs = {"database_id": db_id}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = notion_client.databases.query(
                **kwargs,
                sorts=[{"property": sort_prop, "direction": "ascending"}]
            )
            pages_sorted.extend(resp["results"])
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        return pages_sorted

    return pages

def fetch_episodes() -> List[Dict]:
    return fetch_episodes_safe(notion, DB_ID, PROP_EP_NO)

def options_map(pages: List[Dict]) -> Dict[str, str]:
    out = {}
    for p in pages:
        num = page_number(p)
        lab = f'#{num if num is not None else "-"} {page_title(p)}  [{page_status(p)}]'
        out[lab] = p["id"]
    return out

def update_properties(page_id: str,
                      status: Optional[str] = None,
                      release: Optional[date] = None,
                      recording: Optional[date] = None,
                      topic: Optional[str] = None,
                      guest: Optional[str] = None):
    props = {}
    # Status: obsługa status/select
    if status is not None:
        st_prop = DB_PROPS.get(PROP_STATUS, {})
        if st_prop.get("type") == "status":
            props[PROP_STATUS] = {"status": {"name": status}}
        else:
            props[PROP_STATUS] = {"select": {"name": status}}
    # Release/Recording
    if release is not None:
        props[PROP_RELEASE] = {"date": {"start": release.isoformat()}}
    if recording is not None:
        props[PROP_RECORDING] = {"date": {"start": recording.isoformat()}}
    # Topic: obsługa multi_select/select
    if topic:
        topic_prop = DB_PROPS.get(PROP_TOPIC, {})
        if topic_prop.get("type") == "multi_select":
            items = [{"name": t.strip()} for t in topic.split(",") if t.strip()]
            props[PROP_TOPIC] = {"multi_select": items}
        else:
            props[PROP_TOPIC] = {"select": {"name": topic}}
    # Guest: people/rich_text
    if guest is not None:
        guest_prop = DB_PROPS.get(PROP_GUEST, {})
        if guest_prop.get("type") == "people":
            # Uwaga: ustawienie 'people' wymaga ID użytkowników Notion (nie imion).
            # Tu tylko ostrzegamy i nie nadpisujemy, aby nie wyczyścić istniejących danych.
            st.warning("Pole 'Guest' ma typ 'people' — do ustawienia wymagane są ID użytkowników Notion. Pomijam zapis.")
        else:
            props[PROP_GUEST] = {"rich_text": [{"type": "text", "text": {"content": guest}}]} if guest else {"rich_text": []}

    if props:
        notion.pages.update(page_id=page_id, properties=props)

def add_todos(page_id: str, items: List[str]):
    if not items:
        return
    # nagłówek
    notion.blocks.children.append(page_id, children=[{
        "object": "block",
        "type": "heading_3",
        "heading_3": {"rich_text": [{"type": "text", "text": {"content": "Checklist produkcyjny"}}]}
    }])
    # elementy to-do
    children = [{
        "object": "block",
        "type": "to_do",
        "to_do": {"rich_text": [{"type": "text", "text": {"content": t}}], "checked": False}
    } for t in items]
    notion.blocks.children.append(page_id, children=children)

def quick_report(pages: List[Dict]) -> str:
    buckets: Dict[str, List[Dict]] = {}
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

# ---------- COMMAND API (polecenia z czatu) ----------
def sign_payload(payload_b64: str) -> str:
    return hmac.new(
        key=COMMAND_SHARED_SECRET.encode("utf-8"),
        msg=payload_b64.encode("utf-8"),
        digestmod=hashlib.sha256
    ).hexdigest()

def decode_cmd(cmd_b64: str) -> Optional[dict]:
    try:
        pad = "=" * (-len(cmd_b64) % 4)  # dopełnienie Base64URL
        data = base64.urlsafe_b64decode(cmd_b64 + pad)
        return json.loads(data.decode("utf-8"))
    except Exception:
        return None

def find_page_id_by_label(pages: List[Dict], label: str) -> Optional[str]:
    # Obsługuje "#8 Tytuł ..." — dopasowanie po numerze i prefiksie tytułu
    target_num = None
    if label.startswith("#"):
        try:
            target_num = int(label.split()[0].lstrip("#"))
        except Exception:
            target_num = None
    title_part = label.split(" ", 1)[1] if " " in label else ""
    for p in pages:
        num = page_number(p)
        title = page_title(p)
        if (target_num is None or num == target_num) and (not title_part or title.startswith(title_part)):
            return p["id"]
    return None

def apply_command(cmd: dict) -> (bool, str):
    op = cmd.get("op")
    if op == "update_properties":
        pages = fetch_episodes()
        page_label = cmd.get("page")
        page_id = find_page_id_by_label(pages, page_label) if page_label else cmd.get("page_id")
        if not page_id:
            return False, "Nie znaleziono strony odcinka (page/page_id)."
        props = cmd.get("props", {})
        status = props.get("Status")
        rel = parse_date_any(props.get("Release Date"))
        rec = parse_date_any(props.get("Recording Date"))
        topic = props.get("Topic")
        guest = props.get("Guest") if "Guest" in props else None
        update_properties(page_id, status=status, release=rel, recording=rec, topic=topic, guest=guest)
        return True, "Właściwości zaktualizowane."

    elif op == "add_checklist":
        pages = fetch_episodes()
        page_label = cmd.get("page")
        page_id = find_page_id_by_label(pages, page_label) if page_label else cmd.get("page_id")
        if not page_id:
            return False, "Nie znaleziono strony odcinka (page/page_id)."
        items = cmd.get("items", [])
        if not items:
            return False, "Brak pozycji checklisty."
        add_todos(page_id, items)
        return True, "Checklistę dodano."

    else:
        return False, f"Nieznana operacja: {op}"

# ---------- UI: TABS ----------
tab_list, tab_edit, tab_todos, tab_report, tab_diag, tab_cmd = st.tabs(
    ["Przegląd odcinków", "Aktualizuj właściwości", "Dodaj checklistę", "Mini‑raport", "Diagnostyka", "Polecenia"]
)

from urllib.parse import urlencode

def make_command_link(cmd_dict):
    payload_json = json.dumps(cmd_dict)
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode("utf-8")).decode("utf-8").rstrip("=")
    sig = sign_payload(payload_b64)
    return f"{APP_BASE_URL}?{urlencode({'cmd': payload_b64, 'sig': sig})}"

with tab_list:
    pages = fetch_episodes()
    st.caption(f"Ostatnia aktualizacja: {datetime.now(LOCAL_TZ).strftime('%Y-%m-%d %H:%M')}")

    c1, c2, c3, c4, c5, c6, c7, c8 = st.columns([6,2,3,3,3,4,4,2])
    with c1: st.markdown("**Tytuł odcinka**")
    with c2: st.markdown("**#**")
    with c3: st.markdown("**Status**")
    with c4: st.markdown("**Topic**")
    with c5: st.markdown("**Guest**")
    with c6: st.markdown("**Data nagrania**")
    with c7: st.markdown("**Data publikacji**")
    with c8: st.markdown("**Command**")

    for p in pages:
        ep_label = f"#{page_number(p)} {page_title(p)}"
        c1, c2, c3, c4, c5, c6, c7, c8 = st.columns([6,2,3,3,3,4,4,2])
        with c1: st.write(safe(page_title(p)))
        with c2: st.write(safe(page_number(p)))
        with c3: st.write(safe(page_status(p)))
        with c4: st.write(safe(page_topic(p)))
        with c5: st.write(safe(page_guest(p)))
        with c6: st.write(safe(page_date(p, PROP_RECORDING)))
        with c7: st.write(safe(page_date(p, PROP_RELEASE)))
        with c8:
            cmd_dict = {
                "op": "update_properties",
                "page": ep_label,
                "props": {"Status": "Nagrany"}  # przykład
            }
            link = make_command_link(cmd_dict)
            st.markdown(f"[link]({link})")



with tab_edit:
    pages = fetch_episodes()
    opts = options_map(pages)
    sel = st.selectbox("Wybierz odcinek", list(opts.keys()))
    new_status = st.selectbox("Status", STATUS_OPTIONS, index=STATUS_OPTIONS.index("Szkic") if "Szkic" in STATUS_OPTIONS else 0)
    new_topic = st.text_input("Topic — dla multi‑select podaj po przecinku (np. 'Historia, Zamek') / dla select wpisz jedną wartość")
    new_guest = st.text_input("Guest — jeśli pole ma typ 'people', zapisz ręcznie w Notion (tu obsługujemy rich_text)")
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

with tab_report:
    pages = fetch_episodes()
    st.markdown("### Stan na dziś")
    st.markdown(quick_report(pages))
    st.info("Skopiuj raport i wklej do Notion/Slack/e‑maila.")

with tab_diag:
    st.subheader("Diagnostyka połączenia z Notion")
    st.write("**Nazwa bazy:**", db_title_text(DB_META))
    st.write("**Właściwości w bazie (nazwa → typ):**")
    for k, v in DB_PROPS.items():
        st.write("-", k, "→", v.get("type"))
    st.caption("Upewnij się, że stałe PROP_* w kodzie zgadzają się 1:1 z powyższymi.")

with tab_cmd:
    st.write("Masz dwa sposoby wykonania polecenia z czatu: wklejenie JSON lub wejście w link z podpisem HMAC.")
    tab_cmd_local, tab_cmd_link, tab_gen = st.tabs(["Wklej polecenie", "Link (URL) z podpisem", "Generator linku"])

    with tab_cmd_local:
        cmd_text = st.text_area("Polecenie (JSON)", height=180,
                                placeholder='{"op":"update_properties","page":"#8 Opera, Warszawa, Zamek i małe biurko","props":{"Status":"Nagrany","Release Date":"2025-08-29"}}')
        if st.button("Zastosuj polecenie"):
            try:
                cmd = json.loads(cmd_text)
                ok, msg = apply_command(cmd)
                (st.success if ok else st.error)(msg)
            except json.JSONDecodeError:
                st.error("Niepoprawny JSON.")

    with tab_cmd_link:
        # zgodność z różnymi wersjami Streamlit
        try:
            qp = st.query_params
        except Exception:
            qp = st.experimental_get_query_params()
        cmd_b64 = qp.get("cmd")
        sig = qp.get("sig")
        if isinstance(cmd_b64, list): cmd_b64 = cmd_b64[0] if cmd_b64 else None
        if isinstance(sig, list): sig = sig[0] if sig else None

        if cmd_b64 and sig:
            expected = sign_payload(cmd_b64)
            if sig != expected:
                st.error("Nieprawidłowy podpis polecenia (HMAC).")
            else:
                cmd = decode_cmd(cmd_b64)
                if not cmd:
                    st.error("Nie można zdekodować polecenia.")
                else:
                    st.write("**Podgląd polecenia:**")
                    st.json(cmd)
                    if st.button("Wykonaj polecenie"):
                        ok, msg = apply_command(cmd)
                        (st.success if ok else st.error)(msg)
        else:
            st.info("Brak `cmd`/`sig` w URL. Wygeneruj link w czacie i kliknij.")

    with tab_gen:
        st.caption("Lokalny generator (do testów): wklej JSON, dostaniesz podpisany link.")
        gen_json = st.text_area("JSON do podpisania", height=120,
                                value='{"op":"update_properties","page":"#8 Opera, Warszawa, Zamek i małe biurko","props":{"Status":"Nagrany","Release Date":"2025-08-29"}}')
        if st.button("Podpisz i pokaż link"):
            payload_b64 = base64.urlsafe_b64encode(gen_json.encode("utf-8")).decode("utf-8").rstrip("=")
            sig = sign_payload(payload_b64)
            base = APP_BASE_URL or "https://example.streamlit.app"
            url = f"{base}?cmd={payload_b64}&sig={sig}"
            st.code(url)
            st.caption("Skopiuj link, otwórz w przeglądarce i potwierdź wykonanie.")

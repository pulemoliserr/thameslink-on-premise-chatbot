"""Frontline Assistant, version 2: the frontline knowledge base (use case 1).

Three user groups, each signing in to its own area:
- Staff & customer service: ask questions (answers cite document, section and page), quick checks
  for tickets, assistance, refunds and trains, and the documents themselves.
- Policy owner: everything staff have, plus the document library (upload, replace, remove), checking
  answers, the test set and the change history. Changing documents needs the "document editor" right.
- IT & data protection: accounts, logs, privacy settings, the on-premise check, backups and recovery.

Run:  python -m streamlit run app.py          Accounts:  python -m rag.auth --help
"""
from __future__ import annotations

import os
import time
from dataclasses import asdict
from urllib.parse import urlparse

import pandas as pd
import streamlit as st

from rag import (advisors, auth, config, history, library, netguard, ollama_client, settings, testset,
                 usage)
from rag.advisors import (CLAIM_EVENTS, FLEET_FIELDS, NO_TICKET_REASONS, R, TICKET_SITUATIONS, TICKET_TYPES,
                          Finding)
from rag.answer import answer, coverage
from rag.retrieve import Retriever
from rag.usecases import BY_ID

st.set_page_config(page_title="Frontline Assistant", page_icon="🚆", layout="wide")
ICON = {"ok": "✅", "warn": "⚠️", "stop": "⛔", "info": "ℹ️"}
TOPICS = {"policy": "All documents", "tickets": "Tickets & fares", "claims": "Refunds & disruption",
          "assist": "Assistance", "fleet": "Trains"}
EXAMPLES = ["What is a passenger entitled to if their train is cancelled?",
            "Can a passenger take a folding bike on the train?",
            "Can a mobility scooter travel on the train?",
            "Which trains have an accessible toilet?"]


def demo_mode() -> bool:
    """DEMO_MODE from Streamlit secrets (Community Cloud) or the environment, read on every page load."""
    value = ""
    try:
        value = str(st.secrets.get("DEMO_MODE", ""))
    except Exception:                       # no secrets file: that's fine
        pass
    value = value or os.getenv("DEMO_MODE", "")
    config.DEMO_MODE = value.strip().lower() in ("1", "true", "yes", "on")
    return config.DEMO_MODE


# ------------------------------------------------------------------ first start
@st.cache_resource(show_spinner="First start: preparing the documents and the search index …")
def prepare_library(demo: bool) -> str:
    """Seed the document library and build the index if needed. Returns an error message or ""."""
    try:
        library.ensure_library()
        if library.index_file().exists():
            return ""
        missing = [d for d in library.registry() if d.get("kind", "pdf") == "pdf" and not library.doc_path(d).exists()]
        if missing and demo:        # public demo only: fetch the public documents from their publishers
            import importlib.util
            spec = importlib.util.spec_from_file_location("dl", config.ROOT / "scripts" / "download_documents.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.main()
            library.ensure_library()
        elif missing:
            return ("These documents are missing: " + ", ".join(d["short"] for d in missing)
                    + ". Put the PDFs in the library folder (see README, “Get the documents”).")
        library.build_index()
        return ""
    except Exception as e:
        return f"Could not prepare the documents ({e}). See the README, “Get the documents”."


@st.cache_resource
def guard(mode: str) -> str:
    return netguard.install(mode)


@st.cache_data(ttl=3600, show_spinner=False)
def daily_purge(_day: str) -> dict:
    return usage.purge()


@st.cache_resource(show_spinner="Loading the search index …")
def load_retriever(version: int) -> Retriever:
    return Retriever.load()


@st.cache_data(show_spinner=False)
def citation_problems(version: int) -> list[str]:
    return advisors.check_citations(load_retriever(version).chunks)


@st.cache_data(ttl=15, show_spinner=False)
def ollama_status():
    return ollama_client.available_models()


demo = demo_mode()
problem = prepare_library(demo)
if problem:
    st.error(problem)
    st.stop()
guard_mode = guard("monitor" if demo else config.NETWORK_GUARD)
daily_purge(time.strftime("%Y-%m-%d"))
if demo and not auth.load_users():
    auth.create_demo_accounts()
version = library.index_version()
retriever = load_retriever(version)
models = ollama_status()
llm_ready = ollama_client.has_model(config.CHAT_MODEL, models or [])
cfg = settings.load()


# ------------------------------------------------------------------ sign-in
AREAS = {
    # area id: (label, permission needed)
    "ask": ("💬 Ask", "chat"),
    "checks": ("🧭 Quick checks", "advisors"),
    "docs": ("📄 Documents", "view_documents"),
    "manage": ("🗂️ Manage documents", "view_document_history"),
    "inspect": ("🔍 Check answers", "inspect_answers"),
    "tests": ("🧪 Test set", "manage_testset"),
    "accounts": ("👥 Accounts", "manage_users"),
    "logs": ("📜 Activity & logs", "view_audit"),
    "privacy": ("🔒 Privacy & on-premise", "manage_settings"),
    "backups": ("💾 Backups & recovery", "manage_backups"),
    "system": ("🛠️ System", "system"),
}


def areas_for(u: auth.User) -> list[str]:
    return [a for a, (_, perm) in AREAS.items() if u.can(perm)]


def sign_out(reason: str = ""):
    if st.session_state.get("user"):
        auth.audit("logout", st.session_state["user"], detail=reason)
    st.session_state.clear()                 # the conversation lives only in this session: gone at sign-out
    if reason:
        st.session_state["signout_reason"] = reason


def demo_accounts_box():
    demos = auth.demo_logins()
    if not demos:
        return
    with st.expander("🔑 Demo accounts: pick one to copy the sign-in details"):
        labels = {f"{d['name']}  ·  {d['username']}": d for d in demos}
        pick = labels[st.selectbox("User group", list(labels), key="demo_pick")]
        st.caption("Username")
        st.code(pick["username"], language=None)
        st.caption("Password")
        st.code(pick["password"], language=None)
        dummy = auth.User("x", "", pick["role"], "", "", doc_editor=pick["role"] == "policy_owner")
        st.caption("Opens: " + ", ".join(AREAS[a][0] for a in areas_for(dummy)))
        if st.button("Fill in the sign-in form", use_container_width=True):
            st.session_state["login_username"] = pick["username"]
            st.session_state["login_password"] = pick["password"]
            st.rerun()
        st.caption("Copy with the icon at the right of each box. Demo data only.")


def login_page():
    _, mid, _ = st.columns([1, 1.3, 1])
    with mid:
        st.markdown("## 🚆 Frontline Assistant")
        st.caption("Staff sign-in · student prototype using public rail documents (not affiliated with Thameslink / GTR)")
        reason = st.session_state.pop("signout_reason", "")
        if reason == "idle":
            st.info("You were signed out after 30 minutes without activity.")
        if not auth.load_users():
            st.warning("No accounts exist yet. For a public demo, add the secret `DEMO_MODE = \"1\"` and reboot the app. "
                       "Otherwise IT creates the first administrator on the server with:  \n"
                       "`python -m rag.auth create-admin <username>`")
        demo_accounts_box()
        with st.form("login"):
            username = st.text_input("Username", autocomplete="username", key="login_username")
            password = st.text_input("Password", type="password", autocomplete="current-password",
                                     key="login_password")
            if st.form_submit_button("Sign in", type="primary", use_container_width=True):
                try:
                    u = auth.authenticate(username, password)
                    st.session_state["user"] = u.username
                    st.session_state["last_active"] = time.time()
                    st.rerun()
                except auth.AuthError as e:
                    st.error(str(e))
        st.caption("Each user group signs in to its own area. Forgotten password: ask IT.")


def change_password_page(u: auth.User):
    _, mid, _ = st.columns([1, 1.3, 1])
    with mid:
        st.markdown(f"## Welcome, {u.name}")
        st.info("Please choose your own password before you continue.")
        with st.form("pw"):
            old = st.text_input("Temporary password", type="password", autocomplete="current-password")
            new = st.text_input(f"New password (at least {auth.MIN_PASSWORD_LEN} characters, letters and a number or symbol)",
                                type="password", autocomplete="new-password")
            new2 = st.text_input("Repeat the new password", type="password", autocomplete="new-password")
            if st.form_submit_button("Save password", type="primary", use_container_width=True):
                if new != new2:
                    st.error("The new passwords do not match.")
                else:
                    try:
                        auth.change_password(u.username, old, new)
                        st.rerun()
                    except auth.AuthError as e:
                        st.error(str(e))
        if st.button("Sign out"):
            sign_out()
            st.rerun()


if "user" not in st.session_state:
    login_page()
    st.stop()
if time.time() - st.session_state.get("last_active", 0) > auth.SESSION_IDLE_SECONDS:
    sign_out("idle")
    st.rerun()
st.session_state["last_active"] = time.time()
user = auth.get_user(st.session_state["user"])
if user is None or not user.active:
    sign_out("disabled")
    st.rerun()
if user.must_change:
    change_password_page(user)
    st.stop()

# ------------------------------------------------------------------ sidebar
problems = citation_problems(version)
with st.sidebar:
    st.markdown("### 🚆 Frontline Assistant")
    st.markdown(f"**{user.name}**  \n{auth.ROLES[user.role]['name']}"
                + ("  \n✏️ Document editor" if user.doc_editor and user.role == "policy_owner" else ""))
    my_areas = areas_for(user)
    area = st.radio("Your areas", my_areas, format_func=lambda a: AREAS[a][0], label_visibility="collapsed")
    if st.button("Sign out", use_container_width=True):
        sign_out()
        st.rerun()
    st.divider()
    st.caption("STATUS")
    if llm_ready:
        st.success(f"Language model: `{config.CHAT_MODEL}` (on this server)")
    elif models is None:
        st.info("No language model running: answers quote the documents directly, with their sources.")
    else:
        st.warning(f"Ollama is running, but `{config.CHAT_MODEL}` is not installed.")
    st.caption(f"{len(library.registry())} documents · {len(retriever.chunks)} passages"
               + (" · keyword + meaning search" if retriever.has_embeddings else " · keyword search"))
    if problems and user.role != "staff":
        st.error(f"{len(problems)} quick-check rule(s) no longer match the documents (see Check answers).")
    st.caption(f"Version {config.APP_VERSION}")


# ------------------------------------------------------------------ shared components
def doc_meta() -> dict:
    return {d["id"]: d for d in library.registry()}


def pdf_button(doc_id: str, label: str, key: str):
    """Download the source PDF (served only to signed-in users; loaded on click)."""
    doc = doc_meta().get(doc_id)
    if not doc or not library.source_pdf(doc):
        return
    st.download_button(label, data=lambda: library.pdf_bytes(doc_id) or b"", file_name=library.source_pdf(doc).name,
                       mime="application/pdf", key=key, on_click="ignore", icon="📄", type="tertiary")


def render_findings(findings: list[Finding]):
    if not findings:
        st.info("Nothing to check yet.")
        return
    shown = 0
    for f in findings:
        resolved = [(c, advisors.resolve(c, retriever.chunks)) for c in f.cites]
        if any(chunk is None for _, chunk in resolved):
            # never cite a rule whose source text is no longer in the documents
            st.warning(f"⚠️ **{f.title}**: this rule is being reviewed because its source text is no longer in "
                       "the documents. Check the current document or ask your duty manager.")
            continue
        shown += 1
        with st.container(border=True):
            st.markdown(f"{ICON[f.level]} **{f.title}**")
            st.write(f.text)
            refs = [f"{c.ref} ({chunk['doc_short']}, p. {chunk['page_start']})" for c, chunk in resolved]
            st.caption("Source: " + " · ".join(dict.fromkeys(refs)))
            with st.popover("Show the exact wording"):
                for c, chunk in resolved:
                    st.markdown(f"**{c.ref}** — “{c.quote}”")
    if shown:
        st.caption("These checks restate the documents. The decision stays with you.")


def render_sources(a: dict, key: str, show_all: bool = False):
    meta = doc_meta()
    cited = [s for s in a["sources"] if s["cited"]] or ([] if not a["found"] else a["sources"][:1])
    if cited:
        st.markdown("**Sources**")
        for s in cited:
            version = meta.get(s["doc_id"], {}).get("version", "")
            c1, c2 = st.columns([5, 2])
            c1.markdown(f"[{s['n']}] **{s['doc_short']}** — {s['label']} ({s['pages']})"
                        + (f"  \n<small>{version}</small>" if version else ""), unsafe_allow_html=True)
            with c2:
                pdf_button(s["doc_id"], f"Open PDF ({s['pages']})", f"pdf_{key}_{s['n']}")
    if a["sources"]:
        with st.expander(f"Passages the answer came from ({len(a['sources'])} searched)", expanded=show_all):
            for s in a["sources"]:
                st.markdown(f"**[{s['n']}] {s['doc_short']}** — {s['label']} ({s['pages']})"
                            + ("" if s["cited"] else " · not cited"))
                st.caption(s["text"][:900] + ("…" if len(s["text"]) > 900 else ""))


def render_answer(msg: dict, key: str):
    a = msg["answer"]
    st.markdown(a["text"])
    for note in a["notes"]:
        st.info(note)
    render_sources(a, key)
    secs, target = msg.get("seconds", 0), cfg["response_target_seconds"]
    c1, c2, c3 = st.columns([1, 1, 6])
    for col, rating, icon in ((c1, "up", "👍"), (c2, "down", "👎")):
        if col.button(icon, key=f"{rating}{key}"):
            st.session_state[f"rate_{key}"] = rating
    c3.caption(f"{'Language model' if a['generator'].startswith('llm') else 'Quoted from the documents'} · "
               f"{secs:.1f} s {'✓' if secs <= target else f'(target {target} s)'} · topic: {TOPICS.get(a.get('topic'), '')}")
    rating = st.session_state.get(f"rate_{key}")
    if rating and not st.session_state.get(f"rated_{key}"):
        with st.form(f"fb_{key}"):
            share = st.checkbox("Share my question and this answer with the policy owner, so they can check it",
                                value=rating == "down")
            if st.form_submit_button("Send feedback"):
                usage.log_feedback(user.role, rating, a["generator"], [s["chunk_id"] for s in a["sources"]],
                                   question=a["question"] if share else None, answer=a["text"] if share else None)
                st.session_state[f"rated_{key}"] = True
                st.toast("Thanks — feedback sent.")
                st.rerun()


def ask(question: str, topic: str, use_llm: bool) -> dict:
    t0 = time.time()
    try:
        a = answer(question, retriever, use_llm=use_llm, scope=None if topic == "policy" else BY_ID[topic].scope,
                   role=None if topic == "policy" else BY_ID[topic].role)
    except Exception as e:                  # log the error type only (no question text), then show the error
        usage.log_request(user.role, topic, "error", False, time.time() - t0, 0, type(e).__name__)
        raise
    secs = time.time() - t0
    d = asdict(a)
    d["topic"] = topic
    usage.log_request(user.role, topic, a.generator, a.found, secs, len(a.sources))
    return {"answer": d, "seconds": secs}


# ------------------------------------------------------------------ staff areas
def page_ask():
    st.markdown("## 💬 Ask the documents")
    st.caption("Answers come only from the policy documents and cite document, section and page. "
               "If the documents don't cover a question, the answer says so.")
    chat = st.session_state.setdefault("chat", [])        # this browser session only
    topic = st.segmented_control("Search in", list(TOPICS), format_func=TOPICS.get, default="policy",
                                 key="topic") or "policy"
    pending = None
    if not chat:
        cols = st.columns(2)
        for i, ex in enumerate(EXAMPLES):
            if cols[i % 2].button(ex, key=f"ex_{i}", use_container_width=True):
                pending = ex
    for i, msg in enumerate(chat):
        with st.chat_message(msg["role"]):
            if msg["role"] == "user":
                st.markdown(msg["content"])
            else:
                render_answer(msg, f"c{i}")
    prompt = st.chat_input("Ask about tickets, refunds, luggage, cycles, dogs, assistance, trains…") or pending
    if prompt:
        chat.append({"role": "user", "content": prompt})
        with st.spinner("Searching the documents…"):
            msg = ask(prompt, topic, llm_ready)
        chat.append({"role": "assistant", **msg})
        history.save(user, prompt, msg["answer"]["text"], [s for s in msg["answer"]["sources"] if s["cited"]])
        st.rerun()

    st.divider()
    c1, c2 = st.columns([3, 2])
    with c1:
        if history.enabled_for(user):
            st.caption(f"🔒 Your history is kept on this server for {cfg['chat_history_days']} days. Only you can see it.")
        else:
            st.caption("🔒 This conversation exists only in this browser session. Nothing is stored, and it is gone "
                       "when you sign out. Please don't type passenger names or contact details.")
    with c2:
        if chat and st.button("Clear conversation"):
            st.session_state["chat"] = []
            st.rerun()
    if cfg["allow_chat_history"]:
        with st.expander("My chat history"):
            keep = st.toggle("Keep my chat history on the server", value=user.keep_history,
                             help=f"Off by default. Stored for {cfg['chat_history_days']} days; only you can see it.")
            if keep != user.keep_history:
                auth.set_keep_history(user.username, keep)
                st.rerun()
            own = history.load_own(user)
            for e in own[:20]:
                st.markdown(f"**{e['question']}**  \n<small>{e['time'][:16].replace('T', ' ')}</small>",
                            unsafe_allow_html=True)
                st.caption(e["answer"][:300])
            if own and st.button("Delete my history"):
                history.clear_own(user)
                st.rerun()


def page_checks():
    st.markdown("## 🧭 Quick checks")
    st.caption("Describe the situation; the check lists the rules that apply, each with its exact source.")
    has_fleet = "fleet" in doc_meta() and library.doc_path(doc_meta()["fleet"]).exists()
    tabs = st.tabs(["🎫 Tickets on board", "♿ Assistance", "💷 Refunds & delays"] + (["🚆 Trains"] if has_fleet else []))
    with tabs[0]:
        situation = st.selectbox("What is the situation?", list(TICKET_SITUATIONS), format_func=TICKET_SITUATIONS.get,
                                 key="t_sit")
        reasons, split_calls, area_t, child_age = [], None, False, ""
        if situation == "no_ticket":
            st.markdown("**Did any of these apply at the departure station?**")
            reasons = [r for r, label in NO_TICKET_REASONS.items() if st.checkbox(label, key=f"t_r_{r}")]
        elif situation == "split_ticket":
            split_calls = st.radio("Does the train call at the station where the tickets change?", ["Yes", "No"],
                                   horizontal=True, key="t_split") == "Yes"
            area_t = st.checkbox("One of the tickets is a Season, zonal or other area ticket", key="t_area")
        elif situation == "child":
            child_age = st.radio("Age", ["under5", "5to15"], horizontal=True, key="t_age",
                                 format_func={"under5": "Under 5, with a ticket holder", "5to15": "5 to 15"}.get)
        scheme = st.radio("Penalty Fare scheme at this station?", ["unknown", "yes", "no"], horizontal=True, key="t_pf",
                          format_func={"unknown": "Don’t know", "yes": "Yes, notices displayed", "no": "No"}.get)
        st.divider()
        render_findings(advisors.ticket_advice(situation, reasons, scheme, split_calls, area_t, child_age))
    with tabs[1]:
        c1, c2 = st.columns(2)
        with c1:
            aid = st.selectbox("Mobility aid", ["none", "wheelchair", "powerchair", "scooter"], key="a_aid",
                               format_func={"none": "None", "wheelchair": "Manual wheelchair", "powerchair": "Powerchair",
                                            "scooter": "Mobility scooter"}.get)
            w = l = kg = tr = 0
            if aid != "none":
                w = st.number_input("Width (mm)", 0, 2000, 650, step=10, key="a_w")
                l = st.number_input("Length (mm)", 0, 3000, 1100, step=10, key="a_l")
                kg = st.number_input("Weight including user (kg)", 0, 600, 180, step=5, key="a_kg")
                tr = st.number_input("Turning radius (mm, 0 if unknown)", 0, 3000, 0, step=10, key="a_tr")
            dog = st.checkbox("Travelling with an assistance dog", key="a_dog")
            lug = st.number_input("Items of luggage needing help", 0, 10, 0, key="a_lug")
        with c2:
            booked = st.radio("Booked in advance?", ["Booked", "Unbooked"], horizontal=True, key="a_b") == "Booked"
            hours = st.number_input("Hours of notice before departure", 0.0, 168.0, 3.0, step=0.5, key="a_h") if booked else 0
            station = st.radio("Departure station", ["staffed", "unstaffed"], horizontal=True, key="a_st",
                               format_func=str.capitalize)
            core = st.checkbox("Boarding in the Thameslink Core (St Pancras – London Bridge)", key="a_core")
            other = st.checkbox("Journey includes another train operator", key="a_other")
            disr = st.checkbox("Disruption or rail replacement transport expected", key="a_dis")
        st.divider()
        render_findings(advisors.assistance_check(aid, w, l, kg, tr, booked, hours, station, core, other, lug, disr, dog))
    with tabs[2]:
        c1, c2 = st.columns(2)
        with c1:
            event = st.selectbox("What happened?", list(CLAIM_EVENTS), format_func=CLAIM_EVENTS.get, key="c_ev")
            ticket = st.selectbox("Ticket", list(TICKET_TYPES), format_func=TICKET_TYPES.get, key="c_t")
        with c2:
            minutes = st.number_input("Minutes late at destination", 0, 600, 75, step=5, key="c_m") if event == "late" else 0
            days = st.number_input("Days since the journey (or intended travel date)", 0, 365, 5, key="c_d")
            price = leg = None
            if event == "late" and ticket != "season":
                if ticket == "return_one":
                    leg = st.number_input("Price of the delayed leg (£, optional)", 0.0, 2000.0, 0.0, step=0.5, key="c_leg") or None
                else:
                    price = st.number_input("Ticket price paid (£, optional)", 0.0, 2000.0, 0.0, step=0.5, key="c_p") or None
            refund_already = st.checkbox("Passenger already received a full refund for not travelling",
                                         key="c_ref") if event == "late" else False
        st.divider()
        render_findings(advisors.claim_advice(event, ticket, minutes, days, price, leg, refund_already))
    if has_fleet:
        with tabs[3]:
            page_trains()


def page_trains():
    fleet = advisors.load_fleet()
    labels = [advisors.fleet_label(r) for r in fleet]

    def mark(v: str) -> str:
        return "✅ Yes" if v == "Yes" else "❌ No" if v == "No" else f"❔ {v}"

    mode = st.segmented_control("View", ["Profile", "Compare", "Find a feature"], default="Profile", key="f_mode") or "Profile"
    if mode == "Profile":
        pick = st.selectbox("Train class", labels, key="f_pick")
        row = fleet[labels.index(pick)]
        st.markdown(f"**{pick}** · Routes: {row['routes']}")
        cols = st.columns(2)
        for i, (key, name) in enumerate(FLEET_FIELDS):
            cols[i % 2].markdown(f"{mark(row[key])[:2]} **{name}** — {mark(row[key])[2:].strip()}")
        st.caption(f"Source: Fleet Accessibility Information ({doc_meta()['fleet'].get('version', '')}), p. 1")
        render_findings([
            Finding("info", "Mobility aid limits", "Width 700 mm, length 1,200 mm, weight 300 kg including the user, "
                    "turning radius 1,500 mm.", [R["A.5-dims"]]),
            Finding("info", "Boarding", "Staff provide manual ramps; in the Thameslink Core the accessible carriage stops at "
                    "a level boarding area.", [R["A.1-ramps"], R["A.1-core"]]),
        ])
    elif mode == "Compare":
        chosen = st.multiselect("Trains to compare", labels, default=labels[:2], key="f_cmp")
        if chosen:
            rows = [fleet[labels.index(c)] for c in chosen]
            st.dataframe(pd.DataFrame({c: [mark(r[k]) for k, _ in FLEET_FIELDS] for c, r in zip(chosen, rows)},
                                      index=[n for _, n in FLEET_FIELDS]), use_container_width=True)
    else:
        feat = st.selectbox("Feature", [k for k, _ in FLEET_FIELDS], format_func=dict(FLEET_FIELDS).get, key="f_feat")
        groups = {"Yes": [], "No": [], "Other": []}
        for r in fleet:
            groups[r[feat] if r[feat] in ("Yes", "No") else "Other"].append(
                advisors.fleet_label(r) + ("" if r[feat] in ("Yes", "No") else f" ({r[feat]})"))
        c1, c2, c3 = st.columns(3)
        c1.markdown("**✅ Has it**  \n" + ("  \n".join(groups["Yes"]) or "—"))
        c2.markdown("**❌ Does not**  \n" + ("  \n".join(groups["No"]) or "—"))
        c3.markdown("**❔ Conditional**  \n" + ("  \n".join(groups["Other"]) or "—"))


def page_docs():
    st.markdown("## 📄 Documents")
    st.caption("Everything the assistant knows comes from these documents. If the assistant is unavailable, "
               "open the PDF directly.")
    for row in library.overview():
        with st.container(border=True):
            c1, c2 = st.columns([5, 2])
            c1.markdown(f"**{row['title']}**  \n{row['version']} · {row['publisher']} · "
                        f"{row['pages'] or '—'} pages · updated {row['updated'] or '—'}")
            with c2:
                pdf_button(row["id"], "Open PDF", f"doc_{row['id']}")


# ------------------------------------------------------------------ policy owner areas
def after_change(trigger: str):
    """Policy owner story: the test set is rerun after every document change."""
    st.cache_resource.clear()
    fresh = Retriever.load()
    with st.spinner("Re-running the test set on the updated documents…"):
        rec = testset.run(fresh, trigger, by=user.username, use_llm=False)
    st.session_state["last_test_run"] = rec


def show_run_summary(rec: dict):
    t = rec["totals"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Questions", rec["questions"])
    c2.metric("Correct", t["correct"])
    c3.metric("Wrong answers", t["wrong"], help="Target: 0. A wrong answer is worse than “not found”.")
    c4.metric("Not found", t["not found"], help="Answerable questions the assistant said it couldn't find.")
    st.caption(f"{rec['time'][:16].replace('T', ' ')} UTC · {rec['trigger']} · {rec['method']} · {rec['seconds']} s")
    if t["wrong"]:
        st.error(f"{t['wrong']} wrong answer(s). See the Test set area for details.")
    else:
        st.success("No wrong answers on the test set.")


def page_manage():
    st.markdown("## 🗂️ Manage documents")
    editor = user.can("edit_documents")
    if not editor:
        st.info("You can see the document library and its history. To upload or remove documents, ask a document "
                "editor to give you editing rights.")
    msg = st.session_state.pop("change_msg", None)
    if msg:
        (st.success if msg[0] == "ok" else st.error)(msg[1])
    rec = st.session_state.pop("last_test_run", None)
    if rec:
        st.markdown("**Test set after your change**")
        show_run_summary(rec)
    tabs = st.tabs(["📚 Overview", "⬆️ Upload or replace", "🗑️ Remove", "🕘 Change history", "✏️ Document editors"])
    with tabs[0]:
        rows = library.overview()
        st.dataframe(pd.DataFrame(rows).drop(columns=["id", "protected"]).rename(columns={
            "title": "Title", "short": "Short name", "version": "Version", "publisher": "Publisher", "kind": "Type",
            "pages": "Pages", "passages": "Passages", "updated": "Last update", "updated_by": "Updated by",
            "size_kb": "Size (KB)"}), use_container_width=True, hide_index=True)
        st.caption(f"{sum(r['passages'] for r in rows)} passages in the search index. "
                   "A passage is a short section of a document that answers can cite.")
    with tabs[1]:
        if not editor:
            st.caption("Only document editors can upload.")
        else:
            docs = [d for d in library.registry() if d.get("kind", "pdf") == "pdf"]
            mode = st.radio("What are you uploading?", ["new", "replace"], horizontal=True, key="up_mode",
                            format_func={"new": "A new document", "replace": "A new version of a document"}.get)
            old = None
            if mode == "replace":
                old = st.selectbox("Document to replace", docs, format_func=lambda d: f"{d['short']} ({d.get('version', '')})",
                                   key="up_old")
                if old and library.is_protected(old):
                    st.warning("This document is protected in the public demo; upload it as a new document instead.")
            with st.form("upload", clear_on_submit=False):
                f = st.file_uploader("PDF file", type=["pdf"])
                c1, c2 = st.columns(2)
                title = c1.text_input("Title", value=old["title"] if old else "")
                short = c2.text_input("Short name (used in citations)", value=old["short"] if old else "")
                ver = c1.text_input("Version or date", placeholder="e.g. Version 7, from 1 March 2027")
                credit = c2.text_input("Publisher", value=old.get("credit", "") if old else "")
                style = c1.selectbox("How are sections numbered?", list(library.SECTION_STYLES),
                                     index=list(library.SECTION_STYLES).index(old.get("section_style", "heading")) if old else 0,
                                     format_func=library.SECTION_STYLES.get)
                skip = c2.text_input("Pages to skip (cover, contents)", value=",".join(map(str, old.get("skip_pages", []))) if old else "",
                                     placeholder="e.g. 1,2")
                go = st.form_submit_button("Upload and index", type="primary")
            if go:
                if f is None:
                    st.error("Choose a PDF file first.")
                else:
                    try:
                        pages = [int(p) for p in skip.replace(" ", "").split(",") if p]
                        with st.spinner("Checking and indexing the document…"):
                            res = library.add_document(user, f.getvalue(), title, short, ver, credit, style, pages,
                                                       replace_id=old["id"] if old else None)
                        st.session_state["change_msg"] = ("ok",
                            f"{'Replaced' if res['replaced'] else 'Added'} **{res['doc']['short']}**: "
                            f"{res['pages']} pages, {res['passages']} passages. "
                            + ("The old version is no longer used." if res["replaced"] else "Staff can use it now."))
                        after_change(f"{'replaced' if res['replaced'] else 'added'} {res['doc']['short']}")
                        st.rerun()
                    except ValueError:
                        st.error("Pages to skip: use numbers separated by commas, e.g. 1,2")
                    except (library.LibraryError, auth.PermissionDenied) as e:
                        st.error(f"Upload failed: {e}")
            st.caption(f"PDFs with selectable text, up to {config.MAX_UPLOAD_MB:.0f} MB. A restore point is taken before "
                       "every change. If anything fails, the previous version stays in use.")
    with tabs[2]:
        if not editor:
            st.caption("Only document editors can remove documents.")
        else:
            docs = library.registry()
            if not docs:
                st.info("The library is empty.")
            else:
                doc = st.selectbox("Document to remove", docs, format_func=lambda d: f"{d['short']} ({d.get('version', '')})",
                                   key="rm_doc")
                st.warning(f"Removing **{doc['short']}** deletes the file, its passages, and its copies in restore points "
                           "and saved chat histories. Staff will no longer get answers from it.")
                confirm = st.text_input(f"Type **{doc['short']}** to confirm", key="rm_confirm")
                if st.button("Remove document", type="primary", disabled=confirm.strip().lower() != doc["short"].lower()):
                    try:
                        left = library.remove_document(user, doc["id"], confirm)
                        st.session_state["change_msg"] = (
                            ("error", f"{doc['short']} removed, but copies remain: " + "; ".join(left)) if left else
                            ("ok", f"**{doc['short']}** removed everywhere: file, passages, embeddings, restore points "
                                   "and saved chat histories."))
                        after_change(f"removed {doc['short']}")
                        st.rerun()
                    except (library.LibraryError, auth.PermissionDenied) as e:
                        st.error(str(e))
    with tabs[3]:
        events = [e for e in auth.read_audit(1000) if e["event"].startswith(("document_", "library_", "testset_"))]
        if events:
            st.dataframe(pd.DataFrame(events)[["time", "event", "user", "by", "detail"]].rename(
                columns={"user": "document / user"}), use_container_width=True, hide_index=True)
        else:
            st.info("No changes yet.")
    with tabs[4]:
        owners = [u for u in auth.load_users().values() if u.role == "policy_owner"]
        st.markdown("Only named document editors may upload, replace or remove documents. IT creates the accounts; "
                    "document editors decide who else may edit.")
        for o in owners:
            c1, c2 = st.columns([4, 2])
            c1.markdown(f"**{o.name}** ({o.username}) · {'✏️ editor' if o.doc_editor else 'view only'}"
                        + ("" if o.active else " · disabled"))
            if user.can("manage_editors"):
                label = "Remove editing rights" if o.doc_editor else "Make document editor"
                if c2.button(label, key=f"ed_{o.username}"):
                    try:
                        auth.set_doc_editor(o.username, not o.doc_editor, by=user.username)
                        st.rerun()
                    except auth.AuthError as e:
                        st.error(str(e))


def page_inspect():
    st.markdown("## 🔍 Check answers")
    tabs = st.tabs(["🔍 Answer inspector", "👎 Staff feedback", "📏 Quick-check rules"])
    with tabs[0]:
        st.caption("Ask a question the way staff would. You see the answer and every passage it was based on, "
                   "with its search scores, so you can confirm what the policy says.")
        c1, c2, c3 = st.columns([3, 1, 1])
        q = c1.text_input("Question", key="insp_q", placeholder="e.g. Can a passenger take a dog on the train?")
        method = c2.selectbox("Answer method", ["Quote documents"] + (["Language model"] if llm_ready else []))
        k = c3.slider("Passages", 2, 10, config.TOP_K)
        if q:
            a = answer(q, retriever, k=k, use_llm=method == "Language model")
            st.markdown("**Answer**")
            st.markdown(a.text)
            hits, _ = retriever.search(q, k=k)
            st.markdown("**Passages found, best first**")
            for s, h in zip(a.sources, hits):
                with st.container(border=True):
                    st.markdown(f"[{s.n}] **{s.doc_short}** — {s.label} ({s.pages})"
                                + (" · ✅ cited in the answer" if s.cited else ""))
                    st.caption(f"Keyword rank {h.bm25_rank + 1 if h.bm25_rank is not None else '—'} · "
                               f"meaning rank {h.dense_rank + 1 if h.dense_rank is not None else '—'} · "
                               f"question words covered {coverage(q, s.text, retriever):.0%}")
                    st.write(s.text)
    with tabs[1]:
        fb = usage.read_feedback(user)
        down = [f for f in fb if f["rating"] == "down"]
        c1, c2 = st.columns(2)
        c1.metric("Answers rated 👎", f"{len(down)} of {len(fb)}")
        shared = [f for f in fb if "question" in f]
        c2.metric("Shared with you", len(shared), help="Staff choose whether to share the question and answer.")
        if shared:
            st.dataframe(pd.DataFrame(shared)[["time", "rating", "question", "answer"]], use_container_width=True,
                         hide_index=True)
        st.caption("Add questions that went wrong to the test set, then fix the document or the rule.")
    with tabs[2]:
        n = len(advisors.ALL_CITES)
        if problems:
            st.error(f"{len(problems)} of {n} rules no longer match the documents. Staff see a “being reviewed” "
                     "notice instead of these rules until they are updated.")
            for p in problems:
                st.markdown(f"- {p}")
        else:
            st.success(f"All {n} quick-check rules quote text that is still in the current documents.")


def page_tests():
    st.markdown("## 🧪 Test set")
    st.caption("Real staff questions with the facts a correct answer must mention. The set is rerun automatically "
               "after every document change. Target: no wrong answers.")
    tabs = st.tabs(["📊 Latest results", "✏️ Questions", "🕘 Run history"])
    runs = testset.runs()
    with tabs[0]:
        c1, c2 = st.columns([1, 1])
        if c1.button("Run the test set now", type="primary"):
            with st.spinner("Running…"):
                testset.run(retriever, "run by hand", by=user.username, use_llm=False)
            st.rerun()
        if llm_ready and c2.button("Run with the language model (slower)"):
            with st.spinner("Running with the language model…"):
                testset.run(retriever, "run by hand", by=user.username, use_llm=True)
            st.rerun()
        if runs:
            show_run_summary(runs[0])
            df = pd.DataFrame(runs[0]["results"])
            order = {"wrong": 0, "not found": 1, "correct": 2}
            df = df.sort_values("outcome", key=lambda s: s.map(order))
            df["outcome"] = df["outcome"].map({"correct": "✅ correct", "wrong": "❌ wrong", "not found": "➖ not found"})
            df["right_source"] = df["right_source"].map({True: "✓", False: "✗"}).fillna("")
            st.dataframe(df[["id", "outcome", "question", "answer", "right_source", "sources"]].rename(columns={
                "right_source": "right source"}), use_container_width=True, hide_index=True)
        else:
            st.info("No run yet. Click “Run the test set now”.")
    with tabs[1]:
        qs = testset.load()
        df = pd.DataFrame([{"id": q["id"], "question": q["question"], "answerable": q["answerable"],
                            "documents": ", ".join(q["docs"]), "must mention": "; ".join(q["facts"]),
                            "source must contain": q["evidence"]} for q in qs])
        st.caption("“Must mention”: words the answer must contain, separated by ; (use | for alternatives, e.g. "
                   "“two hours|2 hours”). Untick “answerable” for questions the documents don't cover.")
        edited = st.data_editor(df, num_rows="dynamic", use_container_width=True, hide_index=True, key="ts_edit",
                                column_config={"answerable": st.column_config.CheckboxColumn(default=True)})
        if st.button("Save questions"):
            try:
                testset.save(user, [{"id": r["id"], "question": r["question"], "answerable": bool(r["answerable"]),
                                     "docs": r["documents"] or "", "facts": r["must mention"] or "",
                                     "evidence": r["source must contain"] or ""}
                                    for r in edited.fillna("").to_dict("records")])
                st.success("Saved. Run the test set to see the effect.")
            except (ValueError, auth.PermissionDenied) as e:
                st.error(str(e))
    with tabs[2]:
        if runs:
            st.dataframe(pd.DataFrame([{"time": r["time"][:16].replace("T", " "), "trigger": r["trigger"],
                                        "by": r.get("by", ""), "method": r["method"], "questions": r["questions"],
                                        **r["totals"]} for r in runs]), use_container_width=True, hide_index=True)


# ------------------------------------------------------------------ IT & data protection areas
def page_accounts():
    st.markdown("## 👥 Accounts")
    users = auth.load_users()
    st.dataframe(pd.DataFrame([{
        "Username": u.username, "Name": u.name, "Role": auth.ROLES[u.role]["name"],
        "Document editor": "✏️" if u.doc_editor else "", "Status": "active" if u.active else "disabled",
        "Must change password": u.must_change, "Last sign-in": u.last_login or "—"} for u in users.values()]),
        use_container_width=True, hide_index=True)
    st.caption("Document editing rights are given by document editors (policy owners), not by IT.")
    c1, c2 = st.columns(2)
    with c1, st.form("add_user", clear_on_submit=True):
        st.markdown("**Add a user**")
        new_u = st.text_input("Username", placeholder="e.g. jsmith")
        new_n = st.text_input("Full name")
        new_r = st.selectbox("Role", list(auth.ROLES), format_func=lambda r: f"{auth.ROLES[r]['name']} — {auth.ROLES[r]['group']}")
        if st.form_submit_button("Create account"):
            try:
                pw = auth.create_user(new_u, new_n, new_r, by=user.username)
                st.success(f"Account **{new_u.strip().lower()}** created. Temporary password (shown once):")
                st.code(pw)
            except auth.AuthError as e:
                st.error(str(e))
    with c2:
        st.markdown("**Manage a user**")
        target = st.selectbox("User", list(users), key="adm_target")
        if target:
            t = users[target]
            role = st.selectbox("Role", list(auth.ROLES), index=list(auth.ROLES).index(t.role),
                                format_func=lambda r: auth.ROLES[r]["name"], key=f"adm_role_{target}")
            b1, b2, b3 = st.columns(3)
            try:
                if b1.button("Save role", key="adm_save", disabled=role == t.role):
                    auth.update_user(target, by=user.username, role=role)
                    st.rerun()
                if b2.button("Disable" if t.active else "Enable", key="adm_toggle", disabled=target == user.username):
                    auth.update_user(target, by=user.username, active=not t.active)
                    st.rerun()
                if b3.button("Reset password", key="adm_reset"):
                    st.success(f"New temporary password for **{target}** (shown once):")
                    st.code(auth.reset_password(target, by=user.username))
            except auth.AuthError as e:
                st.error(str(e))


def page_logs():
    st.markdown("## 📜 Activity & logs")
    tabs = st.tabs(["⏱️ Requests & response time", "📜 Audit log", "ℹ️ What is logged"])
    with tabs[0]:
        entries = usage.read_usage()
        s = usage.usage_stats(entries)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Answers", s["requests"])
        c2.metric("Median time", f"{s['median']:.1f} s")
        c3.metric("95% answered within", f"{s['p95']:.1f} s", help=f"Agreed target: {s['target']} s")
        c4.metric("Within target", f"{s['within_target']:.0%}")
        if entries:
            st.dataframe(pd.DataFrame(entries), use_container_width=True, hide_index=True)
        st.caption("No usernames, questions or answers are in this log.")
    with tabs[1]:
        log = auth.read_audit(500)
        st.dataframe(pd.DataFrame(log) if log else pd.DataFrame(columns=["time", "event", "user", "by", "detail"]),
                     use_container_width=True, hide_index=True)
        st.caption("Sign-ins, account changes, document changes, settings changes and refused actions. Passwords are never logged.")
    with tabs[2]:
        st.table(pd.DataFrame([
            ("Request log", "time, role, topic, method, found or not, seconds, error type", "No user, question or answer",
             f"{cfg['usage_log_days']} days"),
            ("Audit log", "sign-ins, account / document / settings changes, refused actions", "No passwords",
             f"{cfg['audit_log_days']} days"),
            ("Answer ratings", "👍/👎, passages used; question and answer only if staff tick “share”", "Opt-in content",
             f"{cfg['feedback_days']} days"),
            ("Conversations", "browser session only", "Not stored, unless the user switches on history"
             + (" (allowed)" if cfg["allow_chat_history"] else " (switched off by IT)"), f"{cfg['chat_history_days']} days"),
        ], columns=["Log", "Contains", "Never contains", "Kept for"]))
        if st.button("Delete expired entries now"):
            st.success(f"Removed: {usage.purge()}")


def page_privacy():
    st.markdown("## 🔒 Privacy & on-premise")
    tabs = st.tabs(["🛰️ On-premise check", "⚙️ Settings"])
    with tabs[0]:
        host = urlparse(config.OLLAMA_URL).hostname or ""
        st.table(pd.DataFrame([
            ("Language model", f"Ollama at {config.OLLAMA_URL}",
             "✅ inside the network" if netguard.is_internal(host) else "⚠️ outside the network"),
            ("Documents, index, accounts, logs", "files on this server", "✅ local"),
            ("Cloud AI services", "none", "✅"),
            ("Usage statistics to Streamlit", "switched off in .streamlit/config.toml", "✅"),
            ("Network guard", {"block": "refuses connections outside the network",
                               "monitor": "records connections outside the network (public demo)",
                               "off": "switched off"}[netguard.STATE["mode"]],
             "✅" if netguard.STATE["mode"] == "block" else "⚠️"),
        ], columns=["Component", "Where", "Status"]))
        attempts = netguard.external_attempts()
        st.markdown(f"**Connections outside the network since start:** {len(attempts)}")
        if attempts:
            st.dataframe(pd.DataFrame(attempts), use_container_width=True, hide_index=True)
        if st.button("Run the on-premise check", type="primary"):
            with netguard.strict() as g, st.spinner("Answering a test question with external connections blocked…"):
                a = answer("What is a passenger entitled to if their train is cancelled?", retriever, use_llm=llm_ready)
            if not g.attempts and a.found:
                st.success(f"✅ The full workflow works with external connections blocked: search and "
                           f"{'language model' if llm_ready else 'quoted answer'} gave a cited answer, and no external "
                           "connection was attempted.")
            else:
                st.error(f"External connection attempts: {g.attempts}. Answer found: {a.found}")
    with tabs[1]:
        with st.form("settings"):
            new = {}
            for key, label in settings.LABELS.items():
                if isinstance(settings.DEFAULTS[key], bool):
                    new[key] = st.toggle(label, value=cfg[key])
                else:
                    new[key] = st.number_input(label, min_value=1, max_value=3650, value=int(cfg[key]))
            if st.form_submit_button("Save settings", type="primary"):
                try:
                    settings.update(user, **new)
                    st.success("Saved. Every change is in the audit log.")
                    st.rerun()
                except (ValueError, auth.PermissionDenied) as e:
                    st.error(str(e))


def page_backups():
    st.markdown("## 💾 Backups & recovery")
    tabs = st.tabs(["💾 Restore points", "📘 Recovery steps"])
    with tabs[0]:
        st.caption(f"A restore point of the document library (documents, passages, embeddings) is taken before every "
                   f"document change; the last {config.SNAPSHOT_KEEP} are kept. Removed documents are deleted from restore "
                   "points too.")
        snaps = library.snapshots()
        if st.button("Create a restore point now"):
            library.snapshot("taken by hand", user.username)
            st.rerun()
        if snaps:
            st.dataframe(pd.DataFrame([{"id": s["id"], "time": s["time"][:16].replace("T", " "), "reason": s["reason"],
                                        "by": s["by"], "documents": ", ".join(s["documents"]),
                                        "app version": s.get("version", "")} for s in snaps]),
                         use_container_width=True, hide_index=True)
            pick = st.selectbox("Restore point", [s["id"] for s in snaps],
                                format_func=lambda i: next(f"{s['time'][:16].replace('T', ' ')} — {s['reason']}" for s in snaps if s["id"] == i))
            ok = st.checkbox("I understand that the document library goes back to this point (the current state is saved first)")
            if st.button("Restore", type="primary", disabled=not ok):
                try:
                    library.restore(user, pick)
                    st.cache_resource.clear()
                    st.success("Restored.")
                    st.rerun()
                except (library.LibraryError, auth.PermissionDenied) as e:
                    st.error(str(e))
        else:
            st.info("No restore points yet.")
    with tabs[1]:
        st.markdown((config.ROOT / "RECOVERY.md").read_text(encoding="utf-8"))


def page_system():
    st.markdown("## 🛠️ System")
    import platform
    import importlib.metadata as md

    def v(pkg):
        try:
            return md.version(pkg)
        except md.PackageNotFoundError:
            return "—"
    st.table(pd.DataFrame([
        ("App version", config.APP_VERSION), ("Python", platform.python_version()),
        ("Streamlit / pdfplumber / rank-bm25", f"{v('streamlit')} / {v('pdfplumber')} / {v('rank-bm25')}"),
        ("Chat model", f"{config.CHAT_MODEL} ({'ready' if llm_ready else 'not available'})"),
        ("Embedding model", f"{config.EMBED_MODEL} ({'in use' if retriever.has_embeddings else 'not in use'})"),
        ("Documents / passages", f"{len(library.registry())} / {len(retriever.chunks)}"),
        ("Document library", str(config.DATA_DIR)), ("Accounts, logs, restore points", str(config.STATE_DIR)),
        ("Quick-check rules matching the documents", f"{len(advisors.ALL_CITES) - len(problems)} of {len(advisors.ALL_CITES)}"),
    ], columns=["Item", "Value"]))
    c1, c2 = st.columns(2)
    if c1.button("Rebuild the search index"):
        with st.spinner("Rebuilding…"):
            n = library.build_index()
        auth.audit("index_rebuilt", "", by=user.username, detail=f"{n} passages")
        st.cache_resource.clear()
        st.success(f"Rebuilt: {n} passages.")
    if c2.button("Rebuild meaning search (embeddings)", disabled=not ollama_client.has_model(config.EMBED_MODEL, models or [])):
        with st.spinner("Embedding all passages…"):
            library._refresh_embeddings(library.load_chunks())
        st.cache_resource.clear()
        st.success("Done.")


PAGES = {"ask": page_ask, "checks": page_checks, "docs": page_docs, "manage": page_manage, "inspect": page_inspect,
         "tests": page_tests, "accounts": page_accounts, "logs": page_logs, "privacy": page_privacy,
         "backups": page_backups, "system": page_system}
if area not in AREAS or not user.can(AREAS[area][1]):           # enforced on the server, not only hidden in the menu
    auth.audit("permission_denied", user.username, by=user.username, detail=f"area {area}")
    st.error("You do not have access to this area.")
    st.stop()
PAGES[area]()
st.caption("Prototype · answers and checks come only from the published documents. Always check the cited source "
           "before acting; the decision stays with you.")

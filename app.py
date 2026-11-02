"""Thameslink Frontline Assistant — five use cases, one area per user group.

Run:  streamlit run app.py
Accounts:  python -m rag.auth --help
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from urllib.parse import urlparse

import pandas as pd
import streamlit as st
import yaml

from rag import advisors, auth, caselog, config, ollama_client
from rag.advisors import (CLAIM_EVENTS, FLEET_FIELDS, NO_TICKET_REASONS, R, TICKET_SITUATIONS, TICKET_TYPES,
                          Finding)
from rag.answer import answer
from rag.retrieve import Retriever
from rag.usecases import BY_ID, USE_CASES

st.set_page_config(page_title="Frontline Assistant", page_icon="🚆", layout="wide")


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

ICON = {"ok": "✅", "warn": "⚠️", "stop": "⛔", "info": "ℹ️"}


# ------------------------------------------------------------------ first start
@st.cache_resource(show_spinner="First start: fetching the documents and building the search index (about a minute) …")
def prepare_index() -> str:
    """On a fresh server (e.g. Streamlit Community Cloud) download the documents if needed and build the index.
    Returns an error message, or "" when the index is ready."""
    if (config.INDEX_DIR / "chunks.jsonl").exists():
        return ""
    import importlib.util
    from rag import ingest
    try:
        missing = [d for d in ingest.load_documents() if d["kind"] == "pdf" and not (config.DATA_DIR / d["file"]).exists()]
        if missing:
            spec = importlib.util.spec_from_file_location("download_documents", config.ROOT / "scripts" / "download_documents.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.main()
        ingest.save_chunks(ingest.build_chunks())
        return ""
    except Exception as e:                          # missing document, network error, ...
        return f"Could not prepare the documents ({e}). See the README, \"Get the documents\"."


def prepare_demo() -> None:
    """Public demo: create the demo accounts on first start (they then appear on the sign-in page)."""
    if demo_mode() and not auth.load_users():
        auth.create_demo_accounts()


# ------------------------------------------------------------------ cached resources
@st.cache_resource
def load_retriever():
    return Retriever.load()


@st.cache_data
def load_docs():
    with open(config.DOCUMENTS_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)["documents"]


@st.cache_data(ttl=15)
def ollama_status():
    return ollama_client.available_models()


@st.cache_data
def citation_problems() -> list[str]:
    return advisors.check_citations(load_retriever().chunks)


def log_feedback(entry: dict):
    entry["user"] = st.session_state.get("user", "")
    with open(config.STATE_DIR / "feedback.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


problem = prepare_index()
if problem:
    st.error(problem)
    st.stop()
prepare_demo()
try:
    retriever = load_retriever()
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()

models = ollama_status()
llm_ready = ollama_client.has_model(config.CHAT_MODEL, models or [])


# ------------------------------------------------------------------ sign-in
def sign_out(reason: str = ""):
    if st.session_state.get("user"):
        auth.audit("logout", st.session_state["user"], detail=reason)
    st.session_state.clear()
    if reason:
        st.session_state["signout_reason"] = reason


def demo_accounts_box():
    """Dropdown of the demo accounts with copyable username and password (only if demo accounts exist)."""
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
        st.caption(f"Opens: {', '.join(BY_ID[u].short for u in auth.ROLES[pick['role']]['use_cases'])}"
                   + (" and the administration area" if auth.ROLES[pick['role']].get('admin') else ""))
        if st.button("Fill in the sign-in form", use_container_width=True):
            st.session_state["login_username"] = pick["username"]
            st.session_state["login_password"] = pick["password"]
            st.rerun()
        st.caption("Copy with the icon at the right of each box. Demo data only.")


def login_page():
    _, mid, _ = st.columns([1, 1.3, 1])
    with mid:
        st.markdown("## 🚆 Frontline Assistant")
        st.caption("Staff sign-in · independent student prototype using public rail documents (not affiliated with Thameslink / GTR)")
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
                    user = auth.authenticate(username, password)
                    st.session_state["user"] = user.username
                    st.session_state["last_active"] = time.time()
                    st.rerun()
                except auth.AuthError as e:
                    st.error(str(e))
        st.caption("Each user group signs in to its own area. Forgotten password: ask your administrator.")


def change_password_page(user: auth.User):
    _, mid, _ = st.columns([1, 1.3, 1])
    with mid:
        st.markdown(f"## Welcome, {user.name}")
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
                        auth.change_password(user.username, old, new)
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
if user is None or not user.active:          # account removed or disabled while signed in
    sign_out("disabled")
    st.rerun()
if user.must_change:
    change_password_page(user)
    st.stop()

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.markdown("### 🚆 Frontline Assistant")
    st.markdown(f"**{user.name}**  \n{auth.ROLES[user.role]['name']}")
    areas = (["admin"] if user.is_admin else []) + list(user.use_cases)     # first entry = landing page
    choice = st.radio("Your areas", areas, label_visibility="collapsed",
                      format_func=lambda i: "⚙️ Administration" if i == "admin"
                      else f"{BY_ID[i].icon} {BY_ID[i].number}. {BY_ID[i].short}")
    uc = BY_ID.get(choice)
    if st.button("Sign out", use_container_width=True):
        sign_out()
        st.rerun()

    st.divider()
    st.caption("STATUS")
    if models is None:
        st.warning("Ollama is not running — chat answers quote the documents directly. "
                   "The rule-based advisors work without it.")
    elif not llm_ready:
        st.warning(f"Ollama is running, but `{config.CHAT_MODEL}` is not installed: `ollama pull {config.CHAT_MODEL}`")
    else:
        st.success(f"Language model: `{config.CHAT_MODEL}` (local)")
    problems = citation_problems()
    if problems:
        st.error(f"{len(problems)} advisor rule(s) no longer match the documents — see README.")
    else:
        st.caption(f"✓ All {len(advisors.ALL_CITES)} advisor rules verified against the documents")

    with st.expander("Chat settings"):
        answer_mode = st.radio("Answer style", ["Language model", "Quote documents only"],
                               index=0 if llm_ready else 1, disabled=not llm_ready)
        retrieval_mode = st.selectbox("Search method", ["auto", "hybrid", "bm25", "dense"])
        k = st.slider("Passages given to the model", 2, 10, config.TOP_K)
    with st.expander("Documents"):
        for d in load_docs():
            st.markdown(f"**{d['title']}**  \n{d['version']} · {d['credit']}")


# ------------------------------------------------------------------ shared components
def header(u):
    st.markdown(f"## {u.icon} {u.title}")
    st.caption(f"Use case {u.number} · Users: {u.users} · Sources: {u.sources}")
    st.markdown(f"> **User story.** {u.story}")
    if u.extra_story:
        st.markdown(f"> **Also:** {u.extra_story}")
    with st.expander("The problem"):
        st.write(u.problem)


def render_findings(findings: list[Finding]):
    if not findings:
        st.info("Nothing to check yet.")
        return
    for f in findings:
        with st.container(border=True):
            st.markdown(f"{ICON[f.level]} **{f.title}**")
            st.write(f.text)
            refs = []
            for c in f.cites:
                chunk = advisors.resolve(c, retriever.chunks)
                where = f" ({chunk['doc_short']}, p. {chunk['page_start']})" if chunk else " (source not found!)"
                refs.append(f"{c.ref}{where}")
            st.caption("Source: " + " · ".join(dict.fromkeys(refs)))
            with st.popover("Show the exact wording"):
                for c in f.cites:
                    st.markdown(f"**{c.ref}** — “{c.quote}”")
    st.caption("These checks restate the documents. The decision stays with you.")


def render_answer(msg: dict, key: str):
    a = msg["answer"]
    st.markdown(a["text"])
    for note in a["notes"]:
        st.info(note)
    cited = [s for s in a["sources"] if s["cited"]]
    if cited:
        st.markdown("**Sources**  \n" + "  \n".join(
            f"[{s['n']}] {s['doc_short']} — {s['label']} ({s['pages']})" for s in cited))
    if a["sources"]:
        with st.expander(f"Show passage text ({len(a['sources'])} passages searched)"):
            for s in a["sources"]:
                st.markdown(f"**[{s['n']}] {s['doc_short']}** — {s['label']} ({s['pages']})"
                            + ("" if s["cited"] else " · not cited"))
                st.caption(s["text"][:900] + ("…" if len(s["text"]) > 900 else ""))
    c1, c2, c3 = st.columns([1, 1, 8])
    for col, rating, icon in ((c1, "up", "👍"), (c2, "down", "👎")):
        if col.button(icon, key=f"{rating}{key}"):
            log_feedback({"time": datetime.now(timezone.utc).isoformat(), "use_case": a.get("use_case"),
                          "question": a["question"], "answer": a["text"], "rating": rating, "generator": a["generator"]})
            st.toast("Thanks — feedback saved.")
    c3.caption(f"{a['generator']} · search: {a['retrieval_mode']} · {msg.get('seconds', 0):.1f}s")


def render_chat(u, placeholder: str):
    key = f"chat_{u.id}"
    st.session_state.setdefault(key, [])
    history = st.session_state[key]
    st.caption(f"Searches only: {u.sources}.")
    pending = None
    if not history:
        cols = st.columns(2)
        for i, ex in enumerate(u.examples):
            if cols[i % 2].button(ex, key=f"ex_{u.id}_{i}", use_container_width=True):
                pending = ex
    for i, msg in enumerate(history):
        with st.chat_message(msg["role"]):
            if msg["role"] == "user":
                st.markdown(msg["content"])
            else:
                render_answer(msg, f"{u.id}{i}")
    prompt = st.chat_input(placeholder, key=f"input_{u.id}") or pending
    if prompt:
        history.append({"role": "user", "content": prompt})
        t0 = time.time()
        with st.spinner("Searching the documents…"):
            a = answer(prompt, retriever, k=k, retrieval_mode=retrieval_mode,
                       use_llm=(answer_mode == "Language model" and llm_ready), scope=u.scope, role=u.role)
        d = asdict(a)
        d["use_case"] = u.id
        history.append({"role": "assistant", "answer": d, "seconds": time.time() - t0})
        st.rerun()
    if history and st.button("Clear conversation", key=f"clear_{u.id}"):
        st.session_state[key] = []
        st.rerun()


# ------------------------------------------------------------------ use case pages
def page_policy(u):
    render_chat(u, "Ask about refunds, tickets, luggage, accessibility, assistance…")


def page_tickets(u):
    tab1, tab2, tab3 = st.tabs(["🧭 Situation advisor", "🗂️ Similar past cases", "💬 Ask the Conditions"])
    with tab1:
        st.markdown("Describe the situation at the ticket check. The advisor shows what the Conditions of Travel allow.")
        situation = st.selectbox("What is the situation?", list(TICKET_SITUATIONS),
                                 format_func=TICKET_SITUATIONS.get, key="t_sit")
        reasons, split_calls, area, child_age = [], None, False, ""
        if situation == "no_ticket":
            st.markdown("**Did any of these apply at the departure station?**")
            reasons = [r for r, label in NO_TICKET_REASONS.items() if st.checkbox(label, key=f"t_r_{r}")]
        elif situation == "split_ticket":
            split_calls = st.radio("Does the train call at the station where the tickets change?", ["Yes", "No"],
                                   horizontal=True, key="t_split") == "Yes"
            area = st.checkbox("One of the tickets is a Season, zonal or other area ticket (e.g. ranger, rover)", key="t_area")
        elif situation == "child":
            child_age = st.radio("Age", ["under5", "5to15"], horizontal=True, key="t_age",
                                 format_func={"under5": "Under 5, with a ticket holder", "5to15": "5 to 15"}.get)
        scheme = st.radio("Penalty Fare scheme at this station?", ["unknown", "yes", "no"], horizontal=True, key="t_pf",
                          format_func={"unknown": "Don’t know", "yes": "Yes, notices displayed", "no": "No"}.get)
        st.divider()
        render_findings(advisors.ticket_advice(situation, reasons, scheme, split_calls, area, child_age))
        sims = caselog.similar_cases(TICKET_SITUATIONS[situation] + " " + " ".join(NO_TICKET_REASONS[r] for r in reasons))
        if sims:
            st.markdown("**Similar past cases**")
            for _, c in sims:
                st.markdown(f"- {'*Example* · ' if c.get('example') else ''}{c['situation']} → **{c['decision']}** "
                            f"(Conditions {c['conditions']})")
    with tab2:
        st.markdown("Search resolved cases, or save a new one so colleagues can reuse the decision.")
        q = st.text_input("Describe the case", placeholder="e.g. machine broken, no ticket office, boarded without ticket",
                          key="t_q")
        if q:
            sims = caselog.similar_cases(q, k=5)
            if not sims:
                st.info("No similar cases yet.")
            for score, c in sims:
                with st.container(border=True):
                    st.markdown(("🏷️ *Example (fictional training case)* · " if c.get("example") else "")
                                + f"**{c['situation']}**")
                    st.write(f"Decision: {c['decision']}")
                    st.caption(f"Conditions {c['conditions']} · match score {score:.1f}")
        with st.form("save_case", clear_on_submit=True):
            st.markdown("**Save a resolved case**  \nNo passenger names or other personal data.")
            s = st.text_area("Situation")
            d = st.text_area("Decision taken")
            cond = st.text_input("Conditions applied", placeholder="e.g. 6.1.1, 9.4")
            if st.form_submit_button("Save case") and s and d:
                caselog.add_case(s, d, cond, author=user.username)
                st.success("Case saved on this server.")
        n_real = len(caselog.load_cases(include_examples=False))
        st.caption(f"{n_real} saved case(s) + {len(caselog.load_cases()) - n_real} fictional examples · "
                   f"stored in {caselog.LOG.name} on this server")
    with tab3:
        render_chat(u, "Ask about ticket validity, Penalty Fares, split tickets…")


def page_assist(u):
    tab1, tab2 = st.tabs(["🧭 Request checker", "💬 Ask the policy"])
    with tab1:
        st.markdown("Check an assistance request against the Accessible Travel Policy before arranging it.")
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
        st.caption("Not covered: matching the request to available staff. That needs rostering data we do not have; "
                   "see the README.")
    with tab2:
        render_chat(u, "Ask about Passenger Assist, ramps, mobility aids, alternative transport…")


def page_claims(u):
    tab1, tab2, tab3 = st.tabs(["🧭 Claim checker", "💬 Ask the Conditions", "🔒 Data & privacy"])
    with tab1:
        st.markdown("Check a refund or compensation claim against Parts E and F of the Conditions of Travel.")
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
    with tab2:
        render_chat(u, "Ask about refunds, compensation, deadlines, payment methods…")
    with tab3:
        host = urlparse(config.OLLAMA_URL).hostname or ""
        local = host in ("localhost", "127.0.0.1", "::1")
        st.markdown("#### Where the data is processed")
        rows = [
            ("Language model", f"Ollama at `{config.OLLAMA_URL}`",
             "✅ on this machine" if local else f"⚠️ on server `{host}` — check it is inside Thameslink’s network"),
            ("Documents and search index", f"`{config.INDEX_DIR}`", "✅ local files"),
            ("Claim checker inputs", "kept in the browser session only", "✅ not stored"),
            ("Accounts, audit log, feedback, saved cases", f"`{config.STATE_DIR}`", "✅ local files"),
            ("Cloud AI services", "none used", "✅ no data leaves the server"),
            ("Streamlit usage statistics", "switched off in `.streamlit/config.toml`", "✅"),
        ]
        st.table(pd.DataFrame(rows, columns=["Component", "Where", "Status"]))
        st.caption("The app needs no internet connection once the models are downloaded. "
                   "Do not type passenger names or contact details into the chat.")


def page_fleet(u):
    fleet = advisors.load_fleet()
    labels = [advisors.fleet_label(r) for r in fleet]

    def mark(v: str) -> str:
        return "✅ Yes" if v == "Yes" else "❌ No" if v == "No" else f"❔ {v}"

    tab1, tab2, tab3, tab4 = st.tabs(["🚆 Train profile", "⚖️ Compare trains", "🔎 Find a feature", "💬 Ask about trains"])
    with tab1:
        pick = st.selectbox("Train class", labels, index=labels.index("Thameslink Class 700"), key="f_pick")
        row = fleet[labels.index(pick)]
        st.markdown(f"**{pick}** · Routes: {row['routes']}")
        cols = st.columns(2)
        for i, (key, name) in enumerate(FLEET_FIELDS):
            cols[i % 2].markdown(f"{mark(row[key])[:2]} **{name}** — {mark(row[key])[2:].strip()}")
        st.caption("Source: Fleet Accessibility Information (GTR, April 2026), p. 1")
        st.markdown("**Related policy**")
        render_findings([
            Finding("info", "Wheelchair spaces", "In standard class only, marked on the outside of the carriage; not reservable.",
                    [R["A.1-spaces"], R["A.1-noreserve"]]),
            Finding("info", "Mobility aid limits", "Width 700 mm, length 1,200 mm, weight 300 kg including the user, "
                    "turning radius 1,500 mm.", [R["A.5-dims"]]),
            Finding("info", "Boarding", "Staff provide manual ramps; in the Thameslink Core the accessible carriage stops at "
                    "a level boarding area.", [R["A.1-ramps"], R["A.1-core"]]),
        ])
    with tab2:
        chosen = st.multiselect("Trains to compare", labels, default=["Thameslink Class 700", "Great Northern Class 717"],
                                key="f_cmp")
        if chosen:
            rows = [fleet[labels.index(c)] for c in chosen]
            table = pd.DataFrame({c: [mark(r[k]) for k, _ in FLEET_FIELDS] for c, r in zip(chosen, rows)},
                                 index=[n for _, n in FLEET_FIELDS])
            differs = [n for (k, n) in FLEET_FIELDS if len({r[k] for r in rows}) > 1]
            st.dataframe(table, use_container_width=True)
            if len(chosen) > 1:
                st.markdown("**Differences:** " + (", ".join(differs) if differs else "none"))
    with tab3:
        feat = st.selectbox("Feature", [k for k, _ in FLEET_FIELDS], format_func=dict(FLEET_FIELDS).get, key="f_feat")
        c1, c2, c3 = st.columns(3)
        groups = {"Yes": [], "No": [], "Other": []}
        for r in fleet:
            groups[r[feat] if r[feat] in ("Yes", "No") else "Other"].append(
                advisors.fleet_label(r) + ("" if r[feat] in ("Yes", "No") else f" ({r[feat]})"))
        c1.markdown("**✅ Has it**  \n" + ("  \n".join(groups["Yes"]) or "—"))
        c2.markdown("**❌ Does not**  \n" + ("  \n".join(groups["No"]) or "—"))
        c3.markdown("**❔ Conditional**  \n" + ("  \n".join(groups["Other"]) or "—"))
        st.caption("Not in the source table: induction loops, number of wheelchair spaces per train, carriage positions.")
    with tab4:
        render_chat(u, "Ask about a train class or on-board accessibility…")


def page_admin():
    st.markdown("## ⚙️ Administration")
    st.caption("Accounts, activity and quality review. Visible to administrators only.")
    tab1, tab2, tab3, tab4, tab5 = st.tabs(["👥 Users", "📜 Activity", "👎 Feedback", "🗂️ Saved cases", "🛠️ System"])
    with tab1:
        users = auth.load_users()
        st.dataframe(pd.DataFrame([{
            "Username": u.username, "Name": u.name, "Role": auth.ROLES[u.role]["name"],
            "Areas": ", ".join(BY_ID[i].short for i in u.use_cases),
            "Status": "active" if u.active else "disabled", "Must change password": u.must_change,
            "Last sign-in": u.last_login or "—"} for u in users.values()]), use_container_width=True, hide_index=True)
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
                    if b2.button("Disable" if t.active else "Enable", key="adm_toggle",
                                 disabled=target == user.username):
                        auth.update_user(target, by=user.username, active=not t.active)
                        st.rerun()
                    if b3.button("Reset password", key="adm_reset"):
                        st.success(f"New temporary password for **{target}** (shown once):")
                        st.code(auth.reset_password(target, by=user.username))
                except auth.AuthError as e:
                    st.error(str(e))
    with tab2:
        log = auth.read_audit(300)
        st.dataframe(pd.DataFrame(log) if log else pd.DataFrame(columns=["time", "event", "user", "by", "detail"]),
                     use_container_width=True, hide_index=True)
        st.caption(f"Latest 300 events from {auth.AUDIT_FILE.name}. Passwords are never logged.")
    with tab3:
        path = config.STATE_DIR / "feedback.jsonl"
        fb = [json.loads(l) for l in open(path, encoding="utf-8")] if path.exists() else []
        down = [f for f in fb if f.get("rating") == "down"]
        st.metric("Answers rated 👎", f"{len(down)} of {len(fb)}")
        if down:
            st.dataframe(pd.DataFrame(down)[["time", "user", "use_case", "question", "answer"]],
                         use_container_width=True, hide_index=True)
        st.caption("Review 👎 answers regularly: fix the documents, the rules or the test questions.")
    with tab4:
        cases = caselog.load_cases(include_examples=False)
        if cases:
            st.dataframe(pd.DataFrame(cases), use_container_width=True, hide_index=True)
        else:
            st.info("No cases saved yet.")
    with tab5:
        st.markdown(f"- Advisor rules verified against the documents: **{len(advisors.ALL_CITES) - len(problems)} of "
                    f"{len(advisors.ALL_CITES)}**")
        for p in problems:
            st.error(p)
        st.markdown(f"- Language model: **{'connected' if llm_ready else 'not available'}** (`{config.CHAT_MODEL}` at "
                    f"`{config.OLLAMA_URL}`)")
        st.markdown(f"- Search index: **{len(retriever.chunks)} passages**"
                    f"{', with semantic embeddings' if retriever.has_embeddings else ', keyword search only'}")
        st.markdown(f"- Accounts file: `{auth.USERS_FILE}` · audit log: `{auth.AUDIT_FILE}`")


PAGES = {"policy": page_policy, "tickets": page_tickets, "assist": page_assist, "claims": page_claims, "fleet": page_fleet}
if choice == "admin":
    if not user.is_admin:                                   # enforced on the server, not only hidden in the menu
        st.error("You do not have access to this area.")
        st.stop()
    page_admin()
else:
    if uc is None or uc.id not in user.use_cases:
        st.error("You do not have access to this area.")
        st.stop()
    header(uc)
    PAGES[uc.id](uc)
st.caption("Prototype · answers and checks come only from the published documents. "
           "Always check the cited source before acting.")

"""Thameslink Frontline Assistant — five use cases in one app.

Run:  streamlit run app.py
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from urllib.parse import urlparse

import pandas as pd
import streamlit as st
import yaml

from rag import advisors, caselog, config, ollama_client
from rag.advisors import (CLAIM_EVENTS, FLEET_FIELDS, NO_TICKET_REASONS, R, TICKET_SITUATIONS, TICKET_TYPES,
                          Finding)
from rag.answer import answer
from rag.retrieve import Retriever
from rag.usecases import USE_CASES

st.set_page_config(page_title="Frontline Assistant", page_icon="🚆", layout="wide")

ICON = {"ok": "✅", "warn": "⚠️", "stop": "⛔", "info": "ℹ️"}


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
    with open(config.DATA_DIR / "feedback.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


try:
    retriever = load_retriever()
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()

models = ollama_status()
llm_ready = ollama_client.has_model(config.CHAT_MODEL, models or [])

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.markdown("### 🚆 Frontline Assistant")
    choice = st.radio("Assistant", [u.id for u in USE_CASES],
                      format_func=lambda i: next(f"{u.icon} {u.number}. {u.short}" for u in USE_CASES if u.id == i),
                      label_visibility="collapsed")
    uc = next(u for u in USE_CASES if u.id == choice)

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
                caselog.add_case(s, d, cond)
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
            ("Feedback and saved cases", f"`{config.DATA_DIR}`", "✅ local files"),
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


PAGES = {"policy": page_policy, "tickets": page_tickets, "assist": page_assist, "claims": page_claims, "fleet": page_fleet}
header(uc)
PAGES[uc.id](uc)
st.caption("Prototype · answers and checks come only from the published documents. "
           "Always check the cited source before acting.")

"""The five use cases: who they serve, which documents they may search, and how the assistant behaves.

Each use case gets its own search scope, so (for example) the ticket-inspection advisor
only ever quotes Conditions of Travel Parts C–E.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable


def _part(chunk: dict) -> str:
    """'C' for a passage under 'Part C: …' of the Conditions of Travel, else ''."""
    for p in chunk.get("section_path", []):
        m = re.match(r"Part ([A-H])", p)
        if m:
            return m.group(1)
    return ""


def _section(chunk: dict, *prefixes: str) -> bool:
    return any(p.startswith(prefixes) for p in chunk.get("section_path", []))


@dataclass
class UseCase:
    id: str
    number: int
    title: str
    short: str
    icon: str
    users: str
    problem: str
    story: str
    sources: str
    scope: Callable[[dict], bool]
    role: str
    examples: list[str] = field(default_factory=list)
    extra_story: str = ""


USE_CASES: list[UseCase] = [
    UseCase(
        id="policy", number=1, short="Policy & manuals", icon="📘",
        title="General Policy & Manuals Query Assistant",
        users="Frontline employees and customer service agents",
        problem="Staff spend a lot of time searching long policy documents and travel conditions "
                "to answer complex passenger questions on the spot.",
        story="As an employee, I want to ask manuals and policies in plain language, so that I stop searching through PDFs.",
        sources="All four documents",
        scope=lambda c: True,
        role=("a knowledge assistant for Thameslink / Govia Thameslink Railway (GTR) frontline staff: "
              "station staff, conductors and customer service colleagues"),
        examples=["What is a passenger entitled to if their train is cancelled?",
                  "Can a passenger take a bicycle on the train?",
                  "How many items of luggage can a passenger bring?",
                  "How do we help a wheelchair user board at St Pancras?"],
    ),
    UseCase(
        id="tickets", number=2, short="Ticket inspection", icon="🎫",
        title="Frontline Ticket Inspection & Penalty Fares Advisor",
        users="Service desk agents and revenue protection officers",
        problem="Crew and station staff face complex cases about ticket validity, broken journeys and "
                "whether to charge a Penalty Fare — for example when ticket machines are broken or "
                "connections are missed because of delays.",
        story="As a service desk agent, I want to get solutions suggested from similar past tickets, so that I resolve tickets faster.",
        sources="National Rail Conditions of Travel, Parts C, D and E",
        scope=lambda c: c["doc_id"] == "nrcot" and _part(c) in ("C", "D", "E"),
        role=("an advisor for Thameslink revenue protection officers and service desk agents. You explain what the "
              "National Rail Conditions of Travel say about ticket validity, travelling without a valid ticket, "
              "Penalty Fares, combinations of tickets and break of journey. Name the Condition numbers. "
              "Present what the Conditions allow; the decision stays with the member of staff"),
        examples=["A passenger says the ticket machine was broken and the office closed. What now?",
                  "Can someone with an Off-Peak ticket travel on a peak train?",
                  "Is a split ticket valid if the train does not stop at the split station?",
                  "When can we charge a Penalty Fare?"],
    ),
    UseCase(
        id="assist", number=3, short="Passenger Assist", icon="♿",
        title="Passenger Assist & Accessibility Operations Coordinator",
        users="Resource planners and assisted travel coordinators",
        problem="Station and control centre teams coordinate help for disabled and older passengers: "
                "boarding ramps, mobility aids, wheelchair spaces, mobile assistance teams and accessible "
                "transport during disruption.",
        story=("As an assisted travel coordinator, I want to check an assistance request against our policy, "
               "so that the right help is arranged first time."),
        sources="Accessible Travel Policy and Making Rail Accessible",
        scope=lambda c: c["doc_id"] in ("atp", "mra"),
        role=("an assistant for GTR assisted travel coordinators and station staff arranging help for disabled and "
              "older passengers. Answer from the Accessible Travel Policy and Making Rail Accessible, naming the "
              "section (e.g. A.1, A.5)"),
        examples=["How much notice is needed to book Passenger Assist?",
                  "What are the size limits for wheelchairs and mobility scooters?",
                  "What do we do for a passenger at an unstaffed station?",
                  "What happens if a rail replacement bus is not accessible?"],
    ),
    UseCase(
        id="claims", number=4, short="Refunds & compensation", icon="💷",
        title="Disruption & Delay Repay Claims Specialist",
        users="Customer relations agents (and the IT / data protection lead)",
        problem="After disruption, customer relations teams handle many claims and must decide refund "
                "eligibility and compensation correctly for each ticket type.",
        story=("As a customer relations agent, I want to check a claim against the refund and compensation "
               "Conditions, so that every passenger gets the right outcome."),
        extra_story="As the IT / data protection lead, I want to keep everything on our own servers, so that no data leaves Thameslink.",
        sources="National Rail Conditions of Travel, Parts E and F",
        scope=lambda c: c["doc_id"] == "nrcot" and _part(c) in ("E", "F"),
        role=("an assistant for Thameslink customer relations agents handling refund and delay compensation claims. "
              "Answer from the National Rail Conditions of Travel Parts E and F, naming Condition numbers. "
              "The Conditions set minimum compensation for delays of 60 minutes or more; amounts for shorter delays "
              "are set by each Train Company's Passenger's Charter, which is not among your sources"),
        examples=["What compensation is due for a 75-minute delay on a single ticket?",
                  "How long does a passenger have to claim delay compensation?",
                  "Can a passenger get a refund and compensation for the same journey?",
                  "How is compensation paid?"],
    ),
    UseCase(
        id="fleet", number=5, short="Fleet accessibility", icon="🚆",
        title="Fleet Accessibility & Rolling Stock Advisor",
        users="Station managers and dispatch operators",
        problem="Staff need the on-board facilities and accessibility features of each train class "
                "(e.g. Thameslink Class 700 vs Great Northern Class 717) when helping passengers.",
        story=("As a station manager, I want to query train-specific accessibility profiles instantly, so that I can "
               "safely direct passengers to the correct carriages and make sure boarding equipment matches the train."),
        sources="Fleet Accessibility Information, plus the related policy sections (A.1, A.5, On our trains)",
        scope=lambda c: (c["doc_id"] == "fleet"
                         or (c["doc_id"] == "atp" and _section(c, "A.5", "A.1"))
                         or (c["doc_id"] == "mra" and _section(c, "What to expect"))),
        role=("an assistant for GTR station managers and dispatch staff answering questions about the accessibility "
              "features of each train class. The fleet table lists, per class: wheelchair space, toilets, audio and "
              "visual information, priority seats, signage, scooter acceptance, boarding ramp and on-train staff"),
        examples=["Does the Class 717 have an accessible toilet?",
                  "Which trains have on-board staff?",
                  "Can mobility scooters travel on Thameslink Class 700 trains?",
                  "Where are wheelchair spaces on our trains?"],
    ),
]

BY_ID = {u.id: u for u in USE_CASES}

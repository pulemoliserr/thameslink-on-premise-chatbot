"""Rule-based advisors for use cases 2–5.

Each advisor turns a structured situation (what the member of staff ticks in the form)
into a list of Findings. Every Finding cites the document passage it comes from by
quoting an exact phrase (`Cite.quote`). At start-up, and in the tests, every quote is
looked up in the indexed documents (`check_citations`), so if a document is updated
and a rule no longer matches its source, this is detected instead of silently giving
outdated advice.

These advisors only restate what the documents say. The decision stays with the
member of staff.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field

from . import config

OK, WARN, STOP, INFO = "ok", "warn", "stop", "info"


@dataclass
class Cite:
    doc_id: str
    quote: str          # exact text from the document (whitespace-insensitive)
    ref: str            # human label, e.g. "Condition 9.4" or "Accessible Travel Policy A.5"


@dataclass
class Finding:
    level: str          # ok | warn | stop | info
    title: str
    text: str
    cites: list[Cite] = field(default_factory=list)


def _norm(s: str) -> str:
    return " ".join(s.replace("’", "'").split()).lower()


def resolve(cite: Cite, chunks: list[dict]) -> dict | None:
    """Find the passage that contains the quoted phrase."""
    q = _norm(cite.quote)
    for c in chunks:
        if c["doc_id"] == cite.doc_id and q in _norm(c["text"]):
            return c
    return None


def check_citations(chunks: list[dict]) -> list[str]:
    """Return a problem description for every rule quote that is not found in the documents."""
    problems = []
    for cite in ALL_CITES:
        if resolve(cite, chunks) is None:
            problems.append(f"{cite.ref}: quote not found in documents: “{cite.quote[:70]}…”")
    return problems


# ---------------------------------------------------------------- citations used by the rules
def C(n, quote):
    return Cite("nrcot", quote, f"Condition {n}")


def A(sec, quote):
    return Cite("atp", quote, f"Accessible Travel Policy {sec}")


R = {
    # Condition 6: boarding without a valid ticket
    "6.1": C("6.1", "You must hold a valid Ticket or authority to travel for the journey you intend to make before you board a train"),
    "6.1.1": C("6.1.1", "there was no Ticket office open or a self-service Ticket machine was not in working order, or would not accept your only available method of payment"),
    "6.1.2": C("6.1.2", "There was a notice at the station permitting you to start your journey without a valid Ticket"),
    "6.1.3": C("6.1.3", "An authorised person gave you permission to travel without a valid Ticket"),
    "6.1.4": C("6.1.4", "You have a disability and Ticket purchasing arrangements at the station where you started your journey were not accessible to you"),
    "6.1-buy": C("6.1", "you must buy an appropriate Ticket to complete your journey as soon as you are reasonably able to"),
    "6.1-price": C("6.1", "the price of the Ticket you purchase will be the same as if you had bought a Ticket at the station from which you first departed, including any Railcard discount"),
    "6.2": C("6.2", "the Train Company is entitled to treat any such Ticket as an invalid Ticket and take action in accordance with Condition 9"),
    "6.3": C("6.3", "It is your responsibility to ensure that the electronic device can be accessed when required"),
    "6.4": C("6.4", "Children under five years of age may travel free of charge without a Ticket providing that they are travelling with a passenger holding a valid Ticket"),
    "6.5": C("6.5", "children aged between 5 years and 15 years must have a valid Ticket for travel but are entitled to a “child” discount on most Tickets"),
    # Railcards
    "8.4": C("8.4", "you will be treated as having joined a train without a valid Ticket and Conditions 9.1 – 9.5 will apply"),
    "8.5": C("8.5", "because you had forgotten to carry it on that particular journey or were unable to display it on an electronic device, the Train Company concerned will refund any fare or Penalty Fare paid"),
    "8.6": C("8.6", "A maximum of one such claim will be considered in any 12-month period"),
    # Condition 9: travelling without a valid ticket
    "9.1": C("9.1", "you should explain this to the member of Train Company staff who has asked to inspect your Ticket"),
    "9.2.1": C("9.2.1", "To charge the full undiscounted single fare to a station directly served by the train that you are on"),
    "9.2.2": C("9.2.2", "To charge you a Penalty Fare when indicated by notices at stations served by trains with a Penalty Fare scheme in place"),
    "9.2.3": C("9.2.3", "To report you for prosecution for a criminal offence under the Railways Byelaws"),
    "9.2-name": C("9.2", "the Train Company may require you to provide your name and address so that the matter can be followed up"),
    "9.4": C("9.4", "you will be able to travel on the next train service provided by the Train Company(s) with which your ticket was valid without penalty"),
    "9.5.1": C("9.5.1", "are using a Ticket that is correctly dated but restricted for use at a range of times other than the service on which you are travelling"),
    "9.5-diff": C("9.5", "you will be charged the difference between the fare that you have paid and the lowest price Ticket that is valid for the train you are using"),
    "9.5-advance": C("9.5", "If you travel on a train service or services outside the booked itinerary for which your Ticket was issued you will be treated as if no Ticket were held unless the circumstances of clause 9.4 apply"),
    "9.5.2": C("9.5.2", "break your journey when you are not permitted to do so"),
    # Condition 10: Penalty Fares
    "10.1": C("10.1", "Train Companies are required to ensure that warning notices are clearly displayed at stations where Penalty Fares apply"),
    "10.1.1": C("10.1.1", "You travel on a train service without a valid Ticket"),
    "10.1.2": C("10.1.2", "You travel in first class accommodation with a standard class Ticket"),
    "10.1.3": C("10.1.3", "You travel on a train service at a time when your service-specific Ticket is not valid (unless Condition 9.4 applies)"),
    "10.1.5": C("10.1.5", "You do not have the necessary supporting document(s), where required (for example a valid Railcard or photocard)"),
    "10.2": C("10.2", "you must, where indicated, obtain a “promise to pay” from a self-service Ticket machine"),
    # Condition 14: combinations of tickets
    "14.1": C("14.1", "Some Tickets specifically exclude their use in conjunction with other Tickets"),
    "14.2": C("14.2", "you may use a combination of two or more Tickets to make a journey provided that the train services you use Call at the station(s) where you change from one Ticket to another"),
    "14.3": C("14.3", "then the train does not need to Call at that station for your combination to be valid"),
    "14.4": C("14.4", "you must comply with the specific terms and conditions of each of the Tickets you are using"),
    # Conditions 15–16
    "15.1": C("15.1", "Unless Train Company staff, or notices on the train give you specific permission, you cannot travel in first class accommodation"),
    "15.2": C("15.2", "A standard class Ticket can in most cases be upgraded to first class"),
    "16-adv": C("16", "Please note that Advance Tickets do not permit a break of journey"),
    "16.4": C("16.4", "you will be liable to pay an excess fare"),
    # Part E/F: disruption, refunds and compensation
    "28.3": C("28.3", "you will normally in such circumstances be permitted to travel up to either two trains prior or two trains after the affected service"),
    "28.4": C("28.4", "Where your train is likely to be delayed for more than 60 minutes, you may use your Ticket to make your journey at a later date"),
    "28.6": C("28.6", "your Train Company may, in certain circumstances, provide you with meals and refreshments"),
    "29.1": C("29.1", "You must do this no more than 28 days after the expiry of the Ticket(s) unless the terms and conditions of your Ticket state otherwise"),
    "29.2": C("29.2", "An administration charge may also be made, which will not exceed £5 per Ticket (£10 per Ticket in the case of Season Tickets"),
    "30.1": C("30.1", "where you will be given a full refund with no administration fee charged, if you decide not to travel because the train you intended to use is"),
    "30.1-return": C("30.1", "You are also entitled to a refund under these Conditions if you begin your journey but are unable to complete it due to a delay to, or cancellation of your service and you return to your point of origin"),
    "30.1-season": C("30.1", "Season Tickets have their own compensation process when disruption or cancellation prevents travel"),
    "30.2": C("30.2", "You must write to the Train Company to notify them of your claim within 28 days of the date that you intended to travel"),
    "30.3": C("30.3", "any refund due will be paid within 14 days of your claim being agreed by the Train Company"),
    "31.1": C("31.1", "The minimum refund to which you will be entitled will be the difference between the price of the first class Ticket purchased and the cheapest valid standard class walk-up fare"),
    "31.1-28": C("31.1", "You must contact the Train Company you travelled with to notify them of your claim within 28 days of the date that you travelled"),
    "32.1": C("32.1", "You are entitled to compensation if the delay was 60 minutes or longer, regardless of fault"),
    "32.1-charter": C("32", "The Train Company Passenger’s Charters and websites set out the details around compensation arrangements"),
    "32.2": C("32.2", "you can only recover up to the price of your Ticket or Tickets"),
    "33.1": C("33.1", "you must submit your claim to the Train Company within 28 days of completing the journey (unless that deadline is extended by the Train Company)"),
    "33.2": C("33.2", "any compensation due will be paid within 14 days of your claim being agreed by the Train Company"),
    "33.3": C("33.3", "if you arrive 60 or more minute late at your destination station, you will, as a minimum, be entitled to compensation in accordance with the table below"),
    "33.3-season": C("33.3", "Season Ticket The discount or compensation arrangements in the relevant Train Company’s Passenger’s Charter apply"),
    "33.3-fault": C("33.3", "You do not have to prove that the delay was the Train Company’s fault"),
    "33.4": C("33.4", "A Train Company may not be obliged to pay compensation under this Condition if the delay is less than 60 minutes and its cause was entirely outside the rail industry’s control"),
    "33.5": C("33.5", "you cannot then also claim compensation for delay under this clause through a Train Company’s Passenger’s Charter"),
    "34.1": C("34.1", "Your options, which will include at least one “money option”, will vary by Train Company"),
    "34.1-fault": C("34.1", "Where the delay was caused by a fault of the Train Company, you are entitled to payment by the same means as your Ticket was paid for"),
    # Accessible Travel Policy
    "A.1-notice": A("A.1", "We recommend that assistance is booked two hours before travel"),
    "A.1-optional": A("A.1", "Booking assistance before travel is entirely optional"),
    "A.1-20min": A("A.1", "we recommend arriving 20 minutes prior to the train’s scheduled departure time"),
    "A.1-mat": A("A.1", "We ask unbooked passengers who require boarding assistance to contact our Control support team 20 minutes before their train is due to depart"),
    "A.1-unstaffed": A("A.1", "when travelling from any of our unstaffed stations, customers can contact our off-site Control support team via the Help Point"),
    "A.1-phone": A("A.1", "Thameslink/Great Northern Telephone: 0800 058 2844"),
    "A.1-other-op": A("A.1", "if the journey involves changing onto a service operated by another operator, we can book assistance for the whole journey"),
    "A.1-core": A("A.1", "At stations within our Thameslink Core (London St Pancras through to London Bridge), the accessible carriages stopping point will align with raised level boarding areas"),
    "A.1-ramps": A("A.1", "Our station and on-board staff will provide manual boarding ramps for customers who require assistance boarding/leaving trains whether travelling booked or unbooked"),
    "A.1-5min": A("A.1", "we will do all we can to ensure that assistance is provided within five minutes of the train’s arrival at its terminus station"),
    "A.1-luggage": A("A.1", "it can only be provided with up to two items of luggage which should not exceed 30 x 70 x 90cm in size and a single item of hand luggage"),
    "A.1-spaces": A("A.1", "All our trains have wheelchair spaces within the standard class seating area only"),
    "A.1-noreserve": A("A.1", "Wheelchair spaces aren’t reservable"),
    "A.1-dogs": A("A.1", "Assistance dogs are welcome on all our services"),
    "A.4-taxi": A("A.4", "we may provide a free taxi (accessible, if required) between the inaccessible station and the nearest or most convenient accessible station"),
    "A.4-30min": A("A.4", "If any delay is known for alternative transport and that it will be greater than 30 minutes beyond the original expected times, they will inform the customer immediately"),
    "A.5-dims": A("A.5", "Width: 700mm Length: 1,200mm Weight: 300kg (including the user) Have a turning radius of no more than 1,500mm"),
    "A.5-noalt": A("A.5", "Where the above dimensions are exceeded, we are also unable to provide journey support via an alternative vehicle"),
    "A.5-controls": A("A.5", "Our staff will be unable to take the controls for the customer"),
    "A.5-3mph": A("A.5", "we ask that scooters are not driven above three miles per hour whilst at any station"),
    "A.5-bus": A("A.5", "Many rail replacement buses are unable to accept mobility scooters"),
    "A.5-otherop": A("A.5", "Scooter policies do vary between train operators"),
    "A.6-taxi": A("A.6", "If an inaccessible bus is provided for unplanned disruption, we will ensure that a suitable alternative taxi is arranged"),
}
ALL_CITES = list(R.values())


# ---------------------------------------------------------------- Use case 2: ticket inspection
TICKET_SITUATIONS = {
    "no_ticket": "No valid ticket",
    "missed_connection": "Advance / specific-train ticket: booked train missed because an earlier connection was delayed or cancelled",
    "advance_wrong_train": "Advance / specific-train ticket used on a different train (no delayed connection)",
    "offpeak_wrong_time": "Off-Peak or other time-restricted ticket used at a restricted time",
    "break_journey": "Passenger broke their journey where the ticket does not allow it",
    "first_class": "Standard class ticket, sitting in first class",
    "railcard_missing": "Railcard-discounted ticket, but no Railcard shown",
    "phone_ticket": "Ticket is on a phone that cannot display it",
    "split_ticket": "Two or more tickets used for one journey (split ticketing)",
    "child": "Child travelling without a ticket",
}
NO_TICKET_REASONS = {
    "machine": "No ticket office open, and the ticket machine was broken or did not accept the passenger’s only payment method",
    "notice": "A notice at the station allowed travel without a ticket",
    "permission": "Staff (or an announcement / written notice) gave permission to board without a ticket",
    "disability": "Passenger is disabled and the ticket buying facilities were not accessible to them",
}


def ticket_advice(situation: str, reasons: list[str] | None = None, penalty_scheme: str = "unknown",
                  split_calls: bool | None = None, area_ticket: bool = False, child_age: str = "") -> list[Finding]:
    reasons = reasons or []
    f: list[Finding] = []

    def without_ticket(extra_cites=()):
        f.append(Finding(STOP, "Treated as travelling without a valid ticket (Condition 9)",
                         "Ask the passenger to explain first: there may be a reason under Condition 6. If none applies, "
                         "the Train Company may: charge the full undiscounted single fare to a station served by this "
                         "train; charge a Penalty Fare where a scheme is in place and notices are displayed; or report "
                         "for prosecution. You may ask for the passenger’s name and address.",
                         [R["9.1"], R["9.2.1"], R["9.2.2"], R["9.2.3"], R["9.2-name"], *extra_cites]))
        penalty_note()

    def penalty_note():
        if penalty_scheme == "yes":
            f.append(Finding(INFO, "Penalty Fare scheme in place",
                             "Penalty Fares can apply where warning notices are clearly displayed at the station. "
                             "Where notices say so, passengers should have obtained a “promise to pay” from a ticket machine.",
                             [R["10.1"], R["10.2"]]))
        elif penalty_scheme == "no":
            f.append(Finding(INFO, "No Penalty Fare scheme",
                             "A Penalty Fare is only an option where a scheme is in place and indicated by notices at stations; "
                             "the other Condition 9 options still apply.", [R["9.2.2"]]))

    if situation == "no_ticket":
        valid = [r for r in reasons if r in NO_TICKET_REASONS]
        if valid:
            cites = {"machine": R["6.1.1"], "notice": R["6.1.2"], "permission": R["6.1.3"], "disability": R["6.1.4"]}
            f.append(Finding(OK, "Boarding without a ticket is allowed in this case (Condition 6.1)",
                             "Reason given: " + "; ".join(NO_TICKET_REASONS[r] for r in valid) + ". "
                             "The passenger must buy a ticket as soon as reasonably able — on board from train staff, or at "
                             "the destination before leaving the station — at the same price as from their departure "
                             "station, including any Railcard discount.",
                             [cites[r] for r in valid] + [R["6.1-buy"], R["6.1-price"]]))
            f.append(Finding(WARN, "If they do not buy a ticket",
                             "The Train Company may then treat them as having boarded without a valid ticket (Condition 9).",
                             [R["6.1-buy"]]))
        else:
            f.append(Finding(INFO, "None of the Condition 6.1 exceptions applies",
                             "A valid ticket is required before boarding. Buying one after boarding (online on the train or at "
                             "the destination) can be treated as invalid.", [R["6.1"], R["6.2"]]))
            without_ticket([R["10.1.1"]])
    elif situation == "missed_connection":
        f.append(Finding(OK, "Travel on the next service without penalty (Condition 9.4)",
                         "Because an earlier connecting train was delayed or cancelled, the passenger may take the next "
                         "service of the Train Company their ticket is valid with. This is also excluded from Penalty Fares.",
                         [R["9.4"], R["10.1.3"]]))
    elif situation == "advance_wrong_train":
        f.append(Finding(WARN, "Treated as if no ticket were held",
                         "Travelling outside the booked itinerary of an Advance ticket counts as no ticket (unless 9.4 applies). "
                         "A Penalty Fare may apply where a scheme is in place.",
                         [R["9.5-advance"], R["10.1.3"]]))
        without_ticket()
    elif situation == "offpeak_wrong_time":
        f.append(Finding(WARN, "Charge the fare difference (Condition 9.5.1)",
                         "For a correctly dated but time-restricted ticket (e.g. Off-Peak; not Advance), charge the difference "
                         "between the fare paid and the lowest-price ticket valid for this train.",
                         [R["9.5.1"], R["9.5-diff"]]))
    elif situation == "break_journey":
        f.append(Finding(WARN, "Charge the fare difference / excess fare",
                         "Breaking a journey where not permitted is charged as the difference to the lowest-price valid ticket "
                         "(9.5.2), or an excess fare (16.4). Advance tickets never allow a break of journey.",
                         [R["9.5.2"], R["9.5-diff"], R["16.4"], R["16-adv"]]))
    elif situation == "first_class":
        f.append(Finding(WARN, "Not allowed without permission",
                         "Standard ticket holders cannot use first class unless staff or notices on the train allow it. "
                         "A Penalty Fare may apply where a scheme is in place. In most cases the ticket can be upgraded.",
                         [R["15.1"], R["10.1.2"], R["15.2"]]))
        penalty_note()
    elif situation == "railcard_missing":
        f.append(Finding(WARN, "Treated as without a valid ticket (Condition 8.4)",
                         "Conditions 9.1–9.5 apply, and a Penalty Fare is possible where a scheme is in place.",
                         [R["8.4"], R["10.1.5"]]))
        f.append(Finding(INFO, "Refund possible later (Conditions 8.5–8.6)",
                         "If the passenger simply forgot a valid Railcard (or could not show it on a device), the Train Company "
                         "refunds the extra fare or Penalty Fare when they contact customer service with the Railcard details. "
                         "At most one such claim in any 12 months.", [R["8.5"], R["8.6"]]))
        penalty_note()
    elif situation == "phone_ticket":
        f.append(Finding(WARN, "Passenger’s responsibility (Condition 6.3)",
                         "The passenger must make sure the device can show the ticket when asked. If they cannot, the "
                         "Train Company may act under Condition 9.", [R["6.3"]]))
    elif situation == "split_ticket":
        if split_calls or area_ticket:
            why = ("one ticket is a Season / zonal / area ticket, so the train does not need to call where they meet"
                   if area_ticket and not split_calls else "the train calls at the station where the tickets change")
            f.append(Finding(OK, "Valid combination (Condition 14)",
                             f"Valid because {why} — unless one ticket’s conditions exclude combinations, and provided each "
                             "ticket’s own route and time conditions are met.",
                             [R["14.2"] if split_calls else R["14.3"], R["14.1"], R["14.4"]]))
        else:
            f.append(Finding(STOP, "Not a valid combination (Condition 14.2)",
                             "The train must call at the station where the passenger changes from one ticket to the other.",
                             [R["14.2"]]))
            without_ticket()
    elif situation == "child":
        if child_age == "under5":
            f.append(Finding(OK, "Free of charge (Condition 6.4)",
                             "Children under 5 travel free when with a passenger who holds a valid ticket.", [R["6.4"]]))
        else:
            f.append(Finding(WARN, "Ticket needed (Condition 6.5)",
                             "Children aged 5–15 need a valid ticket, with a child discount on most tickets.", [R["6.5"]]))
    return f


# ---------------------------------------------------------------- Use case 3: assistance requests
AID_LIMITS = {"width_mm": 700, "length_mm": 1200, "weight_kg": 300, "turning_mm": 1500}


def assistance_check(aid: str = "none", width_mm: float = 0, length_mm: float = 0, weight_kg: float = 0,
                     turning_mm: float = 0, booked: bool = True, notice_hours: float = 0, station: str = "staffed",
                     core_station: bool = False, other_operator: bool = False, luggage_items: int = 0,
                     disruption: bool = False, assistance_dog: bool = False) -> list[Finding]:
    f: list[Finding] = []
    if aid in ("wheelchair", "powerchair", "scooter"):
        over = []
        if width_mm > AID_LIMITS["width_mm"]:
            over.append(f"width {width_mm:.0f} mm > 700 mm")
        if length_mm > AID_LIMITS["length_mm"]:
            over.append(f"length {length_mm:.0f} mm > 1,200 mm")
        if weight_kg > AID_LIMITS["weight_kg"]:
            over.append(f"weight {weight_kg:.0f} kg > 300 kg (including the user)")
        if turning_mm > AID_LIMITS["turning_mm"]:
            over.append(f"turning radius {turning_mm:.0f} mm > 1,500 mm")
        missing = [n for n, v in (("width", width_mm), ("length", length_mm), ("weight", weight_kg)) if not v]
        if over:
            f.append(Finding(STOP, "Mobility aid exceeds the safety limits",
                             "Cannot be accepted on our trains: " + "; ".join(over) + ". Where the limits are exceeded, "
                             "journey support by an alternative vehicle is not available either.",
                             [R["A.5-dims"], R["A.5-noalt"]]))
        elif missing:
            f.append(Finding(WARN, "Check the mobility aid size",
                             "Limits: width 700 mm, length 1,200 mm, weight 300 kg including the user, turning radius "
                             "1,500 mm. Missing: " + ", ".join(missing) + ".", [R["A.5-dims"]]))
        else:
            f.append(Finding(OK, "Mobility aid is within the limits",
                             "Width ≤ 700 mm, length ≤ 1,200 mm, weight ≤ 300 kg including the user"
                             + (", turning radius ≤ 1,500 mm." if turning_mm else " (turning radius not given; limit 1,500 mm)."),
                             [R["A.5-dims"]]))
        if aid == "scooter":
            f.append(Finding(INFO, "Scooter users",
                             "Staff cannot take the controls. Scooters must not go faster than 3 mph in stations. "
                             "Other operators’ scooter policies differ — check them for connecting journeys.",
                             [R["A.5-controls"], R["A.5-3mph"], R["A.5-otherop"]]))
        f.append(Finding(INFO, "Wheelchair spaces",
                         "Wheelchair spaces are in standard class only and cannot be reserved; staff enforce priority.",
                         [R["A.1-spaces"], R["A.1-noreserve"]]))
    if booked:
        if notice_hours >= 2:
            f.append(Finding(OK, "Booked with the recommended notice",
                             f"{notice_hours:g} hours’ notice meets the recommended two hours.", [R["A.1-notice"]]))
        else:
            f.append(Finding(WARN, "Less than the recommended two hours’ notice",
                             "Booking is optional, so assistance is still provided; arrange it as quickly as possible and "
                             "ask the passenger to arrive 20 minutes before departure.",
                             [R["A.1-notice"], R["A.1-optional"], R["A.1-20min"]]))
    else:
        f.append(Finding(INFO, "Unbooked assistance",
                         "Booking is optional. Recommend arriving 20 minutes before departure. At stations served by the "
                         "Mobile Assistance Team, the passenger contacts Control 20 minutes before departure.",
                         [R["A.1-optional"], R["A.1-20min"], R["A.1-mat"]]))
    if station == "unstaffed":
        f.append(Finding(INFO, "Unstaffed departure station",
                         "The passenger can reach the Control support team via the Help Point or freephone 0808 168 1238 "
                         "(text 07970 511077). Options include sending staff from another station; if the station is "
                         "inaccessible, a free (accessible if needed) taxi to the nearest accessible station.",
                         [R["A.1-unstaffed"], R["A.4-taxi"]]))
    if core_station:
        f.append(Finding(OK, "Level boarding available",
                         "In the Thameslink Core (St Pancras to London Bridge) the accessible carriage stops at a raised "
                         "level boarding area; staff are on hand.", [R["A.1-core"]]))
    else:
        f.append(Finding(INFO, "Boarding ramp", "Staff provide manual boarding ramps, booked or unbooked.", [R["A.1-ramps"]]))
    if other_operator:
        f.append(Finding(INFO, "Journey with another operator",
                         "Passenger Assist can book assistance for the whole journey, including the other operator’s part.",
                         [R["A.1-other-op"]]))
    if luggage_items > 2:
        f.append(Finding(WARN, "Too much luggage for assisted handling",
                         f"{luggage_items} items: luggage assistance covers up to two items (max 30 × 70 × 90 cm) plus one "
                         "item of hand luggage.", [R["A.1-luggage"]]))
    elif luggage_items:
        f.append(Finding(OK, "Luggage assistance possible",
                         "Up to two items (max 30 × 70 × 90 cm) plus one item of hand luggage.", [R["A.1-luggage"]]))
    if disruption:
        f.append(Finding(WARN, "Disruption",
                         "If an inaccessible bus is provided during unplanned disruption, arrange a suitable taxi. Many rail "
                         "replacement buses cannot take mobility scooters. If alternative transport will be more than 30 "
                         "minutes late, tell the passenger immediately.",
                         [R["A.6-taxi"], R["A.5-bus"], R["A.4-30min"]]))
    if assistance_dog:
        f.append(Finding(OK, "Assistance dog", "Assistance dogs are welcome on all services.", [R["A.1-dogs"]]))
    f.append(Finding(INFO, "At the destination",
                     "Aim to give assistance within five minutes of arrival at a terminus. Thameslink / Great Northern "
                     "Assisted Travel: 0800 058 2844.", [R["A.1-5min"], R["A.1-phone"]]))
    return f


# ---------------------------------------------------------------- Use case 4: refunds and compensation
CLAIM_EVENTS = {
    "late": "Travelled, but arrived late at the destination",
    "not_travelled": "Did not travel because the train was cancelled, delayed or rescheduled (or the reservation was not honoured)",
    "returned": "Started the journey, could not complete it because of a delay or cancellation, and returned to the start",
    "changed_mind": "Chose not to travel (no disruption)",
    "no_first_class": "Held a first class ticket, but no first class seat was available",
}
TICKET_TYPES = {"single": "Single", "return_both": "Return — delayed on both legs",
                "return_one": "Return — delayed on one leg", "season": "Season Ticket"}


def claim_advice(event: str, ticket: str = "single", minutes_late: int = 0, days_since: int = 0,
                 price: float | None = None, leg_price: float | None = None,
                 refund_already: bool = False) -> list[Finding]:
    f: list[Finding] = []

    def deadline(limit_cite, what):
        if days_since > 28:
            f.append(Finding(WARN, "Outside the 28-day deadline",
                             f"{days_since} days have passed; {what} must be made within 28 days.", [limit_cite]))
        else:
            f.append(Finding(OK, "Within the 28-day deadline", f"{days_since} days since; {what} within 28 days.",
                             [limit_cite]))

    if event == "late":
        if refund_already:
            f.append(Finding(STOP, "No compensation on top of a full refund",
                             "A passenger who chose not to travel and claimed a full refund under Condition 30 cannot also "
                             "claim delay compensation.", [R["33.5"]]))
            return f
        if ticket == "season":
            f.append(Finding(INFO, "Season Ticket",
                             "Compensation for Season Tickets follows the Train Company’s Passenger’s Charter, not the table "
                             "in Condition 33.3.", [R["33.3-season"]]))
        elif minutes_late >= 60:
            pct = "50% of the price paid" if ticket in ("single", "return_both") else \
                  "50% of the price paid for the delayed portion of the return journey"
            amount = ""
            base = leg_price if ticket == "return_one" else price
            if base:
                amount = f" Minimum for this claim: £{base * 0.5:.2f}."
            f.append(Finding(OK, f"Compensation due — {minutes_late} minutes late",
                             f"Delays of 60 minutes or more are compensated regardless of fault. Minimum: {pct}.{amount} "
                             "The passenger does not need to prove fault.",
                             [R["32.1"], R["33.3"], R["33.3-fault"]]))
        else:
            f.append(Finding(INFO, f"Under 60 minutes ({minutes_late} min): check the Passenger’s Charter",
                             "The Conditions only set the minimum for delays of 60+ minutes. Compensation for shorter delays "
                             "(Delay Repay thresholds) is set in the Train Company’s Passenger’s Charter, which is not in "
                             "these documents. A Train Company may not be obliged to pay for delays under 60 minutes caused "
                             "entirely outside the rail industry’s control.",
                             [R["32.1-charter"], R["33.4"]]))
        deadline(R["33.1"], "the claim (from completing the journey)")
        f.append(Finding(INFO, "Limits and payment",
                         "Claims under the industry arrangements recover at most the ticket price. Payment within 14 days "
                         "of the claim being agreed; options include at least one money option, and if the Train Company "
                         "caused the delay, payment by the same means the ticket was bought.",
                         [R["32.2"], R["33.2"], R["34.1"], R["34.1-fault"]]))
    elif event in ("not_travelled", "returned"):
        if ticket == "season":
            f.append(Finding(INFO, "Season Ticket", "Season Tickets have their own process when disruption prevents travel.",
                             [R["30.1-season"]]))
        else:
            f.append(Finding(OK, "Full refund, no administration fee (Condition 30.1)",
                             "Return the unused ticket(s) to the original retailer or Train Company. "
                             + ("This also covers starting the journey and returning to the origin because of the disruption. "
                                if event == "returned" else ""),
                             [R["30.1"]] + ([R["30.1-return"]] if event == "returned" else [])))
            deadline(R["30.2"], "the refund claim (from the intended travel date)")
            f.append(Finding(INFO, "Also note",
                             "Refund paid within 14 days of being agreed. A passenger who takes this refund cannot also claim "
                             "delay compensation. For cancellations, passengers can usually travel up to two trains before or "
                             "after the affected service instead.", [R["30.3"], R["33.5"], R["28.3"]]))
    elif event == "changed_mind":
        f.append(Finding(INFO, "Refund from the original retailer (Condition 29)",
                         "Unless the ticket’s terms say otherwise. An administration charge of up to £5 per ticket "
                         "(£10 for Season Tickets) may be deducted.", [R["29.1"], R["29.2"]]))
        f.append(Finding(INFO, "Deadline", "Apply no more than 28 days after the ticket expires.", [R["29.1"]]))
    elif event == "no_first_class":
        f.append(Finding(OK, "Refund of at least the fare difference (Condition 31.1)",
                         "At least the difference between the first class ticket and the cheapest valid standard walk-up fare.",
                         [R["31.1"]]))
        deadline(R["31.1-28"], "the claim (from the travel date)")
    if minutes_late > 60 and event == "late":
        f.append(Finding(INFO, "During long delays",
                         "Where a delay of over 60 minutes is likely, the ticket can be used on a later date; meals and "
                         "refreshments may be provided in some circumstances.", [R["28.4"], R["28.6"]]))
    return f


# ---------------------------------------------------------------- Use case 5: fleet
FLEET_FIELDS = [
    ("dedicated_accessible_carriage_with_wheelchair_space", "Accessible carriage with wheelchair space"),
    ("standard_toilet", "Standard toilet"),
    ("accessible_toilet", "Accessible toilet"),
    ("aural_information", "Audio information"),
    ("visual_information", "Visual information"),
    ("priority_seats", "Priority seats"),
    ("accessible_signage_outside_train", "Accessible signage outside"),
    ("scooter_mobility_aid_acceptance", "Scooters / mobility aids"),
    ("boarding_ramp", "Boarding ramp"),
    ("on_train_staff", "On-train staff"),
]


def load_fleet() -> list[dict]:
    with open(config.DATA_DIR / "curated" / "fleet_accessibility.csv", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def fleet_label(row: dict) -> str:
    return f"{row['brand']} Class {row['train_class']}"

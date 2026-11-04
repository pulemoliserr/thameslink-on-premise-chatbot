# Evaluation report — 2026-11-04 18:23

32 questions (26 answerable, 6 out of scope), 206 passages.

## Retrieval

| Method | Hit@1 | Hit@3 | Hit@5 | MRR |
| --- | --- | --- | --- | --- |
| bm25 | 62% | 92% | 92% | 0.75 |

## Answers

| Generator | Correct | Cites right source | Wrongly refused | Correctly refused (out of scope) |
| --- | --- | --- | --- | --- |
| extractive | 92% | 85% | 8% | 100% |

## Per question

Rank = position of the first correct passage (blank = not in top 10).

| ID | Question | Rank bm25 | Extractive correct |
| --- | --- | --- | --- |
| q01 | How far in advance should a passenger book assistance? | 2 | True |
| q02 | A passenger forgot their Railcard and had to pay extra. Can they get that money back? | 3 | True |
| q03 | How many bags is a passenger allowed to bring on board? | 3 | True |
| q04 | Can a customer bring their pet dog on the train? | 1 | True |
| q05 | Are guide dogs and other assistance dogs allowed on our trains? | 1 | True |
| q06 | What are the maximum dimensions for a wheelchair or mobility scooter we can take on board? | 2 | True |
| q07 | Is there an accessible toilet on a Class 717 train? | 1 | True |
| q08 | Do Thameslink Class 700 trains have on-board staff? | 1 | True |
| q09 | A wheelchair user is boarding at St Pancras. Will they need a ramp? | 1 | True |
| q10 | What should a passenger do at an unstaffed station if they need help? | 1 | True |
| q11 | What is the deadline for submitting a delay compensation claim? | 1 | True |
| q12 | Is there an admin fee when refunding an unused ticket? | 3 | True |
| q13 | How much does it cost to replace a damaged ticket? | 2 | True |
| q14 | The train was cancelled and the passenger decided not to travel. Can they get their money back? | 1 | True |
| q15 | What must we do if disruption stops a passenger from finishing their journey? | – | True |
| q16 | Can someone with a standard ticket sit in first class? | 1 | True |
| q17 | When can a passenger be charged a Penalty Fare? | 2 | True |
| q18 | Are e-bikes and e-scooters allowed on trains? | 1 | True |
| q19 | If a station is not accessible for a disabled passenger, will we pay for a taxi? | 2 | True |
| q20 | What discount does the Disabled Persons Railcard give? | 1 | True |
| q21 | Booked assistance didn't turn up. Can the passenger claim anything? | – | False |
| q22 | How long might it take staff to help someone off the train at the final stop? | 1 | True |
| q23 | A passenger hasn't booked help and is at an unstaffed station. How early should they arrive? | 1 | True |
| q24 | Can a mobility scooter go on a rail replacement bus? | 1 | True |
| q25 | Is there somewhere at St Pancras where a passenger can leave luggage? | 1 | True |
| q26 | How fast is a mobility scooter allowed to go inside a station? | 1 | False |
| u01 | What time is the last train from Brighton tonight? | n/a | refused ✓ |
| u02 | How much is an Anytime Return from Bedford to London? | n/a | refused ✓ |
| u03 | Is the lift at Farringdon working today? | n/a | refused ✓ |
| u04 | What is the Wi-Fi password on Class 700 trains? | n/a | refused ✓ |
| u05 | How do I reset a ticket gate that is jammed? | n/a | refused ✓ |
| u06 | How much Delay Repay does a passenger get for a 15-minute delay? | n/a | refused ✓ |

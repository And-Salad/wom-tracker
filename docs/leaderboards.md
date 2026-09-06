# The leaderboards, and the recaps

How a day and a month are decided, why there are two competitions over
the same days, and what the written round-ups do and do not settle.
Operational instructions are in the [README](../README.md).

## The leaderboards

Two competitions over the same days, on the same readings, disagreeing on
purpose. **Maxing** counts experience only up to ninety-nine in each skill and
a ninety-nine takes a day outright; **Grinding** counts all of it and nothing
else. An account with everything maxed cannot place on the first and can win
the second, which is the point of having both.

One rule, `winners.key(shown, board)`, and one page. Grinding shows a single
Wins column where Maxing splits it: a ninety-nine never takes a grinding day, so
the split would be a column that could only ever read nothing.

They share the **Leaderboards** tab and a toggle, the same one the recaps use.
Both are rendered whichever is chosen and the other is put away - one more pass
over the same two months - so switching costs nothing. `?board=` picks the one
shown, which is what the toggle remembers between visits and what lets
`/maxing` and `/grinding` still answer, as redirects to the board they named.

## The leaderboard page

It colours a calendar by who took each day. On **Maxing**: whoever reached a 99
takes it, two beat one, and failing that it goes on experience counted only up
to level 99 in each skill - so an account with everything maxed cannot take a
day off people still climbing. A day is blank unless the tracker actually polled
everyone that day. A month goes to the best average across its days, and is not
awarded at all unless a fortnight of them counted. Mousing over the description
on the page gives the whole rule.

Below the calendar, in its own card, is the day still running - ranked by the
same measure and deliberately not a verdict, since today has not been polled to
its end. Below that again the same figures are drawn as a line per account,
midnight to midnight, so the axis is the whole day rather than however much of
it has happened. Opening an account gives the skills behind its day, including
the experience past 99 that the rule does not count. The prose lives on the
Recaps tab; this page is figures.

All three - the row, the skills behind it and the line under it - open the day
at the same reading, through `winners.day_span`. Wise Old Man stamps a reading
when the hiscores move, so an evening's last hour often arrives seconds into
the next day, and which reading opens the day decides whose day that work
counts toward.

The calendar and the standings under it **ignore the sidebar's ticks**, and
judge every tracked account whatever is ticked. It is one competition with one
answer: narrowed to three of six it silently becomes a different competition,
and the squares would recolour to a result nobody was playing for. The two are
given the same set as each other for the same reason - the standings tally each
account's wins this month from the very verdicts the squares are coloured by, so
a calendar judged across everyone beside a table judged across three would
credit different days on one page. The verdict chips on the Recaps tab quote the
calendar, so they are asked the same question too.

The chart below them does follow the ticks - it is a line per account, and
thinning it is what the ticks are for. Nothing on this page reloads when they
change; the chart simply redraws.

## Recaps

Round-ups are written per calendar window and now per leaderboard: the day, the
week and the month, for Maxing and for Grinding, which is six a cycle. A window
is owed until *both* boards have one - settling it on the first would leave the
second with nothing for ever, because the window never comes due again.

The week is the new one and it is not a verdict. Neither leaderboard awards a
week, so its round-up reviews instead: who took each of its days, and where
that leaves the month's running average. It carries no winner chip, because
there is no award to name.

Each board's digest opens with its own rule, and says its standings by that
rule too. The two are the same figures judged differently, and a round-up
handed only the numbers would pick the winner the numbers suggest rather than
the one who won.

A digest also carries what the players' own clients reported during the window
- a quest finished, a diary done, a combat task, a pet, which drop filled a
collection log slot. Wise Old Man gives us 99s and thresholds, which is a thin
slice of an evening; these are the things somebody would actually mention, and
for a long time the round-up was the one thing writing about the period that
could not see any of them. They are opt-in per player, so both prompts are told
that an account with none was not silent, it was not reporting - the same rule
the coverage lines follow.

Deaths are not among them, though they arrive on the same webhook and have
their own shelf in the Gallery. A round-up is about what somebody did, and one
that reached for the deaths would be writing about the thing they would least
like read back to them.

Written per **calendar window** rather than over a rolling period: "Saturday 29
August", "August 2026" - a closed span with a name, so a recap can be filed,
kept, and compared with the one before it. The first update of each local day
writes whatever has closed and not yet been written, so a machine asleep through
the 1st still writes that month when it wakes.

There are two kinds, and they cover different windows.

The **group recap** on the Recaps tab is the leaderboard's feed, so it covers
the windows a leaderboard has something to say about: the **day**, the **week**
and the **month**. The day and the month carry the leaderboard's own verdict
beside them - who took that day, who took that month, or that the month went
unawarded. The week carries none, because neither board awards one. The recap names a
winner of its own in its prose and the calendar does not read it: the squares
are arithmetic and the round-up is comment. Where the two differ, the chip
quotes the calendar, and the difference is the interesting part.

A **player's own notes** cover all five windows - day, week, month, quarter and
year - because those are about one account's progress, which a quarter still
says something about even where the leaderboard has no verdict for it. They sit
under that account's own branch in the tree below the group's, and carry no
verdict: a note about one account is not something the calendar has an opinion
about.

Each stores a hash of its digest, so a player whose numbers have not moved is
skipped rather than re-billed. A day costs well under a cent; a full set of
windows across six players is a few cents. The model and how hard it thinks
are both set under Admin, and both move that figure - and the round-up may
have its own of each. They are not the same job: a note is a paragraph of
colour, where a round-up follows a stated rule and respects a winner the site
has already decided. Left unset it uses whatever the notes use.

Each also stores the digest it was written from, and a hash of the prompt that
was in force. The hash answers "has this changed"; only the digest itself
answers "why did it say that", and by the time anybody asks, the readings
behind it are gone - compaction thins past thirty days and attribution
recomputes its own rows. Anything written before that was kept has both
columns empty, which is the honest answer rather than a reconstruction.

What each one is told is a prompt you can edit, under **Admin → Prompts**. Two
base prompts cover every window - one for a player's own note, one for the group
recap - and any window may override its own, which is how a yearly retrospective
can be asked for something a daily note should not say. The page offers a group
override only for the windows a round-up is written for, since a group prompt
for a quarter would be a file nothing ever loads. It lists every prompt there is, creates an
override seeded from the base, and removes one to fall back again. They live in
`data/`, so they are yours and survive a redeploy; `backup.py` brings them down
with the database.

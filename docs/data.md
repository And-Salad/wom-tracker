# The schedule, and what is stored

Where the readings come from, what a player's own client can add to
them, and what ends up on disk. Operational instructions are in the
[README](../README.md).

## Schedule

Updates run **every ten minutes**, on the wall-clock boundary. Milestones are
fetched on the hour rather than every pass - they move rarely and cost a request
per player. A slot that passed while the machine was off is caught up when it
starts.

Everything dated - day boundaries, the calendar, the window each recap covers
- follows midnight in the **time zone set under Admin**, so it tracks that
place's daylight saving rather than the server's. The ten-minute interval itself
is `SLOT_MINUTES` in `wom/scheduler.py`.

## Session logins

Wise Old Man can only ever tell us a session has *ended*: the hiscores do not
move until logout, so three hours of training arrives as one jump and lands in
whichever ten-minute window we happened to notice it in.

[Dink](https://github.com/pajlads/DinkPlugin), a Plugin Hub plugin, reports
both ends as they happen. Point its **Custom Metadata Handler** setting at a
URL and it POSTs a login - about six seconds in, not on world hops - carrying
that account's own live reading of its experience, and a logout, which carries
only the fact and the moment. The logout beats what we can infer twice over: it
is the moment itself rather than a ten-minute bracket, and it arrives however
little was gained, where the hiscore route says nothing under 10,000 experience.
Setting that one field is all a player has to do; the Discord webhook box beside
it can stay empty.

Each event is stored twice over in time: `received_at` is when the POST reached
us, `happened_at` is the moment Dink stamped it with. Attribution measures
between the second, because Dink retries a delivery it could not make and a
session resolved from arrival would be shortened by however long the retry
took. A client clock more than thirty minutes from ours is not believed - the
payload is the one part of that request nobody had to prove.

Issue and revoke a player's URL from the admin page, which also shows whether
each account is in game, which world, and when we last heard from them. That
status is inferred from the last event and is only ever as good as the last
thing Dink managed to send - a client that crashed sent no logout, so a login
older than a session could be stops claiming they are still playing. It must be the **https** one the page prints: waitress
strips `X-Forwarded-Proto`, so the app cannot tell how it was reached and the
URL is built as https for any non-local host rather than read off the request.
An http URL is redirected, okhttp turns the redirected POST into a GET, and the
body is lost - so the endpoint answers a GET by naming the scheme rather than
returning a bare 405. The plugin cannot send a header, so the URL *is* the
credential, and each player gets their own: it says who is calling without
trusting the name in the body, and one that leaks is revoked alone. Whatever
Dink offers about someone's Discord account or clan is dropped as the body is
read, never stored.

A player can opt into more. Putting the same URL in **Primary Webhook URLs**
as well - not instead, the metadata field is what carries the logins - and
ticking *Collection Log*, *Level Up*, *Kill Count*, *Quests*, *Achievement
Diary* and *Combat Achievements* sends what happens during a session too. Every
notifier ships off, so nothing else comes with them.

Collection log slots, quests, diaries, combat tasks and pets join Wise Old Man's
own milestones on the **Milestones** page, merged and sorted together, each row
tagged with what it is so the filter above the table can hide a kind. Levels and
boss counts stay off it: those are progress, and progress belongs on a chart.

Deaths and pets can also carry a screenshot, and those appear on the **Gallery**
page, ten of each with a toggle per kind. Images are accepted for those two
kinds and refused for every other, because a public endpoint that takes
arbitrary bytes should take as few as it can. The format is read out of the
bytes rather than believed from the request, the file is named by the digest of
its own contents so nothing a client sends becomes a path, and forty of each
kind are kept against a 250 MB ceiling. They live on the volume rather than in
the database - `backup.py` does not carry them, which is the deliberate answer
for something decorative.

Those land in `game_events` whole, because the interesting part is the detail
no metric has room for: which item, from which drop, at which rank. Where the
payload *is* a metric we already track, the value is also written at the moment
it happened - a collection log slot and a boss count both are - so those stop
being rounded to the next ten-minute reading. Levels are kept but not written
through: our level total shares a row with overall experience, and half a row
would read as a whole one.

Kill Count ships at every fiftieth kill; set *Kill Count Interval* to 1 for
every one. Experience itself cannot be streamed - Dink's XP milestones fire
only for skills already at 99, at a million or more apart.

Where both ends of a session are known and it crossed a local midnight, the app
records what the account had earned by that midnight, so a four-hour evening
session is credited to the evening rather than entirely to the minute after it
ended. Every total, chart and leaderboard picks that up without special-casing
it. An account with no session events is untouched and reports exactly what it
always did.

## Readings we did not take

`update_player` refreshes a player and hands back their latest snapshot and
nothing else. Wise Old Man keeps one whenever *anybody* asks it to look - most
often a player's own client pushing at logout - and one of those made between
two of our polls is invisible to us however often we ask.

That matters at a day boundary. Somebody who logs out at 23:55 is noticed by
our poll at 00:10, and their whole evening lands on the wrong day. So each pass
also re-reads history and keeps anything new, which puts that reading back
where it happened - for every account, with nothing for anyone to install.

It reads from just before its own last reading rather than a flat window, so
normally that is a snapshot or two. Asked for a flat three hours it returned
every ten-minute reading in them, almost all already held, and that alone
turned an eighteen second pass into a minute. After an outage the window
widens by itself, because the last reading is older.

## What gets stored

SQLite, in `data/wom.db`. A reading repeats the one before it for most metrics -
a boss sitting at zero was being written again every ten minutes forever - so a
row is stored only when a value actually moves, and every read carries the last
one forward. Rank is not a move: a hiscore position drifts because strangers
played, and that alone was 83% of every row ever written.

That took six players' year of history from 17.9 MB to under 3 MB. Beyond 30
days, compaction thins what is left to one reading a day, which is finer than a
month-wide chart can draw; it runs once a day, on the first update after
midnight.

With two exceptions. Each reading records an `origin`: `poll` when our own
request caused Wise Old Man to take it, `archive` when it already existed and
we were only collecting it, `derived` when the app worked it out, `reported`
when a plugin told us outright. Compaction thins only `poll`, and recomputing
attribution clears only `derived` - a reported value is evidence, not
arithmetic. Compaction never thins an `archive` reading. A
polled one can be taken again tomorrow, so thinning it costs a detail; an
archive one is a moment recorded without us - a player's client pushing on
logout, most often - and is the only evidence of when that session ended, so
thinning it loses the timestamp for good. They are cheap to keep: on the live
database they are 317 of 2,470 readings and carry 18,749 of 20,340 metric rows,
which is 92% of the history for 13% of the readings.

`backup.py` pulls a copy of the hosted database taken with SQLite's backup API
*inside* the container - a consistent file, not a copy with recent writes
stranded in the write-ahead log - then opens and counts it before believing it.
It brings the prompts and settings down beside it, since those live only on the
volume, and keeps the newest fourteen of each. `backup_schedule.ps1` registers
it as a daily task on Windows.

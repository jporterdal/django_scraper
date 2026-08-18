# Scheduled scrapes (`UpdateSchedule`)

This guide is for operators running recurring price scrapes. A schedule is an
`UpdateSchedule` row: a name, a preset **frequency**, an **anchor time**, an
optional **tag** scope, and an **enabled** flag. When due, a schedule enqueues
the same background run as the manual "Update All Active / Update Selected"
buttons (`run_web_update_task`), so results and errors show up in the usual
places.

Manage schedules in the UI under **Schedules** (`/schedules/`) — list, add
(`/schedules/add/`, also reachable via the legacy `add_update` link), edit, and
delete — or in the Django admin.

## Running the scheduler

Scheduled runs are dispatched by a Huey **periodic task**
(`tracking/tasks.py::dispatch_scheduled_updates`, `@periodic_task(crontab(minute="*"))`).
That task lives in the **Huey worker process**, not the web process:

```bash
# Requires a reachable Redis (settings.REDIS_URL, default redis://localhost:6379/0)
python manage.py run_huey
```

Key consequences:

- **Nothing schedules while the consumer is stopped.** The web app can create and
  edit schedules, but only a running `run_huey` consumer wakes up each minute and
  actually dispatches due runs.
- **Local/dev immediate mode has no scheduler.** With `HUEY["immediate"] = True`
  (the default when `DEBUG` is on, including the test suite), tasks run inline in
  the calling process and there is **no separate worker and no periodic
  scheduler**. So real periodic scheduling is effectively a
  production / long-running-process feature. To exercise it locally, run with
  `DEBUG=False` (or `HUEY_IMMEDIATE=False`) against a live Redis and start
  `run_huey`.
- Redis is only needed for the real queue/worker; the app and its tests run fine
  without it.

## Frequency preset semantics

Pick one preset per schedule (no free-form cron). `anchor_time` is always
interpreted in **America/Halifax** local time.

| Frequency    | Runs per day | When it runs (relative to `anchor_time`)                              |
|--------------|--------------|----------------------------------------------------------------------|
| Hourly       | 24           | Once each hour, at the **minute** of `anchor_time` (the hour is ignored — e.g. `09:15` ⇒ every hour at :15). |
| Twice Daily  | 2            | At `anchor_time` **and** again 12 hours later (e.g. `09:00` ⇒ 09:00 and 21:00). |
| Daily        | 1            | Once per day at `anchor_time` (e.g. `09:00` ⇒ 09:00).                 |

All times are local Atlantic time; `last_run_at` is stored in UTC like every
other timestamp.

## How due-checking works

The dispatcher wakes **every minute** and asks each enabled schedule whether it
is due (`UpdateSchedule.is_due(now)`). Due-checking is interval based, with an
interval per preset:

| Frequency    | Interval |
|--------------|----------|
| Hourly       | 60 min   |
| Twice Daily  | 720 min  |
| Daily        | 1440 min |

A schedule is **due** when all of the following hold:

1. It is **enabled**.
2. Its interval has elapsed since `last_run_at` (or it has never run) — this
   guarantees it **never double-fires within a period**.
3. Local time has reached the most recent aligned occurrence (the `anchor_time`
   slot for Daily/Twice Daily, or the anchor minute for Hourly) that it hasn't
   already run for.

Practical implications:

- Runs are **approximate to the minute**, not exact to the second — a run fires
  on the first minute-wake at or after its anchor slot.
- **No backfill.** If the worker is down across one or more windows, the schedule
  simply runs **once** on the next wake after it comes back; missed windows are
  not replayed.

## Tag scoping

- **No tag** → the run covers **all active items** (identical to "Update All
  Active").
- **A tag** → the run covers **only active items carrying that tag**.
- If a tag-scoped schedule's tag currently matches **no active items**, the
  dispatcher stamps `last_run_at` and skips the run (nothing to scrape) rather
  than silently scraping everything.

**Manual tag update vs scheduled tag scope:** On the item list, filter by a
tag and use **Update items with this tag** (`POST /update/` with `mode=tag`).
That enqueues a one-off background run for the same active, tag-matched item set
as a tag-scoped schedule would use, but it runs immediately when you click the
button — it is not tied to `anchor_time`, frequency, or `last_run_at`, and it
does not stamp any schedule row.

## Operational notes

- **Where to see outcomes:** each dispatched run creates a `WebUpdate` row, so
  results appear in **Scrape History** (`/view_updates/`) and on item detail
  pages, exactly like manual updates. Per-search failures are recorded as
  `FetchJob` rows (visible in the Django admin) with a status such as
  `http_error`, `parse_error`, `oversized`, `empty`, or `blocked` (rate limits /
  bot challenges).
- **POST pagination:** raising `Source.max_pages` above 1 for a POST source only
  fetches additional pages when the configured parser implements
  `next_page_body` (returning the JSON body for page 2+). Without that hook,
  POST searches stay single-page regardless of `max_pages`. GET sources paginate
  via each parser's `next_page_url` instead.
- **Rate limiting / duration:** runs honor the per-source request delay
  (`SCRAPE_REQUEST_DELAY_SECONDS` + jitter), so a large schedule (many
  item-sources) can take a while. Space out or tag-scope schedules so an Hourly
  cadence can actually finish within the hour.
- **Disabling vs deleting:** turn a schedule's **Enabled** off to pause it while
  keeping its configuration and history; **delete** it to remove it entirely.
  Neither action touches previously stored results.
- **Timezone / DST caveat:** `anchor_time` is Atlantic local time. Around the
  twice-a-year DST transitions the local wall-clock offset shifts by an hour, so
  a Daily/Twice-Daily run near the transition may land up to an hour off its
  usual UTC instant for that day. This is expected; the schedule self-corrects on
  subsequent days.

"""Tests for Phase 3 Step 4 — recurring UpdateSchedule scrapes.

All tests run with no Redis and no network: Huey is in immediate mode under the
suite (see settings), and the dispatcher's ``run_web_update``/``Fetcher`` calls
are mocked. Due-check cases use a fixed, timezone-aware ``now`` in America/Halifax.
"""

from datetime import datetime, time
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django import forms
from django.test import TestCase, override_settings
from django.urls import reverse

from tracking import tasks
from tracking.forms import UpdateScheduleForm
from tracking.models import (
    ItemSource,
    SearchableItem,
    Tag,
    UpdateSchedule,
    WebUpdate,
)
from tracking.tests.base import AuthedClientTestCase
from tracking.tests.factories import make_item, make_item_source, make_linked_item, make_source

HALIFAX = ZoneInfo("America/Halifax")

DAILY = UpdateSchedule.Frequency.DAILY
TWICE_DAILY = UpdateSchedule.Frequency.TWICE_DAILY
HOURLY = UpdateSchedule.Frequency.HOURLY


def hfx(year, month, day, hour, minute=0):
    """Build a timezone-aware America/Halifax datetime."""
    return datetime(year, month, day, hour, minute, tzinfo=HALIFAX)


class UpdateScheduleModelTests(TestCase):
    def test_str_for_each_frequency(self):
        for freq, label in [
            (HOURLY, "Hourly"),
            (TWICE_DAILY, "Twice Daily"),
            (DAILY, "Daily"),
        ]:
            sched = UpdateSchedule.objects.create(
                name="Nightly", frequency=freq, anchor_time=time(9, 0)
            )
            self.assertEqual(str(sched), f"Nightly ({label})")

    def test_default_frequency_is_daily(self):
        sched = UpdateSchedule.objects.create(name="D", anchor_time=time(0, 0))
        self.assertEqual(sched.frequency, DAILY)
        self.assertEqual(sched.interval_minutes, 1440)

    def test_interval_minutes_per_preset(self):
        self.assertEqual(
            UpdateSchedule(frequency=HOURLY).interval_minutes, 60
        )
        self.assertEqual(
            UpdateSchedule(frequency=TWICE_DAILY).interval_minutes, 720
        )
        self.assertEqual(
            UpdateSchedule(frequency=DAILY).interval_minutes, 1440
        )


@override_settings(TIME_ZONE="America/Halifax", USE_TZ=True)
class DueCheckTests(TestCase):
    def test_daily_due_at_or_after_anchor_then_not_again_same_day(self):
        sched = UpdateSchedule(
            name="Daily", frequency=DAILY, anchor_time=time(9, 0), enabled=True
        )

        # Never run, just after the anchor -> due.
        self.assertTrue(sched.is_due(hfx(2026, 7, 8, 9, 5)))

        # After running at 09:05, later the same day -> not due (interval guard).
        sched.last_run_at = hfx(2026, 7, 8, 9, 5)
        self.assertFalse(sched.is_due(hfx(2026, 7, 8, 15, 0)))
        self.assertFalse(sched.is_due(hfx(2026, 7, 8, 23, 0)))

    def test_twice_daily_due_at_both_windows_not_between(self):
        sched = UpdateSchedule(
            name="Twice", frequency=TWICE_DAILY, anchor_time=time(9, 0), enabled=True
        )

        # Never run, at the first window -> due.
        self.assertTrue(sched.is_due(hfx(2026, 7, 8, 9, 5)))

        # Ran at first window; between windows -> not due.
        sched.last_run_at = hfx(2026, 7, 8, 9, 5)
        self.assertFalse(sched.is_due(hfx(2026, 7, 8, 15, 0)))

        # ~12h later at the second window -> due again.
        self.assertTrue(sched.is_due(hfx(2026, 7, 8, 21, 5)))

    def test_hourly_due_after_anchor_minute_not_within_same_hour(self):
        sched = UpdateSchedule(
            name="Hourly", frequency=HOURLY, anchor_time=time(9, 15), enabled=True
        )

        # Never run, past this hour's anchor minute -> due.
        self.assertTrue(sched.is_due(hfx(2026, 7, 8, 10, 20)))

        # Ran at 10:20; still within the same hour -> not due.
        sched.last_run_at = hfx(2026, 7, 8, 10, 20)
        self.assertFalse(sched.is_due(hfx(2026, 7, 8, 10, 50)))

        # Next hour, past the anchor minute -> due again.
        self.assertTrue(sched.is_due(hfx(2026, 7, 8, 11, 20)))

    def test_disabled_schedule_is_never_due(self):
        sched = UpdateSchedule(
            name="Off", frequency=HOURLY, anchor_time=time(0, 0), enabled=False
        )
        self.assertFalse(sched.is_due(hfx(2026, 7, 8, 12, 30)))


@override_settings(TIME_ZONE="America/Halifax", USE_TZ=True)
class DispatcherTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.source, cls.item, cls.item_source = make_linked_item(
            item_text="widget",
            source=make_source(name="CC"),
        )

    def test_get_due_schedules_filters_enabled_and_due(self):
        due = UpdateSchedule.objects.create(
            name="Due", frequency=DAILY, anchor_time=time(9, 0), enabled=True
        )
        UpdateSchedule.objects.create(
            name="Disabled", frequency=DAILY, anchor_time=time(9, 0), enabled=False
        )
        now = hfx(2026, 7, 8, 9, 5)
        result = tasks.get_due_schedules(now=now)
        self.assertEqual([s.pk for s in result], [due.pk])

    def test_due_schedule_creates_webupdate_enqueues_and_stamps(self):
        sched = UpdateSchedule.objects.create(
            name="Daily", frequency=DAILY, anchor_time=time(9, 0)
        )
        now = hfx(2026, 7, 8, 9, 5)
        with patch("tracking.tasks.run_web_update_task") as mock_task:
            dispatched = tasks.dispatch_due_schedules(now=now)

        self.assertEqual(len(dispatched), 1)
        webupdate = WebUpdate.objects.get()
        self.assertEqual(webupdate.status, WebUpdate.Status.PENDING)
        self.assertEqual(webupdate.total_searches, 1)
        mock_task.assert_called_once()
        self.assertEqual(mock_task.call_args.args[0], webupdate.pk)
        # No tag -> all active items (item_ids is None).
        self.assertIsNone(mock_task.call_args.kwargs["item_ids"])
        sched.refresh_from_db()
        self.assertEqual(sched.last_run_at, now)

    def test_tag_scoped_schedule_limits_items(self):
        tag = Tag.objects.create(name="gpu")
        self.item.tags.add(tag)
        other = make_item(text="other", active=True)
        make_item_source(other, self.source)

        sched = UpdateSchedule.objects.create(
            name="Tagged", frequency=DAILY, anchor_time=time(9, 0), tag=tag
        )
        now = hfx(2026, 7, 8, 9, 5)
        with patch("tracking.tasks.run_web_update_task") as mock_task:
            tasks.dispatch_due_schedules(now=now)

        item_ids = mock_task.call_args.kwargs["item_ids"]
        self.assertEqual(list(item_ids), [self.item.pk])
        sched.refresh_from_db()
        self.assertEqual(sched.last_run_at, now)

    def test_tag_with_no_active_items_is_stamped_but_skipped(self):
        tag = Tag.objects.create(name="empty")
        sched = UpdateSchedule.objects.create(
            name="Empty", frequency=DAILY, anchor_time=time(9, 0), tag=tag
        )
        now = hfx(2026, 7, 8, 9, 5)
        with patch("tracking.tasks.run_web_update_task") as mock_task:
            dispatched = tasks.dispatch_due_schedules(now=now)

        self.assertEqual(dispatched, [])
        mock_task.assert_not_called()
        self.assertEqual(WebUpdate.objects.count(), 0)
        sched.refresh_from_db()
        self.assertEqual(sched.last_run_at, now)

    def test_dispatch_runs_task_inline_in_immediate_mode(self):
        # Immediate Huey (default under tests) runs the enqueued task inline;
        # mock run_web_update so no Fetcher/network is touched.
        sched = UpdateSchedule.objects.create(
            name="Daily", frequency=DAILY, anchor_time=time(9, 0)
        )
        now = hfx(2026, 7, 8, 9, 5)
        with patch("tracking.scrape.run_web_update") as mock_run:
            # Immediate Huey pickles the task's return value, so hand back a
            # simple picklable value rather than a MagicMock.
            mock_run.return_value = None
            tasks.dispatch_due_schedules(now=now)

        mock_run.assert_called_once()
        webupdate = WebUpdate.objects.get()
        self.assertEqual(mock_run.call_args.kwargs["webupdate"], webupdate)
        self.assertIsNone(mock_run.call_args.kwargs["items"])


class ScheduleCRUDViewTests(AuthedClientTestCase):
    def test_list_view_shows_schedules(self):
        UpdateSchedule.objects.create(name="Nightly", anchor_time=time(9, 0))
        response = self.client.get(reverse("view_schedules"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nightly")
        self.assertContains(response, "Daily")

    def test_create_view_get_renders_frequency_select(self):
        response = self.client.get(reverse("add_schedule"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<select")
        for label in ["Hourly", "Twice Daily", "Daily"]:
            self.assertContains(response, label)

    def test_add_update_alias_still_resolves(self):
        response = self.client.get(reverse("add_update"))
        self.assertEqual(response.status_code, 200)

    def test_create_view_post_creates_schedule(self):
        response = self.client.post(
            reverse("add_schedule"),
            {
                "name": "New",
                "frequency": "hourly",
                "anchor_time": "09:30",
                "enabled": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("view_schedules"))
        sched = UpdateSchedule.objects.get(name="New")
        self.assertEqual(sched.frequency, HOURLY)
        self.assertEqual(sched.anchor_time, time(9, 30))

    def test_edit_view_updates_schedule(self):
        sched = UpdateSchedule.objects.create(name="Old", anchor_time=time(9, 0))
        response = self.client.post(
            reverse("edit_schedule", args=[sched.pk]),
            {
                "name": "Renamed",
                "frequency": "daily",
                "anchor_time": "10:00",
                "enabled": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        sched.refresh_from_db()
        self.assertEqual(sched.name, "Renamed")
        self.assertEqual(sched.anchor_time, time(10, 0))

    def test_delete_view_removes_schedule(self):
        sched = UpdateSchedule.objects.create(name="Del", anchor_time=time(9, 0))
        response = self.client.post(reverse("delete_schedule", args=[sched.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(UpdateSchedule.objects.filter(pk=sched.pk).exists())


class ScheduleFormTests(TestCase):
    def test_frequency_rendered_as_select_of_presets(self):
        form = UpdateScheduleForm()
        self.assertIsInstance(form.fields["frequency"].widget, forms.Select)
        html = str(form["frequency"])
        self.assertIn("<select", html)
        for label in ["Hourly", "Twice Daily", "Daily"]:
            self.assertIn(label, html)

    def test_no_freeform_cron_or_interval_field(self):
        form = UpdateScheduleForm()
        self.assertNotIn("cron", form.fields)
        self.assertNotIn("interval", form.fields)

    def test_frequency_has_no_blank_option(self):
        # Default is DAILY, so the select should not offer an empty choice.
        form = UpdateScheduleForm()
        self.assertNotIn("---------", str(form["frequency"]))

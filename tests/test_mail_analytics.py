import ast
import asyncio
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from datetime import datetime, timezone, timedelta

import psycopg2
import mail_analytics as analytics


def connect():
    return psycopg2.connect(os.environ["DATABASE_URL"])


class MetadataTests(unittest.TestCase):
    def test_service_categories(self):
        self.assertEqual(analytics.classify_sender("security@facebookmail.com"), "facebook")
        self.assertEqual(analytics.classify_sender("alert@mail.instagram.com"), "instagram")
        self.assertEqual(analytics.classify_sender("no-reply@accounts.google.com"), "google")
        self.assertEqual(analytics.classify_sender("news@github.com"), "github.com")
        self.assertEqual(analytics.classify_sender(""), "غير معروف")

    def test_codes_and_dates(self):
        self.assertEqual(analytics.email_code("TEST@EXAMPLE.COM"),
                         analytics.email_code("test@example.com"))
        self.assertEqual(len(analytics.service_code("facebook")), 12)
        self.assertEqual(
            analytics.parse_received_at("2026-11-17T01:20:00Z"),
            datetime(2026, 11, 17, 1, 20, tzinfo=timezone.utc),
        )


@unittest.skipUnless(os.environ.get("DATABASE_URL"), "Database integration requires DATABASE_URL")
class DatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert analytics.initialize(connect)

    def setUp(self):
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE mail_analytics_live, mail_analytics_seen,"
                            " mail_analytics_totals, mail_analytics_daily,"
                            " mail_analytics_scans, mail_analytics_schedule,"
                            " mail_analytics_control RESTART IDENTITY")

    def test_discovery_deduplication_and_deletion(self):
        address = "test@mail.tm"
        initial = [
            {"id": "unique-1", "from": {"address": "noreply@facebookmail.com"},
             "createdAt": "2026-11-17T08:00:00Z", "subject": "Private text"},
            {"id": "unique-2", "from": {"address": "no-reply@github.com"},
             "createdAt": "2026-11-17T08:01:00Z"},
        ]
        self.assertTrue(analytics.save_mailbox_snapshot(connect, 123, address, initial))
        self.assertTrue(analytics.save_mailbox_snapshot(connect, 123, address, initial))
        overview = analytics.read_overview(connect)
        self.assertEqual(overview["lifetime"], 2)
        self.assertEqual(overview["available"], 2)
        self.assertEqual(overview["inboxes"], 1)
        self.assertEqual(overview["members"], 1)
        groups, count = analytics.read_service_email_groups(connect, "facebook", 0)
        self.assertEqual(count, 1)
        self.assertEqual(groups[0]["owner_id"], 123)
        entries, count = analytics.read_service_email_messages(
            connect, "facebook", analytics.email_code(address), 0
        )
        self.assertEqual(count, 1)
        row = analytics.get_live_message(connect, entries[0]["id"])
        self.assertEqual(row["message_id"], "unique-1")

        # Once mail.tm deletes a message, the live metadata disappears,
        # without erasing the historical aggregate.
        self.assertTrue(analytics.save_mailbox_snapshot(connect, 123, address, []))
        overview = analytics.read_overview(connect)
        self.assertEqual(overview["lifetime"], 2)
        self.assertEqual(overview["available"], 0)
        self.assertIsNone(analytics.get_live_message(connect, entries[0]["id"]))
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM mail_analytics_live")
                self.assertEqual(cur.fetchone()[0], 0)
                cur.execute("SELECT COUNT(*) FROM mail_analytics_seen")
                self.assertEqual(cur.fetchone()[0], 2)

        # A duplicate from a subsequent successful scan must not be counted again.
        self.assertTrue(analytics.save_mailbox_snapshot(connect, 123, address, initial))
        self.assertEqual(analytics.read_overview(connect)["lifetime"], 2)
        # A soft-deleted message returned by the API must not stay visible.
        flagged = [dict(initial[0], isDeleted=True), initial[1]]
        self.assertTrue(analytics.save_mailbox_snapshot(connect, 123, address, flagged))
        overview = analytics.read_overview(connect)
        self.assertEqual(overview["lifetime"], 2)
        self.assertEqual(overview["available"], 1)

    def test_deleting_email_removes_only_live_records(self):
        self.assertTrue(analytics.save_mailbox_snapshot(connect, 432,
                         "test2@mail.tm", [{"id": "x", "from": {"address": "hi@discord.com"}}]))
        # Empty member load is ambiguous and must never wipe live data.
        self.assertFalse(analytics.purge_removed_emails(connect, []))
        self.assertEqual(analytics.read_overview(connect)["available"], 1)
        # Deleting this email while another mailbox remains is a valid cleanup.
        self.assertTrue(analytics.purge_removed_emails(connect, ["kept@mail.tm"]))
        overview = analytics.read_overview(connect)
        self.assertEqual(overview["lifetime"], 1)
        self.assertEqual(overview["available"], 0)




class SmartScheduleDatabaseTests(DatabaseTests):
    """Isolated PostgreSQL scheduler tests: no API calls or production writes."""

    def test_initial_scan_only_once_after_redeploy(self):
        address = "firstscan@mail.tm"
        self.assertTrue(analytics.sync_schedule(connect, [(address, 123)]))
        work = analytics.scheduled_work(connect)
        self.assertEqual(work["due_count"], 1)
        self.assertEqual(work["rows"][0]["state"], "new")

        self.assertTrue(analytics.save_mailbox_snapshot(connect, 123, address, []))
        self.assertEqual(analytics.scheduled_work(connect)["due_count"], 0)
        self.assertEqual(analytics.scheduled_work(connect)["categories"]["empty"], 1)
        self.assertTrue(analytics.initialize(connect))
        self.assertTrue(analytics.sync_schedule(connect, [(address, 123)]))
        self.assertEqual(analytics.scheduled_work(connect)["due_count"], 0)

    def test_active_12_hours_and_empty_72_hours(self):
        address = "active@mail.tm"
        self.assertTrue(analytics.sync_schedule(connect, [(address, 12)]))
        self.assertTrue(analytics.save_mailbox_snapshot(connect, 12, address, [
            {"id": "msg-a", "from": {"address": "notify@facebookmail.com"}},
        ]))
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT state, next_check_at - NOW()
                    FROM mail_analytics_schedule WHERE email_address=%s
                """, (address,))
                state, due_in = cur.fetchone()
        self.assertEqual(state, "active")
        self.assertTrue(timedelta(hours=11) < due_in <= timedelta(hours=12))

        self.assertTrue(analytics.save_mailbox_snapshot(connect, 12, address, []))
        self.assertEqual(analytics.read_overview(connect)["available"], 0)
        self.assertEqual(analytics.read_overview(connect)["lifetime"], 1)
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT state, next_check_at - NOW()
                    FROM mail_analytics_schedule WHERE email_address=%s
                """, (address,))
                state, due_in = cur.fetchone()
        self.assertEqual(state, "empty")
        self.assertTrue(timedelta(days=2, hours=23) < due_in <= timedelta(days=3))

    def test_partial_member_inbox_never_erases_other_messages(self):
        address = "member@mail.tm"
        first = {"id": "first", "from": {"address": "hello@facebookmail.com"}}
        second = {"id": "second", "from": {"address": "hello@discord.com"}}
        self.assertTrue(analytics.sync_schedule(connect, [(address, 15)]))
        self.assertTrue(analytics.save_mailbox_snapshot(
            connect, 15, address, [first], complete=False
        ))
        # No successful complete scan yet: keep it due right now.
        self.assertEqual(analytics.scheduled_work(connect)["due_count"], 1)
        self.assertTrue(analytics.save_mailbox_snapshot(connect, 15, address, [first, second]))
        self.assertEqual(analytics.read_overview(connect)["available"], 2)
        self.assertTrue(analytics.save_mailbox_snapshot(
            connect, 15, address, [first], complete=False
        ))
        self.assertEqual(analytics.read_overview(connect)["available"], 2)
        self.assertEqual(analytics.read_overview(connect)["lifetime"], 2)

    def test_token_invalid_is_deferred_and_429_has_backoff(self):
        address = "bad@mail.tm"
        self.assertTrue(analytics.sync_schedule(connect, [(address, 77)]))
        self.assertTrue(analytics.defer_failed_mailbox(connect, 77, address, "token_invalid"))
        self.assertEqual(analytics.scheduled_work(connect)["due_count"], 0)
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT next_check_at - NOW()
                    FROM mail_analytics_schedule WHERE email_address=%s
                """, (address,))
                delay = cur.fetchone()[0]
        self.assertTrue(timedelta(days=2, hours=23) < delay <= timedelta(days=3))
        self.assertTrue(analytics.defer_failed_mailbox(connect, 77, address, "http_429"))
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT failures, next_check_at - NOW()
                    FROM mail_analytics_schedule WHERE email_address=%s
                """, (address,))
                failures, delay = cur.fetchone()
        self.assertEqual(failures, 2)
        self.assertTrue(timedelta(minutes=29) < delay <= timedelta(minutes=30))

    def test_manual_full_scan_and_click_cooldown(self):
        address = "full@mail.tm"
        self.assertTrue(analytics.sync_schedule(connect, [(address, 55)]))
        self.assertTrue(analytics.save_mailbox_snapshot(connect, 55, address, []))
        self.assertEqual(analytics.scheduled_work(connect)["due_count"], 0)
        accepted, detail = analytics.request_full_scan(connect)
        self.assertTrue(accepted, detail)
        self.assertEqual(analytics.scheduled_work(connect)["due_count"], 1)
        accepted, detail = analytics.request_full_scan(connect)
        self.assertFalse(accepted)
        self.assertIn("انتظر", detail)

    def test_deletion_cleanup_retains_historical_total(self):
        address = "delete@mail.tm"
        self.assertTrue(analytics.sync_schedule(connect, [(address, 45)]))
        self.assertTrue(analytics.save_mailbox_snapshot(connect, 45, address, [
            {"id": "once", "from": {"address": "notice@google.com"}}
        ]))
        self.assertTrue(analytics.sync_schedule(connect, [("other@mail.tm", 99)]))
        self.assertEqual(analytics.read_overview(connect)["lifetime"], 1)
        self.assertEqual(analytics.read_overview(connect)["available"], 0)
        self.assertEqual(analytics.scheduled_work(connect)["categories"], {"new": 1})

    def test_migrate_prior_scan_timestamps(self):
        address = "existing@mail.tm"
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO mail_analytics_scans(email_address, scanned_at) "
                    "VALUES (%s, NOW())", (address,)
                )
        self.assertTrue(analytics.initialize(connect))
        self.assertTrue(analytics.sync_schedule(connect, [(address, 5)]))
        self.assertEqual(analytics.scheduled_work(connect)["due_count"], 0)
        self.assertEqual(analytics.scheduled_work(connect)["categories"]["empty"], 1)



class DashboardScannerTests(unittest.TestCase):
    """Run the small scanner function with mocked mail.tm responses only."""

    @staticmethod
    def build_scanner(pages):
        import ast
        from pathlib import Path
        from types import SimpleNamespace

        tree = ast.parse(Path("telegram_bot.py").read_text(encoding="utf-8"))
        definition = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "fetch_complete_mailbox_for_analytics"
        )
        namespace = {
            "get_user_emails": lambda user_id: [{"token": "testing-token"}],
            "refresh_user_email_token_detailed": lambda uid, idx: {
                "token": None, "error": "token_invalid"
            },
        }

        def mail_request(method, endpoint, **kwargs):
            page = int(endpoint.split("page=")[-1])
            response = pages.get(page)
            if response is None:
                return None, "network"
            code, payload = response
            return SimpleNamespace(status_code=code, json=lambda: payload), None

        namespace["mail_request"] = mail_request
        code = compile(
            ast.Module(body=[definition], type_ignores=[]),
            "telegram_bot.py",
            "exec",
        )
        exec(code, namespace)
        return namespace["fetch_complete_mailbox_for_analytics"]

    def test_analytics_scan_empty_mailbox_is_success(self):
        scanner = self.build_scanner({
            1: (200, {"hydra:member": [], "hydra:totalItems": 0})
        })
        self.assertEqual(scanner(1, 0), {"messages": [], "error": None})

    def test_analytics_scan_http_429_is_not_empty_mailbox(self):
        scanner = self.build_scanner({1: (429, {})})
        self.assertEqual(scanner(1, 0)["error"], "http_429")
        self.assertIsNone(scanner(1, 0)["messages"])

    def test_analytics_scan_fetches_all_pages(self):
        first_page = [{"id": str(i)} for i in range(30)]
        scanner = self.build_scanner({
            1: (200, {"hydra:member": first_page, "hydra:totalItems": 31}),
            2: (200, {"hydra:member": [{"id": "30"}], "hydra:totalItems": 31}),
        })
        result = scanner(1, 0)
        self.assertIsNone(result["error"])
        self.assertEqual(len(result["messages"]), 31)

    def test_analytics_scan_refuses_incomplete_mailbox(self):
        scanner = self.build_scanner({
            1: (200, {"hydra:member": [{"id": "1"}], "hydra:totalItems": 10})
        })
        result = scanner(1, 0)
        self.assertEqual(result["error"], "incomplete_mailbox")
        self.assertIsNone(result["messages"])

    def test_old_admin_message_view_stays_and_new_dashboard_does_not_read_bodies(self):
        from pathlib import Path
        source = Path("telegram_bot.py").read_text(encoding="utf-8")
        dashboard = source.split("async def show_mail_analytics(", 1)[1].split(
            "# ================== بيانات المستخدمين ==================", 1
        )[0]
        self.assertNotIn("get_user_message_content", dashboard)
        self.assertNotIn("get_message_text", dashboard)
        self.assertIn("ma_diagnostics", dashboard)
        self.assertIn("🆔 ID:", dashboard)
        self.assertIn("ma_scan_all", dashboard)
        self.assertIn("كل 12 ساعة", dashboard)
        self.assertIn('callback_data=f"member_msg_', source)
        self.assertIn("get_user_message_content, target_id", source)



class ResourceEfficiencyTests(unittest.TestCase):
    """No Railway services or real mail.tm calls; preserve exact user behavior."""

    def test_idle_deadline_never_misses_earlier_scan(self):
        now = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(
            analytics._next_idle_delay(now + timedelta(minutes=3), now), 180
        )
        self.assertEqual(
            analytics._next_idle_delay(now + timedelta(hours=12), now), 600
        )
        self.assertEqual(
            analytics._next_idle_delay(None, now), 600
        )
        self.assertEqual(
            analytics._next_idle_delay(now - timedelta(seconds=3), now), 0.25
        )

    def test_event_wakes_idle_scanner_without_another_worker(self):
        async def scenario():
            event = asyncio.Event()
            task = asyncio.create_task(analytics._idle_wait(event, 600))
            await asyncio.sleep(0)
            event.set()
            await asyncio.wait_for(task, 1)
            self.assertFalse(event.is_set())

        asyncio.run(scenario())

    def test_member_profile_is_not_rewritten_without_change(self):
        tree = ast.parse(Path("telegram_bot.py").read_text(encoding="utf-8"))
        fn = next(node for node in tree.body
                  if isinstance(node, ast.FunctionDef)
                  and node.name == "update_user_info")
        data = {
            "lang": "ar", "first_name": "Samer", "last_name": "",
            "username": "samer", "emails": [
                {"address": "x@example.com", "token": "dont-change"}
            ]
        }
        records = {"7": data}
        saved = []
        scope = {
            "get_user_data": lambda user_id: records[str(user_id)],
            "user_database": records,
            "save_single_user": lambda uid, row: saved.append((uid, row.copy())),
        }
        exec(compile(ast.Module(body=[fn], type_ignores=[]),
                     "telegram_bot.py", "exec"), scope)
        update = scope["update_user_info"]
        user = SimpleNamespace(first_name="Samer", last_name="", username="samer")
        update(7, user)
        self.assertEqual(saved, [])
        user.username = "newname"
        update(7, user)
        self.assertEqual(len(saved), 1)
        self.assertEqual(records["7"]["emails"][0]["address"], "x@example.com")

    def test_telegram_subscription_checks_remain_and_no_job_queue_is_used(self):
        source = Path("telegram_bot.py").read_text(encoding="utf-8")
        self.assertIn("get_chat_member(chat_identifier, user_id)", source)
        self.assertIn("channels=active_channels", source)
        self.assertIn("def get_missing_subscription_channels(", source)
        self.assertIn("notify_schedule_change(owners_changed=True)", source)
        self.assertIn("mail_analytics.notify_schedule_change()", source)
        self.assertIn("get_user_message_content", source)
        self.assertNotIn("run_repeating(", source)
        self.assertNotIn("job_queue.run_", source)

    def test_http_session_is_worker_local(self):
        tree = ast.parse(Path("telegram_bot.py").read_text(encoding="utf-8"))
        fn = next(node for node in tree.body
                  if isinstance(node, ast.FunctionDef)
                  and node.name == "_mail_session")
        import requests
        import threading
        scope = {"_MAIL_HTTP_LOCAL": threading.local(), "requests": requests}
        exec(compile(ast.Module(body=[fn], type_ignores=[]),
                     "telegram_bot.py", "exec"), scope)
        first = scope["_mail_session"]()
        second = scope["_mail_session"]()
        self.assertIs(first, second)
        first.close()
        # Test-local context only: production workers keep their own sessions.

if __name__ == "__main__":
    unittest.main()

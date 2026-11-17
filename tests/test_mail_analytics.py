import os
import unittest
from datetime import datetime, timezone

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
                            " mail_analytics_scans RESTART IDENTITY")

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
        self.assertTrue(analytics.purge_removed_emails(connect, []))
        overview = analytics.read_overview(connect)
        self.assertEqual(overview["lifetime"], 1)
        self.assertEqual(overview["available"], 0)



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
        self.assertIn('callback_data=f"member_msg_', source)
        self.assertIn("get_user_message_content, target_id", source)

if __name__ == "__main__":
    unittest.main()

# Validation-only: identical production code, run full suite on the main snapshot.

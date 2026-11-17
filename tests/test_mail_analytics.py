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


if __name__ == "__main__":
    unittest.main()

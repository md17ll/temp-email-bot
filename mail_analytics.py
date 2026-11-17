"""Private, metadata-only mail.tm analytics for the Telegram bot.

Keep message bodies, subjects, credentials, and one-time codes out of this index.
Only a hash of each previously observed message survives mail.tm deletion.
"""
import asyncio
import hashlib
import re
from collections import Counter
from datetime import datetime, timezone

from psycopg2.extras import RealDictCursor

PAGE_SIZE = 8
POLL_PAUSE_SECONDS = 3
CYCLE_PAUSE_SECONDS = 90

SERVICE_LABELS = {
    "facebook": "📘 فيسبوك",
    "instagram": "📸 إنستغرام",
    "google": "🔵 Google",
    "tiktok": "🎵 TikTok",
    "microsoft": "🪟 Microsoft",
    "telegram": "✈️ Telegram",
    "amazon": "📦 Amazon",
    "apple": "🍎 Apple",
    "discord": "🎮 Discord",
    "x": "𝕏",
    "غير معروف": "📨 غير معروف",
}
SERVICE_DOMAINS = {
    "facebook": ("facebookmail.com", "facebook.com", "fb.com"),
    "instagram": ("instagram.com",),
    "google": ("google.com", "googlemail.com"),
    "tiktok": ("tiktok.com",),
    "microsoft": ("microsoft.com", "accountprotection.microsoft.com", "outlook.com"),
    "telegram": ("telegram.org",),
    "amazon": ("amazon.com",),
    "apple": ("apple.com",),
    "discord": ("discord.com", "discordapp.com"),
    "x": ("x.com", "twitter.com"),
}


def _digest(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def service_code(value):
    return _digest(value)[:12]


def email_code(value):
    return _digest(str(value).strip().lower())[:16]


def display_service(value):
    return SERVICE_LABELS.get(value, "✉️ " + str(value))


def classify_sender(address):
    """Identify known official sender domains, otherwise group by sender domain."""
    sender = str(address or "").strip().lower()
    host = sender.rsplit("@", 1)[-1] if "@" in sender else ""
    host = host.strip(".")
    if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", host):
        return "غير معروف"
    for name, domains in SERVICE_DOMAINS.items():
        if any(host == domain or host.endswith("." + domain) for domain in domains):
            return name
    return host[:120]


def parse_received_at(raw):
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(raw or "").replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


def initialize(connect):
    """Create only new analytics tables; do not alter existing bot tables."""
    conn = connect()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS mail_analytics_live (
                    id BIGSERIAL PRIMARY KEY,
                    email_address TEXT NOT NULL,
                    email_code CHAR(16) NOT NULL,
                    owner_id BIGINT NOT NULL,
                    message_id TEXT NOT NULL,
                    service TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    received_at TIMESTAMPTZ NOT NULL,
                    UNIQUE (email_address, message_id)
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS mail_analytics_live_service_idx
                ON mail_analytics_live (service, email_code, received_at DESC)
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS mail_analytics_seen (
                    fingerprint CHAR(64) PRIMARY KEY
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS mail_analytics_totals (
                    service TEXT PRIMARY KEY,
                    received_total BIGINT NOT NULL DEFAULT 0
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS mail_analytics_daily (
                    event_date DATE NOT NULL,
                    service TEXT NOT NULL,
                    received_total BIGINT NOT NULL DEFAULT 0,
                    PRIMARY KEY (event_date, service)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS mail_analytics_scans (
                    email_address TEXT PRIMARY KEY,
                    scanned_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
        conn.commit()
        return True
    except Exception as error:
        conn.rollback()
        print(f"⚠️ تعذر تجهيز إحصائيات البريد: {error}")
        return False
    finally:
        conn.close()


def save_mailbox_snapshot(connect, owner_id, address, messages):
    """Commit a complete, successful mail.tm snapshot as a single transaction.

    Deleted messages lose all metadata here; cumulative counters and opaque
    fingerprints remain to avoid counting a previously seen message twice.
    """
    address = str(address or "").strip().lower()
    if not address or not isinstance(messages, list):
        return False
    conn = connect()
    if not conn:
        return False
    try:
        new_counts = Counter()
        new_daily = Counter()
        visible_ids = []
        unique_messages = set()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT message_id FROM mail_analytics_live "
                "WHERE email_address=%s AND owner_id=%s",
                (address, int(owner_id)),
            )
            already_live = {row[0] for row in cur.fetchall()}
            for item in messages:
                if not isinstance(item, dict) or item.get("isDeleted") is True:
                    continue
                message_id = str(item.get("id") or "").strip()
                if not message_id or message_id in unique_messages:
                    continue
                unique_messages.add(message_id)
                visible_ids.append(message_id)
                # Unchanged messages need no writes and no further mail.tm requests.
                if message_id in already_live:
                    continue
                sender_data = item.get("from") or {}
                sender = str(sender_data.get("address") or "") if isinstance(sender_data, dict) else ""
                sender = sender[:320]
                service = classify_sender(sender)
                received = parse_received_at(item.get("createdAt"))
                fingerprint = _digest(address + "\0" + message_id)
                cur.execute(
                    "INSERT INTO mail_analytics_seen (fingerprint) VALUES (%s) "
                    "ON CONFLICT DO NOTHING RETURNING fingerprint",
                    (fingerprint,),
                )
                if cur.fetchone():
                    new_counts[service] += 1
                    new_daily[(received.date(), service)] += 1

                cur.execute("""
                    INSERT INTO mail_analytics_live
                        (email_address, email_code, owner_id, message_id, service, sender, received_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (email_address, message_id) DO UPDATE SET
                        owner_id = EXCLUDED.owner_id,
                        service = EXCLUDED.service,
                        sender = EXCLUDED.sender,
                        received_at = EXCLUDED.received_at
                """, (address, email_code(address), int(owner_id), message_id,
                      service, sender, received))
            cur.execute("""
                DELETE FROM mail_analytics_live
                WHERE email_address = %s AND NOT (message_id = ANY(%s))
            """, (address, visible_ids))
            for service, count in new_counts.items():
                cur.execute("""
                    INSERT INTO mail_analytics_totals (service, received_total)
                    VALUES (%s, %s)
                    ON CONFLICT (service) DO UPDATE SET
                        received_total = mail_analytics_totals.received_total + EXCLUDED.received_total
                """, (service, count))
            for (day, service), count in new_daily.items():
                cur.execute("""
                    INSERT INTO mail_analytics_daily (event_date, service, received_total)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (event_date, service) DO UPDATE SET
                        received_total = mail_analytics_daily.received_total + EXCLUDED.received_total
                """, (day, service, count))
            cur.execute("""
                INSERT INTO mail_analytics_scans (email_address, scanned_at)
                VALUES (%s, NOW())
                ON CONFLICT(email_address) DO UPDATE SET scanned_at = NOW()
            """, (address,))
        conn.commit()
        return True
    except Exception as error:
        conn.rollback()
        print(f"⚠️ تعذر مزامنة فهرس البريد {address}: {error}")
        return False
    finally:
        conn.close()


def purge_removed_emails(connect, addresses):
    """Remove live metadata for emails no longer owned by bot users."""
    active = sorted({str(address).lower() for address in addresses if address})
    conn = connect()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM mail_analytics_live WHERE NOT (email_address = ANY(%s))",
                (active,),
            )
            cur.execute(
                "DELETE FROM mail_analytics_scans WHERE NOT (email_address = ANY(%s))",
                (active,),
            )
        conn.commit()
        return True
    except Exception as error:
        conn.rollback()
        print(f"⚠️ تعذر تنظيف بيانات البريد المحذوف: {error}")
        return False
    finally:
        conn.close()


def read_overview(connect):
    conn = connect()
    if not conn:
        return None
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT (SELECT COALESCE(SUM(received_total), 0) FROM mail_analytics_totals) AS lifetime,
                       (SELECT COUNT(*) FROM mail_analytics_live) AS available,
                       (SELECT COUNT(DISTINCT email_address) FROM mail_analytics_live) AS inboxes,
                       (SELECT COUNT(DISTINCT owner_id) FROM mail_analytics_live) AS members,
                       (SELECT COALESCE(SUM(received_total), 0)
                        FROM mail_analytics_daily
                        WHERE event_date = CURRENT_DATE) AS today,
                       (SELECT MAX(scanned_at) FROM mail_analytics_scans) AS last_scan
            """)
            result = dict(cur.fetchone())
            cur.execute("SELECT service, received_total FROM mail_analytics_totals")
            totals = {x["service"]: int(x["received_total"]) for x in cur.fetchall()}
            cur.execute("""
                SELECT service, COUNT(*) AS present
                FROM mail_analytics_live GROUP BY service
            """)
            available = {x["service"]: int(x["present"]) for x in cur.fetchall()}
        result["services"] = [
            {"service": key, "code": service_code(key),
             "label": display_service(key), "available": available.get(key, 0),
             "total": totals.get(key, 0)}
            for key in sorted(set(totals) | set(available),
                              key=lambda name: (-available.get(name, 0), name))
        ]
        return result
    except Exception as error:
        print(f"⚠️ تعذر قراءة إحصائيات البريد: {error}")
        return None
    finally:
        conn.close()


def read_service_email_groups(connect, service, page):
    conn = connect()
    if not conn:
        return [], 0
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT email_address, email_code, owner_id, COUNT(*) AS quantity
                FROM mail_analytics_live WHERE service = %s
                GROUP BY email_address, email_code, owner_id
                ORDER BY quantity DESC, email_address
            """, (service,))
            result = [dict(x) for x in cur.fetchall()]
        page = max(0, int(page))
        return result[page * PAGE_SIZE:(page + 1) * PAGE_SIZE], len(result)
    except Exception as error:
        print(f"⚠️ تعذر قراءة إيميلات الخدمة: {error}")
        return [], 0
    finally:
        conn.close()


def read_service_email_messages(connect, service, code, page):
    conn = connect()
    if not conn:
        return [], 0
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, email_address, owner_id, sender, received_at
                FROM mail_analytics_live
                WHERE service = %s AND email_code = %s
                ORDER BY received_at DESC, id DESC
            """, (service, code))
            result = [dict(x) for x in cur.fetchall()]
        page = max(0, int(page))
        return result[page * PAGE_SIZE:(page + 1) * PAGE_SIZE], len(result)
    except Exception as error:
        print(f"⚠️ تعذر قراءة رسائل الخدمة: {error}")
        return [], 0
    finally:
        conn.close()


def get_live_message(connect, row_id):
    conn = connect()
    if not conn:
        return None
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, email_address, email_code, owner_id, message_id, service
                FROM mail_analytics_live WHERE id=%s
            """, (int(row_id),))
            result = cur.fetchone()
            return dict(result) if result else None
    except Exception as error:
        print(f"⚠️ تعذر قراءة الرسالة: {error}")
        return None
    finally:
        conn.close()


async def monitor_mailboxes(connect, user_database, fetch_all):
    """Visit saved mailboxes gradually; never turn mailbox errors into deletions."""
    await asyncio.sleep(10)
    while True:
        try:
            mailboxes = []
            for uid, member in list(user_database.items()):
                for index, record in enumerate(list(member.get("emails") or [])):
                    address = str(record.get("address") or "").strip().lower()
                    if address:
                        mailboxes.append((int(uid), index, address))
            await asyncio.to_thread(
                purge_removed_emails, connect, [box[2] for box in mailboxes]
            )
            for uid, index, address in mailboxes:
                # The owner may have deleted/reordered this email since the snapshot.
                latest = user_database.get(str(uid), {}).get("emails") or []
                if index >= len(latest) or str(latest[index].get("address", "")).lower() != address:
                    continue
                try:
                    messages = await asyncio.to_thread(fetch_all, uid, index)
                    if messages is not None:
                        await asyncio.to_thread(
                            save_mailbox_snapshot, connect, uid, address, messages
                        )
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    print(f"⚠️ خطأ عند فحص البريد {address}: {error}")
                await asyncio.sleep(POLL_PAUSE_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            print(f"⚠️ دورة فحص البريد لم تكتمل: {error}")
        await asyncio.sleep(CYCLE_PAUSE_SECONDS)

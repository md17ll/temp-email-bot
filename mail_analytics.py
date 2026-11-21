"""Private, metadata-only mail.tm analytics for the Telegram bot.

Keep message bodies, subjects, credentials, and one-time codes out of this index.
Only a hash of each previously observed message survives mail.tm deletion.
"""
import asyncio
import hashlib
import re
import threading
import time
from collections import Counter
from datetime import datetime, timezone

from psycopg2.extras import RealDictCursor, execute_values

PAGE_SIZE = 8
POLL_PAUSE_SECONDS = 3
ACTIVE_HOURS = 12
DORMANT_DAYS = 3
# Maximum idle reconciliation; exact due dates can wake the worker sooner.
SCHEDULER_IDLE_RECHECK_SECONDS = 600
SCHEDULER_ERROR_RETRY_SECONDS = 30
_WAKE_LOCK = threading.Lock()
_WAKE_LOOP = None
_WAKE_EVENT = None
_OWNER_REVISION = 0


def notify_schedule_change(owners_changed=False):
    """Wake the one existing scanner without starting another task."""
    global _OWNER_REVISION
    with _WAKE_LOCK:
        if owners_changed:
            _OWNER_REVISION += 1
        loop, event = _WAKE_LOOP, _WAKE_EVENT
    if loop is not None and event is not None and not loop.is_closed():
        try:
            loop.call_soon_threadsafe(event.set)
        except RuntimeError:
            pass


async def _idle_wait(event, delay):
    try:
        await asyncio.wait_for(event.wait(), timeout=max(0.25, float(delay)))
    except asyncio.TimeoutError:
        pass
    finally:
        event.clear()


def _next_idle_delay(next_check, now=None):
    """Respect the next PostgreSQL deadline without polling every 30 seconds."""
    now = now or datetime.now(timezone.utc)
    if next_check is None:
        return SCHEDULER_IDLE_RECHECK_SECONDS
    if next_check.tzinfo is None:
        next_check = next_check.replace(tzinfo=timezone.utc)
    return max(0.25, min(
        SCHEDULER_IDLE_RECHECK_SECONDS,
        (next_check - now).total_seconds(),
    ))

DUE_BATCH_SIZE = 50
GLOBAL_429_PAUSE_SECONDS = 120
MANUAL_SCAN_COOLDOWN_MINUTES = 20

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
            cur.execute("""
                CREATE TABLE IF NOT EXISTS mail_analytics_schedule (
                    email_address TEXT PRIMARY KEY,
                    owner_id BIGINT NOT NULL DEFAULT 0,
                    state TEXT NOT NULL DEFAULT 'new',
                    last_checked_at TIMESTAMPTZ,
                    next_check_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_error TEXT,
                    failures INTEGER NOT NULL DEFAULT 0
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS mail_analytics_schedule_due_idx
                ON mail_analytics_schedule(next_check_at, state)
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS mail_analytics_control (
                    name TEXT PRIMARY KEY,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            # Reuse successful scans from before the scheduler existed. A
            # redeploy must not recheck thousands of already scanned boxes.
            cur.execute("""
                INSERT INTO mail_analytics_schedule (
                    email_address, owner_id, state, last_checked_at, next_check_at
                )
                SELECT scanned.email_address, 0,
                       CASE WHEN EXISTS (
                           SELECT 1 FROM mail_analytics_live live
                           WHERE live.email_address = scanned.email_address
                       ) THEN 'active' ELSE 'empty' END,
                       scanned.scanned_at,
                       scanned.scanned_at + CASE WHEN EXISTS (
                           SELECT 1 FROM mail_analytics_live live
                           WHERE live.email_address = scanned.email_address
                       ) THEN INTERVAL '12 hours' ELSE INTERVAL '3 days' END
                FROM mail_analytics_scans scanned
                ON CONFLICT (email_address) DO NOTHING
            """)
        conn.commit()
        return True
    except Exception as error:
        conn.rollback()
        print(f"⚠️ تعذر تجهيز إحصائيات البريد: {error}")
        return False
    finally:
        conn.close()


def save_mailbox_snapshot(connect, owner_id, address, messages, complete=True):
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
            if messages:
                cur.execute(
                    "SELECT message_id FROM mail_analytics_live "
                    "WHERE email_address=%s AND owner_id=%s",
                    (address, int(owner_id)),
                )
                already_live = {row[0] for row in cur.fetchall()}
            else:
                # Empty boxes are the overwhelming majority. No SELECT is needed.
                already_live = set()
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
            if complete:
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
            if complete:
                cur.execute("""
                    INSERT INTO mail_analytics_scans (email_address, scanned_at)
                    VALUES (%s, NOW())
                    ON CONFLICT(email_address) DO UPDATE SET scanned_at = NOW()
                """, (address,))
            if complete:
                # The transaction has already reconciled exactly these IDs.
                has_messages = bool(visible_ids)
                has_complete_scan = True
            else:
                # A partial manual inbox view may show just its first page.
                # Never delete other messages or mark a first scan complete.
                cur.execute("""
                    SELECT EXISTS(
                        SELECT 1 FROM mail_analytics_live WHERE email_address=%s
                    ), EXISTS(
                        SELECT 1 FROM mail_analytics_scans WHERE email_address=%s
                    )
                """, (address, address))
                has_messages, has_complete_scan = map(bool, cur.fetchone())
            interval = (
                "0 minutes" if not has_complete_scan else
                "12 hours" if has_messages else "3 days"
            )
            state = "active" if has_messages else "empty"
            cur.execute("""
                INSERT INTO mail_analytics_schedule
                    (email_address, owner_id, state, last_checked_at,
                     next_check_at, last_error, failures)
                VALUES (%s, %s, %s, NOW(), NOW() + %s::interval, NULL, 0)
                ON CONFLICT(email_address) DO UPDATE SET
                    owner_id=EXCLUDED.owner_id,
                    state=EXCLUDED.state,
                    last_checked_at=EXCLUDED.last_checked_at,
                    next_check_at=EXCLUDED.next_check_at,
                    last_error=NULL,
                    failures=0
            """, (address, int(owner_id), state, interval))
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
    if not active:
        return False  # Never mass-delete on a failed/empty user-data load.
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
            cur.execute(
                "DELETE FROM mail_analytics_schedule WHERE NOT (email_address = ANY(%s))",
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




def sync_schedule(connect, mailbox_owners):
    """Add newly created mailboxes without resetting any existing due dates.

    Deleting a member email also removes live metadata and its scheduled scan;
    aggregate historical totals remain untouched.
    """
    pairs = sorted({
        (str(address).strip().lower(), int(owner_id))
        for address, owner_id in mailbox_owners if address
    })
    if not pairs:
        return False  # Fail safe if the member database could not be read.
    addresses = [address for address, _ in pairs]
    conn = connect()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO mail_analytics_schedule(email_address, owner_id)
                VALUES %s
                ON CONFLICT(email_address) DO UPDATE
                    SET owner_id=EXCLUDED.owner_id
                WHERE mail_analytics_schedule.owner_id IS DISTINCT FROM EXCLUDED.owner_id
            """, pairs, page_size=300)
            cur.execute(
                "DELETE FROM mail_analytics_live WHERE NOT (email_address = ANY(%s))",
                (addresses,),
            )
            cur.execute(
                "DELETE FROM mail_analytics_scans WHERE NOT (email_address = ANY(%s))",
                (addresses,),
            )
            cur.execute(
                "DELETE FROM mail_analytics_schedule WHERE NOT (email_address = ANY(%s))",
                (addresses,),
            )
        conn.commit()
        return True
    except Exception as error:
        conn.rollback()
        print(f"⚠️ تعذر تحديث جدول المراقبة: {error}")
        return False
    finally:
        conn.close()


def scheduled_work(connect):
    """Fetch a small due batch and category counts; one connection per wake."""
    conn = connect()
    if not conn:
        return None
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT state, COUNT(*) AS total
                FROM mail_analytics_schedule GROUP BY state
            """)
            categories = {row["state"]: int(row["total"]) for row in cur.fetchall()}
            cur.execute("""
                SELECT COUNT(*) AS total FROM mail_analytics_schedule
                WHERE next_check_at <= NOW()
            """)
            due_count = int(cur.fetchone()["total"])
            cur.execute("""
                SELECT email_address, owner_id, state FROM mail_analytics_schedule
                WHERE next_check_at <= NOW()
                ORDER BY CASE WHEN state = 'new' THEN 0
                              WHEN state = 'active' THEN 1 ELSE 2 END,
                         next_check_at, email_address
                LIMIT %s
            """, (DUE_BATCH_SIZE,))
            rows = [dict(row) for row in cur.fetchall()]
            cur.execute("""
                SELECT MIN(next_check_at) AS next_check FROM mail_analytics_schedule
                WHERE next_check_at > NOW()
            """)
            next_check = cur.fetchone()["next_check"]
        return {
            "rows": rows, "due_count": due_count, "categories": categories,
            "next_check": next_check,
        }
    except Exception as error:
        print(f"⚠️ تعذر قراءة جدول المراقبة: {error}")
        return None
    finally:
        conn.close()


def defer_failed_mailbox(connect, owner_id, address, error_code):
    """Persist failure backoff; never delete snapshots on a mail.tm failure."""
    address = str(address or "").strip().lower()
    error_code = str(error_code or "unknown")[:80]
    if not address:
        return False
    conn = connect()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT failures FROM mail_analytics_schedule "
                "WHERE email_address=%s FOR UPDATE", (address,)
            )
            prior = cur.fetchone()
            failures = min(30, (int(prior[0]) if prior else 0) + 1)
            if error_code in ("http_429", "rate_limited"):
                minutes = min(360, 15 * (2 ** min(failures - 1, 5)))
            elif error_code in ("token_invalid", "email_missing"):
                minutes = DORMANT_DAYS * 24 * 60
            else:
                minutes = 12 * 60
            cur.execute("""
                INSERT INTO mail_analytics_schedule
                    (email_address, owner_id, state, next_check_at, last_error, failures)
                VALUES (%s, %s, 'error', NOW() + (%s * INTERVAL '1 minute'), %s, %s)
                ON CONFLICT(email_address) DO UPDATE SET
                    owner_id=EXCLUDED.owner_id,
                    state='error',
                    next_check_at=EXCLUDED.next_check_at,
                    last_error=EXCLUDED.last_error,
                    failures=EXCLUDED.failures
            """, (address, int(owner_id), minutes, error_code, failures))
        conn.commit()
        return True
    except Exception as error:
        conn.rollback()
        print(f"⚠️ تعذر حفظ تأجيل فحص البريد: {error}")
        return False
    finally:
        conn.close()


def request_full_scan(connect):
    """Schedule a full scan; reject rapid repeat clicks even after a redeploy."""
    conn = connect()
    if not conn:
        return False, "تعذر الاتصال بقاعدة البيانات"
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(81722, 2026)")
            cur.execute(
                "SELECT updated_at FROM mail_analytics_control "
                "WHERE name='manual_full_scan'"
            )
            row = cur.fetchone()
            now = datetime.now(timezone.utc)
            if row and (now - row[0]).total_seconds() < MANUAL_SCAN_COOLDOWN_MINUTES * 60:
                conn.rollback()
                return False, f"تم طلب فحص شامل مؤخراً؛ انتظر {MANUAL_SCAN_COOLDOWN_MINUTES} دقيقة"
            cur.execute("UPDATE mail_analytics_schedule SET next_check_at=NOW()")
            scheduled = int(cur.rowcount)
            if scheduled < 1:
                conn.rollback()
                return False, "لا توجد إيميلات محفوظة للفحص"
            cur.execute("""
                INSERT INTO mail_analytics_control(name, updated_at)
                VALUES ('manual_full_scan', NOW())
                ON CONFLICT(name) DO UPDATE SET updated_at=NOW()
            """)
        conn.commit()
        return True, f"✅ تم جدولة فحص {scheduled} إيميل. سيبدأ تدريجياً."
    except Exception as error:
        conn.rollback()
        print(f"⚠️ تعذر طلب الفحص الشامل: {error}")
        return False, "تعذر جدولة الفحص الشامل"
    finally:
        conn.close()

async def monitor_mailboxes(connect, user_database, fetch_all, status=None):
    """One persistent scanner: wake on changes, due dates, or periodic fallback.

    Active boxes still scan every 12h, empty boxes every 72h. No parallel tasks.
    """
    global _WAKE_LOOP, _WAKE_EVENT
    status = status if status is not None else {}
    wake = asyncio.Event()
    with _WAKE_LOCK:
        _WAKE_LOOP = asyncio.get_running_loop()
        _WAKE_EVENT = wake
    known_owners = None
    mailbox_map = {}
    pairs = set()
    last_owner_check = 0.0
    last_owner_revision = -1
    wave_active = False
    await asyncio.sleep(10)
    while True:
        try:
            with _WAKE_LOCK:
                revision = _OWNER_REVISION
            if (
                known_owners is None
                or revision != last_owner_revision
                or time.monotonic() - last_owner_check >= SCHEDULER_IDLE_RECHECK_SECONDS
            ):
                # Rebuilding thousands of email dictionaries only when necessary
                # avoids thousands of needless iterations per day.
                mailbox_map = {}
                for uid, member in list(user_database.items()):
                    for index, record in enumerate(list(member.get("emails") or [])):
                        address = str(record.get("address") or "").strip().lower()
                        if address:
                            mailbox_map[address] = (int(uid), index)
                pairs = {(address, uid) for address, (uid, _) in mailbox_map.items()}
                if pairs and pairs != known_owners:
                    synced = await asyncio.to_thread(sync_schedule, connect, list(pairs))
                    if not synced:
                        status["state"] = "تعذر مزامنة جدول الفحص"
                        await asyncio.sleep(SCHEDULER_ERROR_RETRY_SECONDS)
                        continue
                    known_owners = pairs
                last_owner_revision = revision
                last_owner_check = time.monotonic()

            if not pairs:
                # Never spin on due rows from mailboxes that no longer exist.
                # Keep stored rows intact if loading member data temporarily failed.
                status["state"] = "بانتظار موعد الفحص"
                status["pending_due"] = 0
                await _idle_wait(wake, SCHEDULER_IDLE_RECHECK_SECONDS)
                continue

            work = await asyncio.to_thread(scheduled_work, connect)
            if work is None:
                status["state"] = "تعذر قراءة مواعيد الفحص"
                await asyncio.sleep(SCHEDULER_ERROR_RETRY_SECONDS)
                continue

            status["categories"] = work["categories"]
            status["next_check"] = work["next_check"]
            due = work["due_count"]

            if not due:
                if wave_active:
                    status["state"] = "اكتملت الدورة"
                    status["last_completed"] = datetime.now(timezone.utc).strftime(
                        "%Y-%m-%d %H:%M UTC"
                    )
                    wave_active = False
                    status["wave_active"] = False
                else:
                    status["state"] = "بانتظار موعد الفحص"
                status["pending_due"] = 0
                await _idle_wait(wake, _next_idle_delay(work["next_check"]))
                continue

            if not wave_active:
                wave_active = True
                status.update({
                    "state": "جاري الفحص الذكي",
                    "wave_active": True,
                    "cycle": int(status.get("cycle", 0)) + 1,
                    "total": due,
                    "processed": 0,
                    "ok": 0,
                    "failed": 0,
                    "empty": 0,
                    "with_messages": 0,
                    "messages_found": 0,
                    "errors": {},
                    "examples": [],
                })
            status["state"] = "جاري الفحص الذكي"
            status["total"] = max(
                int(status.get("total") or 0),
                int(status.get("processed") or 0) + due,
            )
            status["pending_due"] = due
            for row in work["rows"]:
                address = row["email_address"]
                owner_id = int(row["owner_id"])
                owner_and_index = mailbox_map.get(address)
                if not owner_and_index or owner_and_index[0] != owner_id:
                    # A recently deleted email must not trigger a network call.
                    await asyncio.to_thread(sync_schedule, connect, list(pairs))
                    status["processed"] += 1
                    continue
                uid, index = owner_and_index
                latest = user_database.get(str(uid), {}).get("emails") or []
                if index >= len(latest) or str(latest[index].get("address") or "").lower() != address:
                    # The member changed mailbox order while a batch was running.
                    notify_schedule_change(owners_changed=True)
                    status["processed"] += 1
                    continue
                try:
                    result = await asyncio.to_thread(fetch_all, uid, index)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    print(f"⚠️ خطأ فحص البريد للعضو {uid}: {type(error).__name__}")
                    result = {"messages": None, "error": "unexpected_error"}

                if isinstance(result, dict):
                    messages = result.get("messages")
                    error_code = str(result.get("error") or "mailbox_error")
                else:
                    messages = result
                    error_code = "mailbox_error"

                if isinstance(messages, list):
                    saved = await asyncio.to_thread(
                        save_mailbox_snapshot, connect, uid, address, messages
                    )
                    if saved:
                        status["ok"] += 1
                        status["messages_found"] += len(messages)
                        if messages:
                            status["with_messages"] += 1
                        else:
                            status["empty"] += 1
                    else:
                        messages = None
                        error_code = "database_error"

                if not isinstance(messages, list):
                    await asyncio.to_thread(
                        defer_failed_mailbox, connect, uid, address, error_code
                    )
                    status["failed"] += 1
                    errors = status["errors"]
                    errors[error_code] = int(errors.get(error_code, 0)) + 1
                    if len(status["examples"]) < 8:
                        status["examples"].append({
                            "owner_id": uid, "email": address,
                            "error": error_code,
                        })
                status["processed"] += 1
                await asyncio.sleep(POLL_PAUSE_SECONDS)
                if error_code in ("http_429", "rate_limited"):
                    # Cooling down the entire scanner prevents cascading 429s.
                    status["state"] = "تهدئة ضغط mail.tm"
                    await asyncio.sleep(GLOBAL_429_PAUSE_SECONDS)
                    status["state"] = "جاري الفحص الذكي"
        except asyncio.CancelledError:
            raise
        except Exception as error:
            status["state"] = "تعثر الفحص"
            print(f"⚠️ دورة الفحص الذكي: {type(error).__name__}: {error}")
            await asyncio.sleep(SCHEDULER_ERROR_RETRY_SECONDS)

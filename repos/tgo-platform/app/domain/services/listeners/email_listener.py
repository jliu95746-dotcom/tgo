from __future__ import annotations

import asyncio
import time
import uuid
import imaplib
import re
import html as html_module
from contextlib import suppress
from dataclasses import dataclass
from typing import Dict, Optional
from datetime import datetime, timedelta, timezone

from email import message_from_bytes, policy
from email.message import Message
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.exc import IntegrityError

from app.db.models import Platform, EmailInbox
from app.domain.entities import NormalizedMessage
from app.domain.ports import MessageNormalizer, TgoApiClient, SSEManager
from app.domain.services.dispatcher import process_message
from app.domain.services.inbox_reliability import (
    claim_inbox_batch,
    finalize_inbox_failure,
    finalize_inbox_success,
)
from app.core.config import settings
from app.infra.visitor_client import VisitorService


class EmailPlatformConfig(BaseModel):
    # Inbound (IMAP)
    imap_host: str
    imap_port: int = 993
    imap_username: str
    imap_password: str
    imap_use_ssl: bool = True
    mailbox: str = "INBOX"

    # Polling / Fetching
    poll_interval_seconds: int = 60
    max_emails_per_poll: int = 10
    fetch_lookback_days: int = 1

    # Processing
    processing_batch_size: int = 10
    max_retry_attempts: int = 3


@dataclass
class _PlatformEntry:
    id: uuid.UUID
    project_id: uuid.UUID
    api_key: str | None
    cfg: EmailPlatformConfig


class TTLIdempotencyStore:
    """Simple in-memory TTL store for idempotency keyed by (platform_id, message_id)."""

    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._ttl = ttl_seconds
        self._store: Dict[tuple[str, str], float] = {}
        self._lock = asyncio.Lock()

    async def seen(self, platform_id: uuid.UUID, message_id: str) -> bool:
        key = (str(platform_id), message_id)
        now = time.time()
        async with self._lock:
            # Cleanup on the fly
            to_del = [k for k, exp in self._store.items() if exp <= now]
            for k in to_del:
                self._store.pop(k, None)
            return key in self._store

    async def mark(self, platform_id: uuid.UUID, message_id: str) -> None:
        key = (str(platform_id), message_id)
        exp = time.time() + self._ttl
        async with self._lock:
            self._store[key] = exp


class EmailChannelListener:
    """
    Multi-tenant Email (IMAP) listener that:
    - Periodically queries DB for all active email platforms (type="email")
    - Spawns one concurrent polling task per platform
    - Polls accounts concurrently and processes messages in parallel
    - Dynamically reloads configuration to add/remove polling tasks without restart

    Note: Handles inbound email via IMAP polling. SMTP sending uses per-platform configuration
    from Platform.config and is handled by the dispatcher/EmailAdapter.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        normalizer: MessageNormalizer,
        tgo_api_client: TgoApiClient,
        sse_manager: SSEManager,
        refresh_interval_seconds: int = 300,
        idempotency_ttl_seconds: int = 3600,
    ) -> None:
        self._session_factory = session_factory
        self._normalizer = normalizer
        self._tgo_api_client = tgo_api_client
        self._sse_manager = sse_manager
        self._refresh_interval = refresh_interval_seconds
        self._stop_event = asyncio.Event()
        self._tasks: dict[uuid.UUID, asyncio.Task] = {}
        self._idempotency = TTLIdempotencyStore(ttl_seconds=idempotency_ttl_seconds)
        # Persistent IMAP connections per platform id
        self._imap_conns: dict[uuid.UUID, imaplib.IMAP4] = {}
        # Background tasks
        self._supervisor_task: asyncio.Task | None = None
        self._consumer_task: asyncio.Task | None = None
        # Visitor registration service (with Redis caching if available)
        self._visitor_service = VisitorService(
            base_url=settings.api_base_url,
            redis_url=settings.redis_url,
            cache_ttl_seconds=settings.visitor_cache_ttl_seconds,
        )

    async def start(self) -> None:
        """Start producer (IMAP fetch) supervisor and consumer processing loop."""
        # Launch both loops as background tasks; this method runs until stop is requested
        self._supervisor_task = asyncio.create_task(self._supervisor_loop(), name="email-supervisor-loop")
        self._consumer_task = asyncio.create_task(self._consumer_loop(), name="email-consumer-loop")
        try:
            await asyncio.gather(self._supervisor_task, self._consumer_task)
        except asyncio.CancelledError:
            pass

    async def _supervisor_loop(self) -> None:
        """Run the producer supervisor loop until stopped."""
        while not self._stop_event.is_set():
            try:
                platforms = await self._load_active_email_platforms()
                await self._reconcile_platform_tasks(platforms)
            except Exception as e:  # pragma: no cover - protective catch
                print(f"[EMAIL] Supervisor error: {e}")
            # Wait for refresh or stop earlier
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._refresh_interval)
            except asyncio.TimeoutError:
                pass

        # On stop: cancel all pollers
        await self._cancel_all_platform_tasks()

    async def stop(self) -> None:
        self._stop_event.set()

    async def _load_active_email_platforms(self) -> list[_PlatformEntry]:
        async with self._session_factory() as session:
            stmt = (
                select(Platform.id, Platform.project_id, Platform.api_key, Platform.config)
                .where(Platform.is_active.is_(True), Platform.type == "email")
            )
            rows = (await session.execute(stmt)).all()
        platforms: list[_PlatformEntry] = []
        for pid, project_id, api_key, cfg_dict in rows:
            try:
                cfg = EmailPlatformConfig(**(cfg_dict or {}))
                platforms.append(_PlatformEntry(id=pid, project_id=project_id, api_key=api_key, cfg=cfg))
            except Exception as e:
                print(f"[EMAIL] Skip platform {pid}: invalid config: {e}")
        return platforms

    async def _reconcile_platform_tasks(self, platforms: list[_PlatformEntry]) -> None:
        current_ids = set(self._tasks.keys())
        new_ids = {p.id for p in platforms}

        # Start new tasks
        to_start = [p for p in platforms if p.id not in current_ids]
        for p in to_start:
            task = asyncio.create_task(self._poll_platform(p), name=f"email-poller-{p.id}")
            self._tasks[p.id] = task
            print(f"[EMAIL] Started poller for platform {p.id}")

        # Cancel removed tasks
        to_cancel = current_ids - new_ids
        for pid in to_cancel:
            task = self._tasks.pop(pid, None)
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                print(f"[EMAIL] Stopped poller for platform {pid}")

        # Update existing tasks' config if needed (future optimization)
        # For now, pollers re-read their config on each iteration if desired.

    async def _cancel_all_platform_tasks(self) -> None:
        ids = list(self._tasks.keys())
        for pid in ids:
            task = self._tasks.pop(pid)
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def _get_imap_conn(self, p: _PlatformEntry):
        conn = self._imap_conns.get(p.id)
        try:
            if conn is not None:
                try:
                    typ, _ = await asyncio.to_thread(conn.noop)
                    if typ != 'OK':
                        raise Exception('NOOP failed')
                except Exception:
                    await self._close_imap_conn(p.id)
                    conn = None
            if conn is None:
                conn = await self._connect_imap(p.cfg)
                self._imap_conns[p.id] = conn
            return conn
        except Exception as e:
            print(f"[EMAIL] IMAP connect error for {p.id}: {e}")
            return None

    async def _connect_imap(self, cfg: EmailPlatformConfig):
        conn = imaplib.IMAP4_SSL(cfg.imap_host, cfg.imap_port) if cfg.imap_use_ssl else imaplib.IMAP4(cfg.imap_host, cfg.imap_port)
        # Login
        print(f"[EMAIL] Connecting to IMAP server", cfg)
        await asyncio.to_thread(conn.login, cfg.imap_username, cfg.imap_password)

        # Send IMAP ID command for 163.com and other Chinese email providers
        # This is required to avoid "Unsafe Login" errors
        # RFC 2971 - IMAP4 ID extension
        try:
            # Register ID command as an AUTH-phase command
            imaplib.Commands["ID"] = ('AUTH',)
            # Build ID arguments tuple and format as required by IMAP protocol
            args = ("name", cfg.imap_username, "contact", cfg.imap_username, "version", "1.0.0", "vendor", "tgo-platform")
            # Convert to IMAP format: remove commas and use double quotes
            id_args = str(args).replace(",", "").replace("'", '"')
            await asyncio.to_thread(conn._simple_command, 'ID', id_args)
            print(f"[EMAIL] IMAP ID command sent successfully for {cfg.imap_username}")
        except Exception as id_err:
            # ID command failure is non-critical, continue anyway
            print(f"[EMAIL] Could not send IMAP ID command (non-critical): {id_err}")

        # Select mailbox (read-only for fetching)
        typ, data = await asyncio.to_thread(conn.select, cfg.mailbox, True)
        if typ != 'OK':
            # List available mailboxes for debugging
            try:
                list_typ, mailboxes = await asyncio.to_thread(conn.list)
                if list_typ == 'OK' and mailboxes:
                    available = [mb.decode() if isinstance(mb, bytes) else str(mb) for mb in mailboxes[:10]]
                    print(f"[EMAIL] Available mailboxes (first 10): {available}")
            except Exception as list_err:
                print(f"[EMAIL] Could not list mailboxes: {list_err}")

            error_msg = data[0].decode() if data and isinstance(data[0], bytes) else str(data)
            raise Exception(f"Cannot select mailbox '{cfg.mailbox}'. Server response: {error_msg}")
        return conn

    async def _close_imap_conn(self, platform_id: uuid.UUID) -> None:
        conn = self._imap_conns.pop(platform_id, None)
        if conn:
            with suppress(Exception):
                await asyncio.to_thread(conn.logout)

    def _decode_header_value(self, value: Optional[str]) -> str:
        if not value:
            return ""
        try:
            return str(make_header(decode_header(value)))
        except Exception:
            return value or ""

    def _extract_text_from_message(self, msg: Message) -> tuple[str, Optional[str]]:
        # Prefer text/plain, but also capture raw HTML (unescaped) when present for Markdown conversion
        plain: Optional[str] = None
        html_raw: Optional[str] = None
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_maintype() == 'multipart':
                    continue
                disp = part.get('Content-Disposition', '') or ''
                if 'attachment' in disp.lower():
                    continue
                ctype = part.get_content_type()
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or 'utf-8'
                try:
                    text = payload.decode(charset, errors='replace')
                except Exception:
                    text = payload.decode('utf-8', errors='replace')
                if ctype == 'text/plain' and plain is None:
                    plain = text
                elif ctype == 'text/html' and html_raw is None:
                    html_raw = html_module.unescape(text)
        else:
            ctype = msg.get_content_type()
            payload = msg.get_payload(decode=True) or b""
            charset = msg.get_content_charset() or 'utf-8'
            try:
                text = payload.decode(charset, errors='replace')
            except Exception:
                text = payload.decode('utf-8', errors='replace')
            if ctype == 'text/plain':
                plain = text
            elif ctype == 'text/html':
                html_raw = html_module.unescape(text)
        # Provide a plain-text fallback by stripping HTML if no explicit text/plain part
        fallback = self._strip_html(html_raw) if (plain is None and html_raw) else None
        body_text = (plain or fallback or "").strip()
        return body_text, html_raw


    async def _html_to_markdown_async(self, html: str) -> str:
        """Convert HTML to Markdown using markdownify if available; fallback to plain text.
        This is async-friendly to allow future offloading if needed.
        """
        if not html:
            return ""
        try:
            # Import inside function to avoid hard dependency at import time
            import markdownify  # type: ignore
            return markdownify.markdownify(html, heading_style="ATX")
        except Exception:
            # Fallback: naive strip of tags
            return self._strip_html(html)

    def _strip_html(self, s: str) -> str:
        return re.sub(r"<[^>]+>", "", s)


    async def _mark_seen(self, p: _PlatformEntry, uid: str) -> None:
        conn = await self._get_imap_conn(p)
        if not conn:
            return
        try:
            # Select writable mailbox for flag updates
            typ, _ = await asyncio.to_thread(conn.select, p.cfg.mailbox, False)
            if typ == 'OK':
                await asyncio.to_thread(conn.uid, 'store', uid, '+FLAGS', '(\\Seen)')
        except Exception as e:
            print(f"[EMAIL] Mark seen failed for {p.id} uid={uid}: {e}")

    async def _poll_platform(self, p: _PlatformEntry) -> None:
        """Polling loop for a single platform. Reuses connection context if implemented."""
        while not self._stop_event.is_set():
            try:
                raws = await self._fetch_new_emails(p)
                if raws:
                    await self._store_raw_batch(p, raws)
            except asyncio.CancelledError:
                # Gracefully close IMAP connection and propagate cancellation
                await self._close_imap_conn(p.id)
                raise
            except Exception as e:  # pragma: no cover - protective catch
                print(f"[EMAIL] Poller error for {p.id}: {e}")
            # Backoff between polls per-platform
            await asyncio.sleep(max(1, int(p.cfg.poll_interval_seconds)))

    async def _fetch_new_emails(self, p: _PlatformEntry) -> list[dict]:
        try:
            conn = await self._get_imap_conn(p)
            if not conn:
                return []
            # Ensure mailbox selected (read-only) for fetching
            typ, _ = await asyncio.to_thread(conn.select, p.cfg.mailbox, True)
            if typ != 'OK':
                print(f"[EMAIL] Select mailbox failed for {p.id}")
                return []

            # Apply lookback window: only fetch emails SINCE a given date
            lookback_days = max(0, int(getattr(p.cfg, "fetch_lookback_days", 3) or 3))
            since_dt = datetime.now(timezone.utc) - timedelta(days=lookback_days)
            since_date = since_dt.strftime('%d-%b-%Y')
            print(f"[EMAIL] IMAP search criteria: UNSEEN SINCE {since_date} (lookback_days={lookback_days})")
            typ, data = await asyncio.to_thread(conn.uid, 'search', None, 'UNSEEN', 'SINCE', since_date)
            if typ != 'OK' or not data or not data[0]:
                return []
            all_uids = data[0].split()
            if not all_uids:
                return []

            total_unread = len(all_uids)
            # Newest first: reverse UID ordering (UIDs generally increase over time)
            limit = max(1, int(getattr(p.cfg, "max_emails_per_poll", 10) or 10))
            uids = (all_uids[::-1])[:limit]
            print(f"[EMAIL] Unread={total_unread}, processing={len(uids)} (newest first) for platform {p.id}")
            results: list[dict] = []

            for uid in uids:
                try:
                    typ, fetch_data = await asyncio.to_thread(conn.uid, 'fetch', uid, '(RFC822)')
                    if typ != 'OK' or not fetch_data:
                        continue
                    raw_bytes = None
                    for part in fetch_data:
                        if isinstance(part, tuple) and isinstance(part[1], (bytes, bytearray)):
                            raw_bytes = part[1]
                            break
                    if not raw_bytes:
                        continue
                    msg = message_from_bytes(raw_bytes, policy=policy.default)
                    message_id = self._decode_header_value(msg.get('Message-ID') or msg.get('Message-Id'))
                    from_ = self._decode_header_value(msg.get('From'))
                    subject = self._decode_header_value(msg.get('Subject'))
                    date = self._decode_header_value(msg.get('Date'))
                    body_text, body_html = self._extract_text_from_message(msg)
                    uid_str = uid.decode() if isinstance(uid, bytes) else str(uid)
                    # Collect raw headers as a dict
                    headers_dict = {k: str(v) for k, v in msg.items()}
                    if body_html:
                        headers_dict["__body_html__"] = body_html
                    results.append({
                        "Message-ID": message_id or uid_str,  # fallback to stable UID
                        "From": from_ or "",
                        "Subject": subject or "",
                        "Body": body_text or "",
                        "Date": date or "",
                        "imap_uid": uid_str,
                        "Headers": headers_dict,
                    })
                except Exception as e:
                    print(f"[EMAIL] Fetch/parse failed for {p.id}: {e}")
                    continue

            return results
        except Exception as e:
            print(f"[EMAIL] IMAP fetch error for {p.id}: {e}")
            return []

    async def _store_raw_batch(self, p: _PlatformEntry, raws: list[dict]) -> None:
        # Store messages concurrently; mark SEEN only after successful insert (or detected duplicate by unique constraint)
        coros = [self._store_single_raw(p, raw) for raw in raws]
        await asyncio.gather(*coros, return_exceptions=False)

    async def _store_single_raw(self, p: _PlatformEntry, raw: dict) -> None:
        mid = raw.get("Message-ID") or raw.get("imap_uid") or str(uuid.uuid4())
        if await self._idempotency.seen(p.id, mid):
            return

        # Extract sender info from From header
        from_header = raw.get("From", "")
        sender_name, sender_email = parseaddr(from_header)
        sender_email = (sender_email or "unknown@example.com").strip().lower()
        if not sender_name:
            sender_name = sender_email.split("@")[0] if "@" in sender_email else sender_email

        # Parse received_at from Date header if available
        received_at: datetime | None = None
        try:
            date_hdr = raw.get("Date")
            if date_hdr:
                received_at = parsedate_to_datetime(date_hdr)
        except Exception:
            received_at = None

        # Insert into email_inbox table
        async with self._session_factory() as db:
            rec = EmailInbox(
                platform_id=p.id,
                project_id=p.project_id,
                message_id=mid,
                imap_uid=str(raw.get("imap_uid") or ""),
                from_address=sender_email,
                from_name=sender_name,
                subject=raw.get("Subject") or None,
                body=raw.get("Body") or "",
                raw_headers=raw.get("Headers") or None,
                received_at=received_at,
                status="pending",
                retry_count=0,
            )
            try:
                db.add(rec)
                await db.commit()
                await db.refresh(rec)
                # Only mark idempotent and SEEN when stored successfully
                await self._idempotency.mark(p.id, mid)
                uid = raw.get("imap_uid")
                if uid:
                    await self._mark_seen(p, uid)
            except IntegrityError:
                # Duplicate (platform_id, message_id) already stored by previous cycle
                await db.rollback()
                await self._idempotency.mark(p.id, mid)
                uid = raw.get("imap_uid")
                if uid:
                    await self._mark_seen(p, uid)
            except Exception as e:
                await db.rollback()
                print(f"[EMAIL] Store raw email failed for {p.id}: {e}")
                # Do not mark SEEN so it can be retried in a future cycle


    async def _consumer_loop(self) -> None:
        """Continuously process pending/eligible emails from the inbox table."""
        while not self._stop_event.is_set():
            try:
                platforms = await self._load_active_email_platforms()
                for p in platforms:
                    try:
                        await self._process_pending_for_platform(p)
                    except Exception as e:
                        print(f"[EMAIL] Consumer error for platform {p.id}: {e}")
            except Exception as e:
                print(f"[EMAIL] Consumer supervisor error: {e}")
            await asyncio.sleep(2)

    async def _process_pending_for_platform(self, p: _PlatformEntry) -> None:
        """Process a batch of pending (and eligible failed) emails for a single platform."""
        batch_size = max(1, int(getattr(p.cfg, "processing_batch_size", 10) or 10))
        max_retries = max(0, int(getattr(p.cfg, "max_retry_attempts", 3) or 3))

        async with self._session_factory() as db:
            candidates = await claim_inbox_batch(
                db,
                EmailInbox,
                platform_id=p.id,
                batch_size=batch_size,
                max_retry_attempts=max_retries,
            )

            if not candidates:
                return

            for rec in candidates:
                try:
                    # Prepare mapped raw payload
                    from_addr = rec.from_address
                    sender_name = rec.from_name or (from_addr.split("@")[0] if "@" in from_addr else from_addr)

                    # If we captured HTML body in raw_headers, convert to Markdown for chat
                    html_body = None
                    try:
                        html_body = (rec.raw_headers or {}).get("__body_html__")
                    except Exception:
                        html_body = None
                    content_for_chat = rec.body or ""
                    if html_body:
                        try:
                            md = await self._html_to_markdown_async(str(html_body))
                            if md:
                                content_for_chat = md
                        except Exception:
                            pass

                    mapped_raw = {
                        "source": "email",
                        "from_uid": from_addr,
                        "content": content_for_chat,
                        "platform_api_key": p.api_key or "",
                        "platform_type": "email",
                        "platform_id": str(p.id),
                        "extra": {
                            "project_id": str(p.project_id),
                            "subject": rec.subject,
                            "message_id": rec.message_id,
                            "from_name": sender_name,
                        },
                    }

                    # Visitor registration
                    visitor = None
                    if p.api_key:
                        try:
                            visitor = await self._visitor_service.register_or_get(
                                platform_api_key=p.api_key,
                                project_id=str(p.project_id),
                                platform_type="email",
                                platform_open_id=from_addr,
                                nickname=sender_name,
                            )
                        except Exception as e:
                            print(f"[EMAIL] Visitor registration failed for {p.id}: {e}")

                    # Normalize and dispatch
                    msg: NormalizedMessage = await self._normalizer.normalize(mapped_raw)
                    reply_text = await process_message(
                        msg=msg,
                        db=db,
                        tgo_api_client=self._tgo_api_client,
                        sse_manager=self._sse_manager
                    )

                    # Success
                    rec.ai_reply = reply_text
                    finalize_inbox_success(rec)
                    await db.commit()
                except Exception as e:
                    # Failure with retry increment
                    print(f"[EMAIL] Processing failed for {p.id}: {e}")
                    finalize_inbox_failure(
                        rec,
                        e,
                        max_retry_attempts=max_retries,
                    )
                    await db.commit()

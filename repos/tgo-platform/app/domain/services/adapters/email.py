from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Optional
import asyncio

from app.domain.entities import StreamEvent
from app.domain.services.adapters.base import BasePlatformAdapter


class EmailAdapter(BasePlatformAdapter):
    """Outbound email adapter using SMTP credentials from Platform.config.

    This adapter is non-streaming: it sends the final aggregated content as an email.
    It is instantiated with the necessary addressing/context derived from the
    NormalizedMessage and Platform.config.
    """

    supports_stream = False

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        smtp_username: str,
        smtp_password: str,
        smtp_use_tls: bool = True,
        to_addr: str | None = None,
        from_addr: str | None = None,
        subject: str | None = None,
    ) -> None:
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_username = smtp_username
        self.smtp_password = smtp_password
        self.smtp_use_tls = smtp_use_tls
        self.to_addr = to_addr or smtp_username
        self.from_addr = from_addr or smtp_username
        self.subject = subject or ""

    async def send_incremental(self, ev: StreamEvent) -> None:  # pragma: no cover - not used
        # Email adapter does not support streaming output; ignore incremental events
        return

    async def send_final(self, content: dict) -> None:
        """Send the aggregated content as a multipart email (text + HTML) via SMTP.

        - Assumes the chat API returns Markdown for email (dispatcher sets expected_output="markdown").
        - Converts Markdown -> HTML for richer email clients while preserving plain text alternative.
        """
        if not self.to_addr:
            # No recipient; nothing to do
            logging.warning("No recipient email address provided; skipping email output.")
            return
        text_md = content.get("text") or ""

        # Convert Markdown to HTML (best-effort)
        html_body = self._markdown_to_html(text_md)

        msg = EmailMessage()
        msg["Subject"] = self.subject or ""
        msg["From"] = self.from_addr
        msg["To"] = self.to_addr
        # Plain text alternative uses the raw markdown rendered as text, acceptable for most clients
        msg.set_content(text_md)
        # HTML alternative if available
        if html_body:
            msg.add_alternative(html_body, subtype="html")

        # Avoid dumping full message content in stdout; keep debug concise
        logging.debug("Preparing to send email via SMTP host=%s port=%s tls=%s to=%s", self.smtp_host, self.smtp_port, self.smtp_use_tls, self.to_addr)

        # Robust connection with retries and proper TLS/SSL handling
        attempts = 1 if content.get("delivery_fenced") else 2
        last_err: Exception | None = None
        for attempt in range(1, attempts + 1):
            client: smtplib.SMTP | smtplib.SMTP_SSL | None = None
            try:
                # Use implicit SSL for port 465 regardless of smtp_use_tls flag
                if self.smtp_port == 465:
                    client = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=30)
                else:
                    client = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30)
                    client.ehlo()
                    if self.smtp_use_tls:
                        # STARTTLS if requested and supported
                        try:
                            if client.has_extn("starttls"):
                                client.starttls()
                                client.ehlo()
                            else:
                                logging.warning("SMTP server does not advertise STARTTLS; continuing without TLS")
                        except Exception as e:
                            logging.warning("STARTTLS negotiation failed: %s", e)
                            raise

                # Authenticate if credentials provided
                if self.smtp_username and self.smtp_password:
                    try:
                        client.login(self.smtp_username, self.smtp_password)
                    except smtplib.SMTPAuthenticationError as e:
                        logging.error("SMTP authentication failed: code=%s msg=%s", getattr(e, 'smtp_code', None), getattr(e, 'smtp_error', None))
                        raise

                # Send email
                rejected = client.send_message(msg)
                if rejected:
                    raise RuntimeError("SMTP recipient was rejected")
                import hashlib
                self.delivery_receipt = "smtp:accepted:" + hashlib.sha256(msg.as_bytes()).hexdigest()
                logging.info("Email sent to %s with subject '%s'", self.to_addr, self.subject)
                # Cleanup and return on success
                try:
                    client.quit()
                except Exception as e:
                    logging.debug("SMTP quit raised but ignored: %s", e)
                return
            except Exception as e:
                last_err = e
                logging.warning("Attempt %d/%d to send email failed: %s", attempt, attempts, e)
                # Ensure socket closed before retry
                try:
                    if client:
                        client.close()
                except Exception:
                    pass
                if attempt < attempts:
                    await asyncio.sleep(1.5)

        # If we reached here, all attempts failed
        raise RuntimeError(f"Failed to send email after {attempts} attempts: {last_err}")

    def _markdown_to_html(self, markdown_text: str) -> str:
        """Convert Markdown to HTML using markdown2 if available; fallback to minimal formatting."""
        if not markdown_text:
            return ""
        # Try markdown2 first
        try:
            import markdown2  # type: ignore
            return markdown2.markdown(markdown_text)
        except Exception:
            pass
        # Try Python-Markdown next if installed under 'markdown'
        try:
            import markdown  # type: ignore
            return markdown.markdown(markdown_text)
        except Exception:
            pass
        # Fallback: replace double newlines with paragraph breaks and single newlines with <br>
        esc = markdown_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        paras = [p.strip() for p in esc.split("\n\n")]
        html_paras = [p.replace("\n", "<br>\n") for p in paras]
        return "\n\n".join(f"<p>{p}</p>" for p in html_paras)


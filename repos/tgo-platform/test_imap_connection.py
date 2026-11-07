#!/usr/bin/env python3
"""Test IMAP connection and list available mailboxes.

Usage:
    python test_imap_connection.py <host> <port> <username> <password> [--no-ssl]

Example:
    python test_imap_connection.py imap.163.com 993 user@163.com password123
"""

import imaplib
import sys


def test_imap(host, port, username, password, use_ssl=True):
    print(f"Testing IMAP connection to {host}:{port}")
    print(f"Username: {username}")
    print(f"SSL: {use_ssl}")
    print("-" * 60)

    try:
        # Connect
        if use_ssl:
            conn = imaplib.IMAP4_SSL(host, int(port))
        else:
            conn = imaplib.IMAP4(host, int(port))
        print("✓ Connected to IMAP server")

        # Login
        conn.login(username, password)
        print("✓ Login successful")

        # List mailboxes
        typ, mailboxes = conn.list()
        if typ == "OK":
            print(f"\n✓ Available mailboxes ({len(mailboxes)} total):")
            for mb in mailboxes:
                decoded = mb.decode() if isinstance(mb, bytes) else str(mb)
                print(f"  - {decoded}")

        # Try to select INBOX
        print("\nTrying to select 'INBOX'...")
        typ, data = conn.select("INBOX", True)
        if typ == "OK":
            print("✓ Successfully selected INBOX")
            print(f"  Messages in INBOX: {data[0].decode()}")
        else:
            print("✗ Failed to select INBOX")
            print(f"  Server response: {data}")

        # Logout
        conn.logout()
        print("\n✓ Test completed successfully")

    except imaplib.IMAP4.error as e:
        print(f"\n✗ IMAP Error: {e}")
        return False
    except Exception as e:
        print(f"\n✗ Error: {e}")
        return False

    return True


if __name__ == "__main__":
    if len(sys.argv) < 5:
        print(__doc__)
        sys.exit(1)

    host = sys.argv[1]
    port = sys.argv[2]
    username = sys.argv[3]
    password = sys.argv[4]
    use_ssl = "--no-ssl" not in sys.argv

    success = test_imap(host, port, username, password, use_ssl)
    sys.exit(0 if success else 1)

"""A temporary cloud authorization failure must not freeze local intake."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import Request

from tools.wechat_local_bridge.reception_scope import Contact, Grant
from tools.wechat_local_bridge.scoped_bridge import ScopedBridge, Worker, cloud_identity
from tools.wechat_local_bridge.tgo_client import TgoAuthorizationError, request_json


class ScopedRecoveryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.grant = Grant(Contact("customer", "测试好友", "", False, True), "a" * 32, ())
        self.scope = Mock()
        self.scope.grants.return_value = {"customer": self.grant}
        self.client = Mock(account_tag="b" * 16)
        self.service = ScopedBridge(self.scope, self.client, Path(folder.name), True)
        self.addCleanup(self.service.network_pool.shutdown)
        self.addCleanup(self.service.delivery_pool.shutdown)
        self.service._active = Mock(return_value=True)
        self.bridge = Mock()
        self.bridge.ledger.initialized.return_value = True
        self.worker = Worker(self.grant, self.bridge)

    def publish(self):
        self.service.published = [
            {
                "identity": cloud_identity(self.client.account_tag, self.grant),
                "keywords": [],
            }
        ]

    def test_worker_waits_until_its_exact_revision_is_published(self):
        self.service._process(self.worker)
        self.bridge.initialize.assert_not_called()
        self.publish()
        self.service._process(self.worker)
        self.bridge.initialize.assert_called_once()

    def test_temporary_403_republishes_current_grant_and_recovers_without_manual_reset(
        self,
    ):
        self.publish()
        self.bridge.initialize.side_effect = [TgoAuthorizationError(403), None]
        before = time.monotonic()
        self.service._process(self.worker)
        self.assertEqual(self.worker.error, "")
        self.assertFalse(self.worker.connected)
        self.assertLessEqual(self.worker.next_poll - before, 2)
        self.assertEqual(self.service.next_publish, 0)
        self.service._process(self.worker)
        self.assertTrue(self.worker.connected)
        self.bridge.poll.assert_called_once_with(read_local=False, deliver=False)

    def test_revoked_local_grant_cannot_reconnect(self):
        self.publish()
        self.service._active.return_value = False
        self.service._process(self.worker)
        self.bridge.initialize.assert_not_called()

    def test_rejected_visitor_token_is_refreshed_for_the_same_authorized_identity(self):
        self.publish()
        self.bridge.initialize.side_effect = [TgoAuthorizationError(401), None]
        self.service._process(self.worker)
        self.assertTrue(self.worker.refresh_session)
        self.service._process(self.worker)
        self.bridge.client.connect.assert_called_once_with(force_refresh=True)
        self.assertFalse(self.worker.refresh_session)
        self.assertTrue(self.worker.connected)

    def test_authorization_status_does_not_expose_response_or_credentials(self):
        opener = Mock()
        opener.open.side_effect = HTTPError(
            "https://example.test/api", 403, "private-response", {}, None
        )
        with self.assertRaises(TgoAuthorizationError) as caught:
            request_json(opener, Request("https://example.test/api"))
        self.assertEqual(caught.exception.status_code, 403)
        self.assertNotIn("private-response", str(caught.exception))

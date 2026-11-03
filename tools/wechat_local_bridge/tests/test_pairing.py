import hashlib
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen
from uuid import uuid4

from tools.wechat_local_bridge import pairing


class PairingTests(unittest.TestCase):
    def test_callback_checks_state_and_exchanges_proof_for_current_channel(self):
        platform = uuid4()
        code = "c" * 43
        proof = "p" * 43
        challenge = hashlib.sha256(proof.encode("ascii")).hexdigest()

        def confirm(url):
            destination = urlsplit(url)
            self.assertEqual(destination.path, f"/platforms/{platform}")
            self.assertNotIn(proof, url)
            params = parse_qs(destination.fragment)
            self.assertEqual(params["wechat_pair"], [challenge])
            callback = f"http://127.0.0.1:{params['port'][0]}/wechat-pair"
            with self.assertRaises(HTTPError) as denied:
                urlopen(callback + "?" + urlencode({"code": code, "state": "wrong"}), timeout=5)
            self.assertEqual(denied.exception.code, 400)
            denied.exception.close()
            with urlopen(callback + "?" + urlencode({"code": code, "state": challenge}), timeout=5) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
            return True

        with (
            patch.object(pairing.secrets, "token_urlsafe", return_value=proof),
            patch.object(pairing.webbrowser, "open", side_effect=confirm),
            patch.object(pairing, "post_json", return_value={"platform_api_key": "fixture-key"}) as exchange,
            patch("builtins.print") as output,
        ):
            key = pairing.pair_current_channel("https://example.test/api", platform)

        self.assertEqual(key, "fixture-key")
        printed_urls = [call.args[0] for call in output.call_args_list if call.args[0].startswith("https://")]
        self.assertEqual(len(printed_urls), 1)
        self.assertIn(f"/platforms/{platform}#wechat_pair={challenge}", printed_urls[0])
        _, endpoint, payload, _ = exchange.call_args.args
        self.assertEqual(endpoint, f"https://example.test/api/v1/platforms/{platform}/wechat-local/pair/exchange")
        self.assertEqual(payload, {"code": code, "verifier": proof})


if __name__ == "__main__":
    unittest.main()

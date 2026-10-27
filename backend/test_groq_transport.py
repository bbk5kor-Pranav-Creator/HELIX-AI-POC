import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from groq_transport import groq_http_options


class GroqTransportTests(unittest.TestCase):
    def test_unconfigured_transport_preserves_existing_defaults(self):
        with patch.dict(os.environ, {"GROQ_PROXY_URL": ""}):
            self.assertEqual(groq_http_options(), {})

    def test_configured_proxy_is_scoped_to_groq_client(self):
        with patch.dict(os.environ, {"GROQ_PROXY_URL": "http://127.0.0.1:3128"}), patch(
            "groq_transport.DefaultHttpxClient"
        ) as transport:
            options = groq_http_options()
            transport.assert_called_once_with(proxy="http://127.0.0.1:3128", trust_env=False)
            self.assertIs(options["http_client"], transport.return_value)

    def test_invalid_proxy_fails_without_disclosing_configuration(self):
        for value in ("not-a-url", "socks5://localhost:3128", "http://localhost:bad"):
            with self.subTest(value=value), patch.dict(os.environ, {"GROQ_PROXY_URL": value}):
                with self.assertRaises(HTTPException) as error:
                    groq_http_options()
                self.assertEqual(error.exception.status_code, 503)
                self.assertNotIn(value, error.exception.detail)


if __name__ == "__main__":
    unittest.main()

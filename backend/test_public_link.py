import io
import os
import socket
import ssl
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import knowledge_store
import public_link_routes
from knowledge_routes import create_access_token, router as knowledge_router


PUBLIC_DNS_RESULT = [
    (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443)),
]

CLEANING_FIXTURES = (
    (
        "documentation",
        "[Start](/) | [Guides](/guides) | [SDK](/sdk)\n\n"
        "# Deployment\n\n## Table of contents\n"
        "- [Install](#install)\n- [Configure](#configure)\n\n"
        "## Install\n\nInstall version 2.4.1 with [the SDK](/sdk).\n\n"
        "## Configure\n\n1. Set timeout to 300 ms.\n2. Open port 443.\n\n"
        "[Start](/) | [Guides](/guides) | [SDK](/sdk)",
        "# Deployment\n\n## Install\n\nInstall version 2.4.1 with [the SDK](/sdk).\n\n"
        "## Configure\n\n1. Set timeout to 300 ms.\n2. Open port 443.",
    ),
    (
        "article",
        '<div role="banner"><div>Site masthead</div>Account toolbar</div>\n'
        '<select aria-label="Edition"><option>2025</option><option>2026</option></select>\n'
        "# Field report\n\n<header>Published 2026-10-29 by Ana</header>\n\n"
        "The sample contained 42 records; 3 failed validation.\n\n"
        "[Download data](https://data.example.org/results.csv)\n\n"
        '<section role="contentinfo"><p>Site legal links</p></section>',
        "# Field report\n\n<header>Published 2026-10-29 by Ana</header>\n\n"
        "The sample contained 42 records; 3 failed validation.\n\n"
        "[Download data](https://data.example.org/results.csv)",
    ),
    (
        "technical reference",
        "## Navigation\n[Catalog](/catalog) | [Index](/index)\n\n"
        "# HTTP reference\n\n"
        "Use [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110) and "
        "[URI syntax](https://example.org/a_(b)?n=2#syntax).\n\n"
        "| Header | Value |\n| --- | --- |\n| Retry-After | 120 |\n\n"
        "```html\n<select><option>Technical example</option></select>\n"
        '<div role="banner">Example markup</div>\n```\n\n'
        "## Navigation\n\nRouting uses [the table](#routing); keep this explanation.",
        "# HTTP reference\n\n"
        "Use [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110) and "
        "[URI syntax](https://example.org/a_(b)?n=2#syntax).\n\n"
        "| Header | Value |\n| --- | --- |\n| Retry-After | 120 |\n\n"
        "```html\n<select><option>Technical example</option></select>\n"
        '<div role="banner">Example markup</div>\n```\n\n'
        "## Navigation\n\nRouting uses [the table](#routing); keep this explanation.",
    ),
    (
        "multilingual",
        "[入口](/) | [文档](/docs) | [版本](/versions)\n\n"
        "# 手册\n\n[安装](#%E5%AE%89%E8%A3%85)\n[配置](#配置)\n\n"
        "## 安装\n\n安装版本 2.4；保留 [参考](/reference)。\n\n"
        "## 配置\n\nकर्मचारी 3 अनुरोध भेजता है। مدة الانتظار 24 ساعة.\n\n"
        "Échéance : 10 jours.",
        "# 手册\n\n## 安装\n\n安装版本 2.4；保留 [参考](/reference)。\n\n"
        "## 配置\n\nकर्मचारी 3 अनुरोध भेजता है। مدة الانتظار 24 ساعة.\n\n"
        "Échéance : 10 jours.",
    ),
    (
        "boilerplate heavy",
        '<div role="navigation"><div>[Browse](/browse)</div>Toolbar remainder</div>\n'
        "[Portal](/) | [Library](/library) | [Releases](/releases)\n\n"
        "# Maintenance\n\n## Table of contents\n"
        "- [Checks](#checks)\n  - [Recovery](#recovery)\n\n"
        "## Checks\n\nCheck checksum SHA-256 before installation.\n\n"
        "[Portal](/) | [Library](/library) | [Releases](/releases)\n\n"
        "## Recovery\n\nRestore the last verified backup; never overwrite it.\n\n"
        "[Portal](/) | [Library](/library) | [Releases](/releases)\n\n"
        '<div role="contentinfo"><div>Footer toolbar</div>Repeated legal UI</div>',
        "# Maintenance\n\n## Checks\n\nCheck checksum SHA-256 before installation.\n\n"
        "## Recovery\n\nRestore the last verified backup; never overwrite it.",
    ),
    (
        "repeated vertical menu",
        "- [Portal](/)\n- [Library](/library)\n- [Releases](/releases)\n\n"
        "# Operations\n\nRetain 3 verified backups.\n\n"
        "- [Portal](/)\n- [Library](/library)\n- [Releases](/releases)",
        "# Operations\n\nRetain 3 verified backups.",
    ),
    (
        "ambiguous",
        "# References\n\n[Compiler](/compiler)\n[Protocol](/protocol)\n"
        "[Schema](/schema)\n\n"
        "## Navigation\n\n[Routing](#routing) and [Metrics](#metrics) explain the API.\n\n"
        "## Contents\n\n[Unknown](#unknown)\n[Routing](#routing)\n\n"
        "## Routing\n\nUse `route_id=42`; preserve https://example.org/v2.\n\n"
        "## Metrics\n\n| Metric | Value |\n| --- | --- |\n| calls | 0 |",
        "# References\n\n[Compiler](/compiler)\n[Protocol](/protocol)\n"
        "[Schema](/schema)\n\n"
        "## Navigation\n\n[Routing](#routing) and [Metrics](#metrics) explain the API.\n\n"
        "## Contents\n\n[Unknown](#unknown)\n[Routing](#routing)\n\n"
        "## Routing\n\nUse `route_id=42`; preserve https://example.org/v2.\n\n"
        "## Metrics\n\n| Metric | Value |\n| --- | --- |\n| calls | 0 |",
    ),
)


class PublicURLValidationTests(unittest.TestCase):
    def test_rejects_non_public_addresses_and_internal_hostnames(self):
        for value in (
            "http://127.0.0.1/",
            "http://10.0.0.1/",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/",
            "http://240.0.0.1/",
            "http://192.0.0.9/",
            "http://[64:ff9b::a00:1]/",
            "http://0177.0.0x1/",
            "http://intranet/",
            "http://service.home.arpa/",
            "http://service.internal/",
            "file:///etc/passwd",
            "https://user:password@docs.example.com/",
            "https://docs.example.com:8443/",
        ):
            with self.subTest(value=value), self.assertRaises(HTTPException) as raised:
                public_link_routes.validate_public_url(value)
            self.assertEqual(raised.exception.status_code, 422)

    def test_rejects_hostname_with_any_non_public_dns_answer(self):
        answers = PUBLIC_DNS_RESULT + [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.0.0.8", 443)),
        ]
        with patch("public_link_routes.socket.getaddrinfo", return_value=answers):
            with self.assertRaises(HTTPException) as raised:
                public_link_routes.validate_public_url("https://docs.example.com/")
        self.assertIn("non-public", raised.exception.detail)

    def test_normalizes_and_accepts_public_hostname(self):
        with patch("public_link_routes.socket.getaddrinfo", return_value=PUBLIC_DNS_RESULT):
            validated = public_link_routes.validate_public_url(
                "HTTPS://Docs.Example.com/a%20page?mode=read#section"
            )
        self.assertEqual(validated.url, "https://docs.example.com/a%20page?mode=read")
        self.assertEqual(validated.addresses, ("93.184.216.34",))

    def test_resolves_hostname_through_configured_proxy_when_local_dns_fails(self):
        response_a = MagicMock()
        response_a.json.return_value = {
            "Status": 0,
            "Answer": [{"type": 1, "data": "93.184.216.34"}],
        }
        response_aaaa = MagicMock()
        response_aaaa.json.return_value = {"Status": 0}
        proxy_client = MagicMock()
        proxy_client.get.side_effect = [response_a, response_aaaa]

        with patch.dict(os.environ, {"GROQ_PROXY_URL": "http://proxy.example:3128"}), patch(
            "public_link_routes.socket.getaddrinfo", side_effect=socket.gaierror
        ), patch(
            "public_link_routes.groq_http_options",
            return_value={"http_client": proxy_client},
        ) as proxy_options:
            validated = public_link_routes.validate_public_url("https://docs.example.com/")

        self.assertEqual(validated.addresses, ("93.184.216.34",))
        proxy_options.assert_called_once_with()
        self.assertEqual(proxy_client.get.call_count, 2)
        proxy_client.close.assert_called_once_with()

    def test_proxy_dns_verification_does_not_require_resolving_the_resolver_hostname(self):
        proxy_client = MagicMock()

        def dns_response(url, *, params, **kwargs):
            response = MagicMock()
            if url != "https://1.1.1.1/dns-query":
                response.raise_for_status.side_effect = OSError("504 Gateway Timeout")
            response.json.return_value = {
                "Status": 0,
                "Answer": [{"type": 1, "data": "93.184.216.34"}]
                if params["type"] == "A" else [],
            }
            return response

        proxy_client.get.side_effect = dns_response
        with patch(
            "public_link_routes.socket.getaddrinfo", side_effect=socket.gaierror
        ), patch(
            "public_link_routes.groq_http_options",
            return_value={"http_client": proxy_client},
        ):
            target = public_link_routes.validate_public_url("https://docs.example.com/")

        self.assertEqual(target.addresses, ("93.184.216.34",))
        for record_type in ("A", "AAAA"):
            proxy_client.get.assert_any_call(
                "https://1.1.1.1/dns-query",
                params={"name": "docs.example.com", "type": record_type},
                headers={"accept": "application/dns-json"},
                timeout=5,
                follow_redirects=False,
            )
        proxy_client.close.assert_called_once_with()

    def test_proxy_dns_failure_does_not_skip_verification(self):
        proxy_client = MagicMock()
        proxy_client.get.return_value.raise_for_status.side_effect = OSError(
            "504 Gateway Timeout"
        )
        with patch(
            "public_link_routes.socket.getaddrinfo", side_effect=socket.gaierror
        ), patch(
            "public_link_routes.groq_http_options",
            return_value={"http_client": proxy_client},
        ), patch("public_link_routes._send_redirect_probe") as probe:
            with self.assertRaises(HTTPException) as raised:
                public_link_routes.resolve_public_redirects("https://docs.example.com/")

        self.assertEqual(raised.exception.status_code, 502)
        probe.assert_not_called()
        proxy_client.close.assert_called_once_with()

    def test_private_ip_literal_never_uses_proxy(self):
        with patch.dict(os.environ, {"GROQ_PROXY_URL": "http://proxy.example:3128"}), patch(
            "public_link_routes.groq_http_options"
        ) as proxy_options, patch(
            "public_link_routes.socket.create_connection"
        ) as connect:
            with self.assertRaises(HTTPException) as raised:
                public_link_routes.resolve_public_redirects("https://10.0.0.8/")

        self.assertEqual(raised.exception.status_code, 422)
        proxy_options.assert_not_called()
        connect.assert_not_called()

    def test_rejects_private_proxy_dns_answers_when_local_dns_fails(self):
        private_answer = MagicMock()
        private_answer.json.return_value = {
            "Status": 0,
            "Answer": [{"type": 1, "data": "10.0.0.8"}],
        }
        empty_answer = MagicMock()
        empty_answer.json.return_value = {"Status": 0}
        proxy_client = MagicMock()
        proxy_client.get.side_effect = [private_answer, empty_answer]

        with patch.dict(os.environ, {"GROQ_PROXY_URL": "http://proxy.example:3128"}), patch(
            "public_link_routes.socket.getaddrinfo", side_effect=socket.gaierror
        ), patch(
            "public_link_routes.groq_http_options",
            return_value={"http_client": proxy_client},
        ):
            with self.assertRaises(HTTPException) as raised:
                public_link_routes.validate_public_url("https://docs.example.com/")

        self.assertIn("non-public", raised.exception.detail)
        proxy_client.close.assert_called_once_with()

    def test_redirect_to_private_address_is_rejected_before_probe(self):
        with patch("public_link_routes.socket.getaddrinfo", return_value=PUBLIC_DNS_RESULT), patch(
            "public_link_routes._send_redirect_probe", return_value=(302, "http://127.0.0.1/admin")
        ) as probe:
            with self.assertRaises(HTTPException) as raised:
                public_link_routes.resolve_public_redirects("https://docs.example.com/")
        self.assertEqual(raised.exception.status_code, 422)
        probe.assert_called_once()

    def test_redirect_probe_connects_to_the_validated_ip(self):
        response = io.BytesIO(
            b"HTTP/1.1 302 Found\r\n"
            b"Location: https://next.example.com/process\r\n\r\n"
        )
        connection = MagicMock()
        connection.makefile.return_value = response
        tls = MagicMock()
        tls.wrap_socket.return_value = connection
        target = public_link_routes.ValidatedPublicURL(
            "https://docs.example.com/process",
            "docs.example.com",
            443,
            "https",
            ("93.184.216.34",),
        )
        with patch(
            "public_link_routes.socket.create_connection", return_value=connection
        ) as connect, patch(
            "public_link_routes.ssl.create_default_context", return_value=tls
        ):
            status, location = public_link_routes._send_redirect_probe(target)
        self.assertEqual((status, location), (302, "https://next.example.com/process"))
        connect.assert_called_once_with(("93.184.216.34", 443), timeout=5)
        self.assertEqual(
            tls.wrap_socket.call_args.kwargs["server_hostname"], "docs.example.com"
        )
        request = connection.sendall.call_args.args[0].decode("ascii")
        self.assertIn("Host: docs.example.com", request)
        self.assertIn("Range: bytes=0-0", request)

    def test_redirect_probe_uses_proxy_with_validated_ip_when_direct_connection_fails(self):
        proxy_connection = MagicMock()
        proxy_connection.makefile.return_value = io.BytesIO(
            b"HTTP/1.1 200 Connection established\r\n\r\n"
        )
        target_connection = MagicMock()
        target_connection.makefile.return_value = io.BytesIO(
            b"HTTP/1.1 302 Found\r\n"
            b"Location: https://next.example.com/process\r\n\r\n"
        )
        tls = MagicMock()
        tls.wrap_socket.return_value = target_connection
        target = public_link_routes.ValidatedPublicURL(
            "https://docs.example.com/process",
            "docs.example.com",
            443,
            "https",
            ("93.184.216.34",),
        )

        with patch.dict(os.environ, {"GROQ_PROXY_URL": "http://proxy.example:3128"}), patch(
            "public_link_routes.socket.create_connection",
            side_effect=[OSError("Direct network unavailable."), proxy_connection],
        ) as connect, patch(
            "public_link_routes.ssl.create_default_context", return_value=tls
        ):
            status, location = public_link_routes._send_redirect_probe(target)

        self.assertEqual((status, location), (302, "https://next.example.com/process"))
        self.assertEqual(connect.call_args_list[1].args[0], ("proxy.example", 3128))
        connect.assert_any_call(("93.184.216.34", 443), timeout=5)
        connect.assert_any_call(("proxy.example", 3128), timeout=5)
        connect_request = proxy_connection.sendall.call_args.args[0].decode("ascii")
        self.assertIn("CONNECT 93.184.216.34:443 HTTP/1.1", connect_request)
        page_request = target_connection.sendall.call_args.args[0].decode("ascii")
        self.assertIn("Host: docs.example.com", page_request)

    def test_https_proxy_preserves_sni_and_does_not_forward_proxy_authentication(self):
        proxy_connection = MagicMock()
        proxy_connection.makefile.return_value = io.BytesIO(
            b"HTTP/1.1 200 Connection established\r\n\r\n"
        )
        page_connection = MagicMock()
        page_connection.makefile.return_value = io.BytesIO(
            b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"
        )
        context = ssl.create_default_context()
        target = public_link_routes.ValidatedPublicURL(
            "https://docs.example.com/process", "docs.example.com", 443,
            "https", ("93.184.216.34",),
        )
        with patch.dict(os.environ, {
            "GROQ_PROXY_URL": "http://test-user:test-password@proxy.example:3128",
            "HTTPS_PROXY": "http://unused.example:9999",
        }), patch(
            "public_link_routes.socket.create_connection", return_value=proxy_connection
        ) as connect, patch(
            "public_link_routes.ssl.create_default_context", return_value=context
        ) as create_context, patch.object(
            context, "wrap_socket", return_value=page_connection
        ) as wrap_socket:
            result = public_link_routes._send_redirect_probe_via_proxy(target)

        self.assertEqual(result, (200, None))
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        create_context.assert_called_once_with()
        connect.assert_called_once_with(("proxy.example", 3128), timeout=5)
        wrap_socket.assert_called_once_with(
            proxy_connection, server_hostname="docs.example.com"
        )
        tunnel_request = proxy_connection.sendall.call_args.args[0]
        self.assertIn(b"CONNECT 93.184.216.34:443 HTTP/1.1", tunnel_request)
        self.assertIn(b"Proxy-Authorization: Basic ", tunnel_request)
        page_request = page_connection.sendall.call_args.args[0]
        self.assertIn(b"Host: docs.example.com\r\n", page_request)
        self.assertNotIn(b"Proxy-Authorization", page_request)
        self.assertNotIn(b"test-password", page_request)

    def test_https_proxy_certificate_failure_is_not_ignored(self):
        proxy_connection = MagicMock()
        proxy_connection.makefile.return_value = io.BytesIO(
            b"HTTP/1.1 200 Connection established\r\n\r\n"
        )
        context = MagicMock()
        context.wrap_socket.side_effect = ssl.SSLCertVerificationError(
            "Certificate hostname mismatch"
        )
        target = public_link_routes.ValidatedPublicURL(
            "https://docs.example.com/", "docs.example.com", 443,
            "https", ("93.184.216.34",),
        )
        with patch.dict(os.environ, {"GROQ_PROXY_URL": "http://proxy.example:3128"}), patch(
            "public_link_routes.socket.create_connection", return_value=proxy_connection
        ), patch(
            "public_link_routes.ssl.create_default_context", return_value=context
        ), self.assertLogs("public_link_routes", level="WARNING") as logs:
            with self.assertRaises(HTTPException) as raised:
                public_link_routes._send_redirect_probe_via_proxy(target)

        self.assertEqual(raised.exception.status_code, 502)
        self.assertIn("TLS certificate", raised.exception.detail)
        self.assertIn("PUBLIC_LINK_CA_BUNDLE", raised.exception.detail)
        self.assertIn("route=proxy category=certificate_verification", "".join(logs.output))
        self.assertNotIn("Certificate hostname mismatch", "".join(logs.output))
        proxy_connection.sendall.assert_called_once()
        proxy_connection.close.assert_called_once_with()

    def test_unsafe_redirects_from_proxy_response_are_rejected_before_next_connection(self):
        for destination in (
            "https://127.0.0.1/admin", "http://service.internal/",
            "https://docs.example.com:8443/", "https://private.example.com/",
        ):
            with self.subTest(destination=destination):
                proxy_connection = MagicMock()
                proxy_connection.makefile.return_value = io.BytesIO(
                    b"HTTP/1.1 200 Connection established\r\n\r\n"
                )
                page_connection = MagicMock()
                page_connection.makefile.return_value = io.BytesIO(
                    f"HTTP/1.1 302 Found\r\nLocation: {destination}\r\n\r\n".encode("ascii")
                )
                context = MagicMock()
                context.wrap_socket.return_value = page_connection
                public_answer = MagicMock()
                public_answer.json.return_value = {
                    "Status": 0, "Answer": [{"type": 1, "data": "93.184.216.34"}],
                }
                empty_answer = MagicMock()
                empty_answer.json.return_value = {"Status": 0}
                private_answer = MagicMock()
                private_answer.json.return_value = {
                    "Status": 0, "Answer": [{"type": 1, "data": "10.0.0.8"}],
                }
                proxy_client = MagicMock()
                proxy_client.get.side_effect = [
                    public_answer, empty_answer, private_answer, empty_answer,
                ]
                with patch.dict(os.environ, {
                    "GROQ_PROXY_URL": "http://proxy.example:3128",
                }), patch(
                    "public_link_routes.socket.getaddrinfo", side_effect=socket.gaierror
                ), patch(
                    "public_link_routes.groq_http_options",
                    return_value={"http_client": proxy_client},
                ), patch(
                    "public_link_routes.socket.create_connection",
                    side_effect=[OSError("Direct network unavailable"), proxy_connection],
                ) as connect, patch(
                    "public_link_routes.ssl.create_default_context", return_value=context
                ):
                    with self.assertRaises(HTTPException) as raised:
                        public_link_routes.resolve_public_redirects("https://docs.example.com/")

                self.assertEqual(raised.exception.status_code, 422)
                self.assertEqual(connect.call_count, 2)
                page_connection.sendall.assert_called_once()


class PublicLinkTLSConfigurationTests(unittest.TestCase):
    def test_default_trust_and_hostname_checks_remain_enabled(self):
        context = ssl.create_default_context()
        with patch.dict(os.environ, {"PUBLIC_LINK_CA_BUNDLE": ""}), patch(
            "public_link_routes.ssl.create_default_context", return_value=context
        ) as create, patch.object(context, "load_verify_locations") as load:
            self.assertIs(public_link_routes._public_link_tls_context(), context)
        create.assert_called_once_with()
        load.assert_not_called()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_configured_bundle_is_added_to_default_trust(self):
        context = ssl.create_default_context()
        bundle = str(Path(tempfile.gettempdir()) / "test-approved-ca.pem")
        with patch.dict(os.environ, {"PUBLIC_LINK_CA_BUNDLE": bundle}), patch(
            "public_link_routes.ssl.create_default_context", return_value=context
        ) as create, patch.object(context, "load_verify_locations") as load:
            self.assertIs(public_link_routes._public_link_tls_context(), context)
        create.assert_called_once_with()
        load.assert_called_once_with(cafile=bundle)
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_invalid_bundle_fails_closed_without_exposing_path_or_error(self):
        bundle = str(Path(tempfile.gettempdir()) / "private-ca.pem")
        for error in (OSError("private diagnostic"), ssl.SSLError("private certificate")):
            with self.subTest(error=type(error).__name__), patch.dict(
                os.environ, {"PUBLIC_LINK_CA_BUNDLE": bundle}
            ), patch("public_link_routes.ssl.create_default_context") as create:
                create.return_value.load_verify_locations.side_effect = error
                with self.assertLogs("public_link_routes", level="WARNING") as logs:
                    with self.assertRaises(HTTPException) as raised:
                        public_link_routes._public_link_tls_context()
                self.assertEqual(raised.exception.status_code, 503)
                self.assertNotIn("private", raised.exception.detail)
                self.assertNotIn("private", "".join(logs.output))

    def test_relative_bundle_path_is_rejected(self):
        with patch.dict(os.environ, {"PUBLIC_LINK_CA_BUNDLE": "relative-ca.pem"}):
            with self.assertRaises(HTTPException) as raised:
                public_link_routes._public_link_tls_context()
        self.assertEqual(raised.exception.status_code, 503)

    def test_certificate_failure_retained_over_later_network_failure(self):
        certificate_error = ssl.SSLCertVerificationError("private certificate detail")
        certificate_error.verify_code = 19
        target = public_link_routes.ValidatedPublicURL(
            "https://docs.example.com/", "docs.example.com", 443,
            "https", ("93.184.216.34", "93.184.216.35"),
        )
        connection = MagicMock()
        with patch("public_link_routes.socket.create_connection", side_effect=[
            connection, TimeoutError("private network detail"),
        ]), patch("public_link_routes._public_link_tls_context") as context, patch(
            "public_link_routes._send_redirect_probe_via_proxy", return_value=None
        ), self.assertLogs("public_link_routes", level="WARNING") as logs:
            context.return_value.wrap_socket.side_effect = certificate_error
            with self.assertRaises(HTTPException) as raised:
                public_link_routes._send_redirect_probe(target)
        self.assertIn("TLS certificate", raised.exception.detail)
        self.assertIn("PUBLIC_LINK_CA_BUNDLE", raised.exception.detail)
        self.assertIn("tls_verify_code=19", "".join(logs.output))
        self.assertIn("category=timeout", "".join(logs.output))
        self.assertNotIn("private", raised.exception.detail + "".join(logs.output))
        connection.sendall.assert_not_called()
        connection.close.assert_called_once()

    def test_bundle_used_for_https_proxy_and_destination_without_changing_sni(self):
        bundle = str(Path(tempfile.gettempdir()) / "test-approved-ca.pem")
        target = public_link_routes.ValidatedPublicURL(
            "https://docs.example.com/", "docs.example.com", 443,
            "https", ("93.184.216.34",),
        )
        connection, tunnel, page = MagicMock(), MagicMock(), MagicMock()
        tunnel.makefile.return_value = io.BytesIO(b"HTTP/1.1 200 Connection established\r\n\r\n")
        page.makefile.return_value = io.BytesIO(b"HTTP/1.1 200 OK\r\n\r\n")
        with patch.dict(os.environ, {
            "PUBLIC_LINK_CA_BUNDLE": bundle, "GROQ_PROXY_URL": "https://proxy.example:443",
        }), patch("public_link_routes.socket.create_connection", return_value=connection), patch(
            "public_link_routes.ssl.create_default_context"
        ) as create:
            outer, inner = MagicMock(), MagicMock()
            create.side_effect = [outer, inner]
            outer.wrap_socket.return_value = tunnel
            inner.wrap_socket.return_value = page
            self.assertEqual(public_link_routes._send_redirect_probe_via_proxy(target), (200, None))
        outer.load_verify_locations.assert_called_once_with(cafile=bundle)
        inner.load_verify_locations.assert_called_once_with(cafile=bundle)
        outer.wrap_socket.assert_called_once_with(connection, server_hostname="proxy.example")
        inner.wrap_socket.assert_called_once_with(tunnel, server_hostname="docs.example.com")
        self.assertIn(b"CONNECT 93.184.216.34:443", tunnel.sendall.call_args.args[0])

    def test_timeout_remains_distinct_from_certificate_failure(self):
        target = public_link_routes.ValidatedPublicURL(
            "https://docs.example.com/", "docs.example.com", 443,
            "https", ("93.184.216.34",),
        )
        with patch("public_link_routes.socket.create_connection", side_effect=TimeoutError), patch(
            "public_link_routes._send_redirect_probe_via_proxy", return_value=None
        ), self.assertLogs("public_link_routes", level="WARNING") as logs:
            with self.assertRaises(HTTPException) as raised:
                public_link_routes._send_redirect_probe(target)
        self.assertEqual(raised.exception.status_code, 502)
        self.assertIn("Timed out", raised.exception.detail)
        self.assertIn("category=timeout", "".join(logs.output))


class PublicContentTests(unittest.TestCase):
    def test_removes_forms_and_contact_markup_without_removing_article(self):
        markdown = (
            "# Service guide\n\nRequests are reviewed within 24 hours.\n\n"
            '<form><div>Name</div><input name="name"><button>Submit</button></form>\n'
            '<input type="email"/><textarea>Enter your message</textarea>\n'
            '<address>123 Main Street, Sample City</address>\n\n'
            "Read [the specification](/specification) before submission."
        )
        cleaned = public_link_routes.clean_markdown(markdown)
        self.assertIn("Requests are reviewed within 24 hours.", cleaned)
        self.assertIn("[the specification](/specification)", cleaned)
        for unwanted in ("<form", "<input", "Submit", "Enter your message", "123 Main Street"):
            self.assertNotIn(unwanted, cleaned)

    def test_redacts_recognizable_contact_details_in_prose_and_code(self):
        markdown = (
            "# Contacts\n\nCall +44 20 7946 0958 for assistance.\n"
            "Mobile: 9876543210\nPhone: (212) 555-0199\n"
            "[Call us](tel:+12125550199)\n"
            "Mailing address: 17 Example Plaza, Example City\n"
            "Visit 123 Main Street, Example City.\n\n"
            '```text\nphone: 9876543210\n```\n\n'
            "Service is available for 24 hours."
        )
        cleaned = public_link_routes.clean_markdown(markdown)
        for unwanted in ("7946", "9876543210", "555-0199", "tel:", "17 Example", "123 Main"):
            self.assertNotIn(unwanted, cleaned)
        self.assertIn("Service is available for 24 hours.", cleaned)
        self.assertIn("[phone removed]", cleaned)
        self.assertIn("[address removed]", cleaned)

    def test_minified_asset_signals_not_line_length_alone_control_removal(self):
        asset = 'self.webpackChunk=[];' + 'var a={x:1};' * 150
        markdown = "# Article\n\nKeep these deployment instructions.\n\n" + asset
        cleaned = public_link_routes.clean_markdown(markdown)
        self.assertNotIn("webpackChunk", cleaned)
        self.assertIn("Keep these deployment instructions.", cleaned)
        for source in (
            "# Technical example\n\n```javascript\n" + "const x={a:1};" * 150 + "\n```",
            "# Data\n\n```json\n" + '{"values":[' + "123," * 500 + "0]}\n```",
            "# Prose\n\n" + ("Long useful article text. " * 150).strip(),
            "# HTML example\n\n```html\n<form><input><button>Send</button></form>\n```",
            "# Reference\n\nUse `__webpack_require__` when diagnosing bundles.",
        ):
            with self.subTest(source=source[:40]):
                self.assertEqual(public_link_routes.clean_markdown(source), source)

    def test_preserves_technical_identifiers_dates_urls_and_multilingual_body(self):
        markdown = (
            "# Reference\n\nVersion 2.4.1 on 2026-10-29 uses port 443.\n"
            "Request ID 1234567890123456789; IPv4 192.0.2.1; timeout 300 ms.\n"
            "Use [API](https://example.org/v2/1234567890) and `address: 0x1234`.\n\n"
            "Address: 192.0.2.1\n\n"
            "Use [lookup](https://example.org/lookup?q=+442079460958) for API queries.\n\n"
            "经理审核 500 元。 कर्मचारी 3 अनुरोध भेजता है। مدة الانتظار 24 ساعة."
        )
        self.assertEqual(public_link_routes.clean_markdown(markdown), markdown)

    def test_minified_asset_inside_fence_is_removed_but_article_remains(self):
        markdown = (
            "# Reference\n\nRead [the API](/api) for the documented behavior.\n\n"
            "```javascript\n__webpack_require__();" + "var x={a:1};" * 150
            + "\n```\n\nKeep the surrounding explanation."
        )
        cleaned = public_link_routes.clean_markdown(markdown)
        self.assertNotIn("__webpack_require__", cleaned)
        self.assertNotIn("var x=", cleaned)
        self.assertIn("Read [the API](/api)", cleaned)
        self.assertIn("Keep the surrounding explanation.", cleaned)

    def test_removing_contact_only_content_does_not_create_useful_knowledge(self):
        markdown = (
            "# Contacts\n\nPhone: +44 20 7946 0958\n"
            "Mailing address: 17 Example Plaza, Example City"
        )
        cleaned = public_link_routes.clean_markdown(markdown)
        with self.assertLogs("public_link_routes", level="WARNING"), self.assertRaises(HTTPException) as raised:
            public_link_routes.validate_extracted_content(markdown, cleaned)
        self.assertEqual(raised.exception.status_code, 422)

    def test_quoted_cross_references_are_not_removed_as_an_outline(self):
        markdown = (
            "# Citation\n\n> [A](#a)\n> [B](#b)\n\n"
            "## A\n\nRead the first specification.\n\n"
            "## B\n\nRead the second specification."
        )
        self.assertEqual(public_link_routes.clean_markdown(markdown), markdown)

    def test_numbered_multi_link_instructions_survive_boilerplate_heading(self):
        markdown = (
            "# Navigation\n\n"
            "1. [Connect](/connect) | [Verify](/verify)\n"
            "2. [Configure](/configure) | [Restart](/restart)"
        )
        self.assertEqual(public_link_routes.clean_markdown(markdown), markdown)

    def test_structural_cleaning_fixtures_remove_only_identified_chrome(self):
        for name, markdown, expected in CLEANING_FIXTURES:
            with self.subTest(fixture=name):
                self.assertEqual(public_link_routes.clean_markdown(markdown), expected)

    def test_ambiguous_html_and_reference_structures_survive(self):
        for markdown in (
            '<section role="region">Useful technical section</section>',
            '<section role="banner">Unclosed, possibly incomplete markup',
            "# References\n\n[A](#a)\n[B](#b)",
            "# A\n\nDetails.\n\n# B\n\nDetails.\n\n[A](#a)\n[B](#b)",
            "# Reference table\n\n| [A](/a) | [B](/b) |",
            "# Selector API\n\nUse `<select>` and `<option>` to configure 3 choices.",
            "# Examples\n\n    <select><option>Keep indented code</option></select>",
            "# Header\n\n<header>Article title and author</header>",
            '# HTML examples\n\n<pre><select><option>42</option></select></pre>\n\n'
            '<code><section role="banner">Example</section></code>',
            "# Workflow\n\n1. [B](#b)\n2. [A](#a)\n\n"
            "## A\n\nRead first.\n\n## B\n\nExecute next.",
            "# Cross references\n\n[B](#b)\n[A](#a)\n\n"
            "## A\n\nRead first.\n\n## B\n\nExecute next.",
            "1. [Root](/)\n2. [Endpoint](/endpoint)\n3. [Status](/status)\n\n"
            "# Steps\n\nExecute these requests in order.",
            "[Root endpoint](/)\n[Query endpoint](/query)\n[Status endpoint](/status)\n\n"
            "# Reference\n\nThese routes are part of the public API.",
        ):
            with self.subTest(markdown=markdown):
                self.assertEqual(public_link_routes.clean_markdown(markdown), markdown)

    def test_cleaning_preserves_headings_and_removes_boilerplate_and_duplicate_sections(self):
        markdown = """
Skip to content
# Approval process
## Submission
Employees submit a request to their manager.
## Submission
Employees submit a request to their manager.
## Cookie Settings
Accept all cookies
## Review
The manager checks the request.
"""
        cleaned = public_link_routes.clean_markdown(markdown)
        self.assertIn("# Approval process", cleaned)
        self.assertIn("## Submission", cleaned)
        self.assertIn("## Review", cleaned)
        self.assertEqual(cleaned.count("Employees submit a request"), 1)
        self.assertNotIn("Cookie Settings", cleaned)
        self.assertNotIn("Skip to content", cleaned)

    def test_chunks_are_bounded_and_overlap(self):
        text = "# Process\n\n" + ("Approval responsibilities and controls. " * 400)
        chunks = public_link_routes.split_content_chunks(text, chunk_size=500, overlap=80)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 500 for chunk in chunks))
        for previous, current in zip(chunks, chunks[1:]):
            self.assertTrue(
                previous[-40:] in current,
                "Adjacent chunks should retain overlapping source context.",
            )

    def test_cleaning_preserves_non_latin_sections(self):
        markdown = "# प्रक्रिया\nकर्मचारी अनुरोध भेजता है।\n# 审批\n经理审核申请。"
        self.assertEqual(public_link_routes.clean_markdown(markdown), markdown.replace("\n#", "\n\n#"))

    def test_normalization_selection_boundaries(self):
        self.assertFalse(public_link_routes.needs_normalization("x" * 6000))
        self.assertTrue(public_link_routes.needs_normalization("x" * 6001))
        self.assertFalse(public_link_routes.needs_normalization(("x" * 1000 + "\n") * 7))
        self.assertTrue(public_link_routes.needs_normalization(("x" * 1001 + "\n") * 7))

    def test_removes_explicit_navigation_controls_and_advertisement_sections(self):
        markdown = """<nav>[Home](/) [Products](/products)</nav>
[Skip to main content](#main)
## Navigation
- [Home](/)
- [About](/about)
## Advertisement
[Buy now](https://ads.example.com/)
# Installation
Install version 2.4.1.
We use cookies to improve your browsing experience.
Accept all cookies
[Privacy policy](/privacy)
## Footer
[Terms of use](/terms)
<footer>Copyright site chrome</footer>"""
        self.assertEqual(
            public_link_routes.clean_markdown(markdown),
            "# Installation\nInstall version 2.4.1.",
        )

    def test_removes_flat_navigation_but_preserves_substantive_link_collections(self):
        markdown = (
            "[Home](/)\n[About us](/about)\n[Contact](/contact)\n\n"
            "# References\n\n[API v2](/api)\n[Protocol specification](/protocol)"
        )
        self.assertEqual(
            public_link_routes.clean_markdown(markdown),
            "# References\n\n[API v2](/api)\n[Protocol specification](/protocol)",
        )

    def test_preserves_structured_technical_markdown_and_code_verbatim(self):
        markdown = """# API reference

Use [the API](https://docs.example.com/a_(b)?x=1&y=2#section) or <https://example.com/v2>.
Types include List<T> and x < y > z; a&nbsp;b &amp; c.
![Architecture diagram](https://example.com/diagram.svg)

1. Connect to port 443.
   - Retry exactly 3 times.
   - Keep [the endpoint](/v2/query).

| Parameter | Value |
| --- | ---: |
| timeout_ms | 1,000 |
| timeout_ms | 1,000 |

```python
if x < 3:
    print("<script>not executable</script>")
    # Navigation
    print("Accept all cookies")

    value = "&lt;literal&gt;"  # keep entities in code
```

~~~xml
<nav><entry id="1">Technical content</entry></nav>
~~~

    def configure():
        return "  unchanged  "

Inline `<T>`, `<!--example-->`, and `a  b` must survive.

---

## Navigation
Navigation uses the routing table below; this is technical documentation.

## Cookie settings
The API stores SameSite=Lax cookies for 24 hours.

Privacy policy APIs return HTTP 200.
Copyright ownership is part of the license.
"""
        cleaned = public_link_routes.clean_markdown(markdown)
        expected = markdown.replace("a&nbsp;b &amp; c", "a\u00a0b & c").strip()
        self.assertEqual(cleaned, expected)
        self.assertEqual(cleaned.count("| timeout_ms | 1,000 |"), 2)

    def test_deduplicates_exact_sections_and_prose_but_not_steps_or_case_sensitive_facts(self):
        paragraph = "Managers review each request before approval and record the reason in the audit log."
        markdown = (
            f"# Review\n\n{paragraph}\n\n{paragraph}\n\n"
            "1. Open the valve.\n2. Close the valve.\n3. Open the valve.\n\n"
            "# Limits\nUse API_KEY with value 10.\n"
            "# Limits\nUse api_key with value 10.\n"
            "# Limits\nUse API_KEY with value 10."
        )
        cleaned = public_link_routes.clean_markdown(markdown)
        self.assertEqual(cleaned.count(paragraph), 1)
        self.assertEqual(cleaned.count("Use API_KEY with value 10."), 1)
        self.assertIn("Use api_key with value 10.", cleaned)
        self.assertIn("1. Open the valve.\n2. Close the valve.\n3. Open the valve.", cleaned)
        self.assertLess(cleaned.index("# Review"), cleaned.index("# Limits"))

    def test_multilingual_order_numbers_and_diacritics_survive(self):
        markdown = (
            "# 审批\n经理审核申请，限额为 500 元。\n\n"
            "# प्रक्रिया\nकर्मचारी 3 अनुरोध भेजता है।\n\n"
            "# العربية\nمدة الانتظار 24 ساعة.\n\n"
            "# Résumé\nÉchéance : 10 jours; API É != API E."
        )
        self.assertEqual(public_link_routes.clean_markdown(markdown), markdown)

    def test_html_wrappers_are_cleaned_without_decoding_code_or_escaped_tags(self):
        markdown = (
            "<script>track()</script><!-- noise --><style>.ad{}</style>"
            "<h1>API</h1><p>Use &lt;T&gt; &amp; &lt;script&gt; as text.</p>"
            '<table><tr><th>Port</th></tr><tr><td>443</td></tr></table>'
        )
        cleaned = public_link_routes.clean_markdown(markdown)
        self.assertIn("# API", cleaned)
        self.assertIn("Use <T> & <script> as text.", cleaned)
        self.assertIn('<table><tr><th>Port</th></tr><tr><td>443</td></tr></table>', cleaned)
        self.assertNotIn("track()", cleaned)
        self.assertNotIn("noise", cleaned)

    def test_unclosed_fence_is_preserved_instead_of_cleaned_as_markup(self):
        markdown = "# Example\n\n```html\n<nav>Example</nav>\nAccept all cookies"
        self.assertEqual(public_link_routes.clean_markdown(markdown), markdown)

    def test_quoted_fences_multiline_inline_code_and_hard_breaks_are_preserved(self):
        markdown = (
            "# Examples\n\n"
            "> ```html\n> <nav>Technical example</nav>\n> ```\n\n"
            "Inline ``example\n<script>literal</script>\n`inner` `` remains code.\n\n"
            "First line  \nSecond line"
        )
        self.assertEqual(public_link_routes.clean_markdown(markdown), markdown)

    def test_code_with_only_symbols_is_still_useful(self):
        markdown = "```json\n{}\n```"
        self.assertEqual(public_link_routes.clean_markdown(markdown), markdown)
        self.assertGreater(public_link_routes.validate_extracted_content(markdown, markdown), 0)
class PublicLinkFormattingTests(unittest.TestCase):
    def format(self, content):
        metrics = public_link_routes.GenerationMetrics()
        result = public_link_routes.format_content(content, metrics)
        self.assertEqual(metrics.call_count, 0)
        self.assertEqual(metrics.submitted_chars, 0)
        self.assertEqual(metrics.usage_status, "not_requested")
        for field in (
            "estimated_input_tokens", "input_token_budget", "max_completion_tokens",
            "actual_input_tokens", "actual_output_tokens", "actual_total_tokens",
        ):
            self.assertIsNone(getattr(metrics, field))
        self.assertEqual(metrics.latency_ms, 0)
        return result, metrics

    def test_dense_plain_prose_is_soft_wrapped_without_any_rewriting(self):
        source = ("Managers review requests and record approvals. " * 200).strip()
        result, metrics = self.format(source)
        self.assertNotEqual(result, source)
        self.assertTrue(all(len(line) <= 1000 for line in result.splitlines()))
        self.assertEqual(result.replace("\n", " "), source)
        self.assertEqual(len(result), len(source))
        self.assertEqual(metrics.outcome, "deterministic_formatted")
        self.assertEqual(metrics.source_chars, len(source))
        self.assertEqual(self.format(result)[0], result)

    def test_short_and_already_readable_content_are_unchanged(self):
        for source in ("A useful fact.", "x" * 6000, "\n".join(["x" * 1000] * 8)):
            with self.subTest(size=len(source)):
                result, metrics = self.format(source)
                self.assertEqual(result, source)
                self.assertEqual(metrics.outcome, "direct")

    def test_structured_and_ambiguous_content_is_preserved_exactly(self):
        prose = ("Managers review requests and record approvals. " * 180).strip()
        for marker in (
            "# Reference", "1. Open the valve.", "- Open the valve.", "> A quoted statement.",
            "[API](https://example.com/v2)", "https://example.com/v2?limit=10",
            "List<T>", "API_KEY = 42", "```python\n    x = 1\n```",
            "| Name | Value |\n| --- | --- |\n| Port | 443 |",
            "First line  \nSecond line", "    indented code", "a\tb", "![Diagram](/diagram.svg)",
        ):
            with self.subTest(marker=marker):
                source = marker + "\n\n" + prose
                result, metrics = self.format(source)
                self.assertEqual(result, source)
                self.assertEqual(metrics.outcome, "structured_source")

    def test_plain_multilingual_prose_keeps_language_numbers_and_order(self):
        source = ("经理审核申请并记录结果，限额为 500 元。 " * 400).strip()
        result, metrics = self.format(source)
        # Ambiguous Unicode punctuation is conservatively preserved rather than rewritten.
        self.assertEqual(result, source)
        self.assertEqual(metrics.outcome, "structured_source")
        source = ("Les responsables vérifient les demandes sous 24 heures. " * 200).strip()
        result, metrics = self.format(source)
        self.assertEqual(result.replace("\n", " "), source)
        self.assertEqual(metrics.outcome, "deterministic_formatted")

    def test_long_unbroken_tokens_are_not_split(self):
        source = "x" * 7000
        result, metrics = self.format(source)
        self.assertEqual(result, source)
        self.assertEqual(metrics.outcome, "source_preserved")

    def test_wrapping_does_not_introduce_a_markdown_list(self):
        source = "x" * 999 + " 1. " + ("Managers review requests. " * 300).strip()
        result, metrics = self.format(source)
        self.assertEqual(result, source)
        self.assertEqual(metrics.outcome, "source_preserved")

    def test_formatter_fidelity_failure_is_explicit(self):
        source = ("Managers review requests. " * 300).strip()
        with patch("public_link_routes._wrap_prose_line", return_value="Invented facts."), self.assertLogs(
            "public_link_routes", level="ERROR"
        ) as logs, self.assertRaises(HTTPException) as raised:
            self.format(source)
        self.assertEqual(raised.exception.status_code, 500)
        self.assertIn("No partial preview", raised.exception.detail)
        self.assertNotIn("Invented facts", "\n".join(logs.output))


class PublicLinkPreviewRouteTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "SESSION_SECRET": "public-link-test-session-secret-with-more-than-32-characters",
            "FIRECRAWL_API_KEY": "test-firecrawl-key",
            "GROQ_PROXY_URL": "",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "knowledge.db"
        self.database_path = patch.object(knowledge_store, "DATABASE_PATH", self.database)
        self.database_path.start()
        self.addCleanup(self.database_path.stop)
        knowledge_store.initialize_database()

        app = FastAPI()
        app.include_router(public_link_routes.router)
        app.include_router(knowledge_router)
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.admin_headers = {
            "Authorization": f"Bearer {create_access_token('admin@example.test', 'Admin')}"
        }

    def preview_markdown(self, markdown, scraped_result=None):
        firecrawl = MagicMock()
        firecrawl.v1.scrape_url.return_value = scraped_result if scraped_result is not None else SimpleNamespace(
            markdown=markdown, title="Source page", metadata={},
        )
        context = MagicMock()
        completion = context.__enter__.return_value.chat.completions.with_raw_response.create
        with patch(
            "public_link_routes.resolve_public_redirects", return_value="https://example.com/"
        ), patch(
            "public_link_routes.socket.getaddrinfo", return_value=PUBLIC_DNS_RESULT
        ), patch(
            "public_link_routes._create_firecrawl_client", return_value=firecrawl
        ), patch(
            "openai.OpenAI", return_value=context
        ) as create_groq, patch.object(
            knowledge_store, "save_text", side_effect=AssertionError("Preview must not save.")
        ), patch.object(
            knowledge_store, "initialize_database", side_effect=AssertionError("Preview must not initialize storage.")
        ):
            response = self.client.post(
                "/api/knowledge/public-link/preview",
                headers=self.admin_headers,
                json={"url": "https://example.com/"},
            )
        return response, create_groq, completion

    def test_preview_requires_admin_authentication(self):
        with patch("public_link_routes._create_firecrawl_client") as firecrawl, patch(
            "openai.OpenAI"
        ) as groq:
            response = self.client.post(
                "/api/knowledge/public-link/preview",
                json={"url": "https://docs.example.com/process"},
            )
        self.assertEqual(response.status_code, 401)
        firecrawl.assert_not_called()
        groq.assert_not_called()

    def test_mocked_structural_fixtures_keep_zero_call_preview_contract(self):
        for name, markdown, expected in CLEANING_FIXTURES:
            with self.subTest(fixture=name):
                response, factory, completion = self.preview_markdown(markdown)
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertEqual(body["cleaned_content"], expected)
                self.assertEqual(body["generated_content"], expected)
                self.assertEqual(body["metadata"]["raw_content_chars"], len(markdown))
                self.assertEqual(body["metadata"]["content_chars"], len(expected))
                self.assertEqual(body["metadata"]["generation_batch_count"], 0)
                metrics = body["metadata"]["generation_metrics"]
                self.assertEqual(metrics["call_count"], 0)
                self.assertEqual(metrics["submitted_chars"], 0)
                self.assertEqual(metrics["source_chars"], len(expected))
                self.assertEqual(metrics["usage_status"], "not_requested")
                factory.assert_not_called()
                completion.assert_not_called()

    def test_main_content_preview_removes_controls_contacts_and_minified_assets(self):
        asset = 'self.webpackChunk=[];' + 'var a={x:1};' * 150
        markdown = (
            "# Operations\n\nRequests are reviewed in 24 hours.\n\n"
            '<form><input name="email"><button>Subscribe</button></form>\n\n'
            "Phone: +44 20 7946 0958\n"
            "Mailing address: 17 Example Plaza, Example City\n\n"
            + asset + "\n\n"
            "See [the procedure](/procedure).\n\n"
            "```python\nretry_count = 3\n```"
        )
        response, factory, completion = self.preview_markdown(markdown)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        cleaned = body["cleaned_content"]
        for unwanted in ("webpackChunk", "var a=", "Subscribe", "7946", "Example Plaza"):
            self.assertNotIn(unwanted, cleaned)
        for wanted in ("Requests are reviewed in 24 hours.", "[the procedure](/procedure)", "retry_count = 3"):
            self.assertIn(wanted, cleaned)
        self.assertEqual(body["generated_content"], cleaned)
        self.assertEqual(body["metadata"]["raw_content_chars"], len(markdown))
        self.assertEqual(body["metadata"]["content_chars"], len(cleaned))
        self.assertEqual(body["metadata"]["generation_metrics"]["call_count"], 0)
        factory.assert_not_called()
        completion.assert_not_called()

    def test_employee_role_cannot_preview_public_links(self):
        employee_headers = {
            "Authorization": f"Bearer {create_access_token('employee@example.test', 'Employee')}"
        }
        with patch("public_link_routes._create_firecrawl_client") as firecrawl, patch(
            "openai.OpenAI"
        ) as groq:
            response = self.client.post(
                "/api/knowledge/public-link/preview",
                headers=employee_headers,
                json={"url": "https://docs.example.com/process"},
            )
        self.assertEqual(response.status_code, 403)
        firecrawl.assert_not_called()
        groq.assert_not_called()

    def test_admin_preview_returns_content_without_database_writes(self):
        firestore = MagicMock()
        firestore.v1.scrape_url.return_value = SimpleNamespace(
            markdown="# Approval process\n\nManagers review each request.",
            title="Approval process",
            description="How requests are approved.",
            success=True,
            metadata={
                "canonical_url": "https://docs.example.com/process",
            },
        )
        groq_context = MagicMock()
        groq_client = groq_context.__enter__.return_value
        before = knowledge_store.list_sources()
        with patch(
            "public_link_routes.resolve_public_redirects",
            return_value="https://docs.example.com/process",
        ), patch(
            "public_link_routes.socket.getaddrinfo", return_value=PUBLIC_DNS_RESULT
        ), patch(
            "public_link_routes._create_firecrawl_client", return_value=firestore
        ) as create_firecrawl, patch(
            "openai.OpenAI", return_value=groq_context
        ), patch.object(
            knowledge_store, "save_text", side_effect=AssertionError("Preview must not save.")
        ) as save_text, patch.object(
            knowledge_store, "initialize_database", side_effect=AssertionError("Preview must not initialize the database.")
        ):
            response = self.client.post(
                "/api/knowledge/public-link/preview",
                headers=self.admin_headers,
                json={"url": "https://docs.example.com/process"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["title"], "Approval process")
        self.assertEqual(body["url"], "https://docs.example.com/process")
        self.assertEqual(body["source_url"], "https://docs.example.com/process")
        self.assertEqual(body["generated_content"], body["cleaned_content"])
        self.assertIn("Managers review each request", body["cleaned_content"])
        self.assertEqual(body["metadata"]["canonical_url"], "https://docs.example.com/process")
        create_firecrawl.assert_called_once_with("test-firecrawl-key")
        firestore.v1.scrape_url.assert_called_once_with(
            "https://docs.example.com/process",
            formats=["markdown"],
            only_main_content=True,
        )
        groq_client.chat.completions.with_raw_response.create.assert_not_called()
        self.assertEqual(body["metadata"]["generation_batch_count"], 0)
        self.assertEqual(body["metadata"]["raw_content_chars"], len(firestore.v1.scrape_url.return_value.markdown))
        self.assertEqual(body["metadata"]["content_chars"], len(body["cleaned_content"]))
        self.assertGreater(body["metadata"]["useful_content_chars"], 0)
        save_text.assert_not_called()
        self.assertEqual(knowledge_store.list_sources(), before)

    def test_save_persists_only_reviewed_text_with_public_link_metadata(self):
        edited_text = "# Admin-reviewed explanation\n\nOnly the edited version belongs in storage."
        extraction_metadata = {
            "canonical_url": "https://docs.example.com/canonical-process",
            "page_title": "Original page title",
            "description": "Approved page details.",
            "author": "Process Office",
            "published_at": "2026-09-10",
            "content_chars": 4200,
            "chunk_count": 4,
            "generation_batch_count": 0,
            "raw_content_chars": 5300,
            "useful_content_chars": 3500,
            "generation_metrics": {
                "source_chars": 4200,
                "call_count": 0,
                "outcome": "direct",
            },
        }
        with patch(
            "public_link_routes.socket.getaddrinfo", return_value=PUBLIC_DNS_RESULT
        ), patch(
            "public_link_routes.format_content",
            side_effect=AssertionError("Saving must not reformat reviewed content."),
        ) as generate:
            response = self.client.post(
                "/api/knowledge/public-link",
                headers=self.admin_headers,
                json={
                    "url": "https://docs.example.com/process",
                    "title": "Admin-approved title",
                    "text": edited_text,
                    "metadata": extraction_metadata,
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["source"]["source_type"], "Public Link")
        self.assertEqual(body["source"]["title"], "Admin-approved title")
        self.assertEqual(body["source"]["metadata"]["source_url"], "https://docs.example.com/process")
        self.assertEqual(body["source"]["metadata"]["canonical_url"], extraction_metadata["canonical_url"])
        self.assertEqual(body["source"]["metadata"]["page_title"], "Original page title")
        saved = knowledge_store.get_source(body["source"]["id"])
        self.assertEqual(saved["original_text"], edited_text)
        self.assertEqual(saved["metadata"]["source_url"], "https://docs.example.com/process")
        self.assertEqual(saved["metadata"]["raw_content_chars"], 5300)
        self.assertEqual(saved["metadata"]["useful_content_chars"], 3500)
        self.assertEqual(saved["metadata"]["generation_metrics"]["outcome"], "direct")
        self.assertIsNone(saved["metadata"]["generation_metrics"].get("actual_input_tokens"))
        with knowledge_store._connection() as connection:
            chunks = connection.execute(
                "SELECT content FROM knowledge_chunks WHERE source_id = ? ORDER BY chunk_index",
                (body["source"]["id"],),
            ).fetchall()
        self.assertEqual(
            [chunk["content"] for chunk in chunks],
            knowledge_store.chunk_text(edited_text),
        )
        self.assertTrue(knowledge_store.search_chunks("Admin-reviewed"))
        employee_response = self.client.post(
            "/api/knowledge/interview-context",
            headers={
                "Authorization": f"Bearer {create_access_token('employee@example.test', 'Employee')}",
            },
            json={"query": "Admin-reviewed"},
        )
        self.assertEqual(employee_response.status_code, 200, employee_response.text)
        self.assertEqual(employee_response.json()["chunks"][0]["source_id"], body["source"]["id"])
        self.assertIn(edited_text, employee_response.json()["chunks"][0]["content"])
        generate.assert_not_called()

    def test_existing_crud_keeps_public_link_type_and_metadata(self):
        with patch(
            "public_link_routes.socket.getaddrinfo", return_value=PUBLIC_DNS_RESULT
        ):
            saved_response = self.client.post(
                "/api/knowledge/public-link",
                headers=self.admin_headers,
                json={
                    "url": "https://docs.example.com/process",
                    "title": "Public Link source",
                    "text": "Initial reviewed process text.",
                    "metadata": {
                        "page_title": "Original page",
                        "description": "Source description",
                    },
                },
            )
        self.assertEqual(saved_response.status_code, 200, saved_response.text)
        source_id = saved_response.json()["source"]["id"]
        updated_text = "Admin revised the saved public link explanation."
        update_response = self.client.put(
            f"/api/knowledge/sources/{source_id}",
            headers=self.admin_headers,
            json={
                "title": "Updated Public Link",
                "text": updated_text,
                "notes": "Reviewed after saving.",
            },
        )
        self.assertEqual(update_response.status_code, 200, update_response.text)
        updated_source = update_response.json()["source"]
        self.assertEqual(updated_source["source_type"], "Public Link")
        self.assertEqual(updated_source["original_text"], updated_text)
        self.assertEqual(updated_source["metadata"]["source_url"], "https://docs.example.com/process")
        self.assertEqual(updated_source["metadata"]["page_title"], "Original page")
        self.assertTrue(knowledge_store.search_chunks("revised"))

        delete_response = self.client.delete(
            f"/api/knowledge/sources/{source_id}",
            headers=self.admin_headers,
        )
        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(knowledge_store.search_chunks("revised"), [])

    def test_save_requires_admin_authentication_and_rejects_employee(self):
        payload = {
            "url": "https://docs.example.com/process",
            "title": "Reviewed source",
            "text": "Reviewed content.",
        }
        with patch("public_link_routes.socket.getaddrinfo", return_value=PUBLIC_DNS_RESULT):
            unauthorized = self.client.post("/api/knowledge/public-link", json=payload)
            employee = self.client.post(
                "/api/knowledge/public-link",
                headers={
                    "Authorization": f"Bearer {create_access_token('employee@example.test', 'Employee')}"
                },
                json=payload,
            )
        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(employee.status_code, 403)
        self.assertEqual(knowledge_store.list_sources(), [])

    def test_save_rejects_blank_content_and_invalid_urls_without_writing(self):
        payload = {
            "url": "https://docs.example.com/process",
            "title": "Reviewed source",
            "text": " \n ",
        }
        with patch("public_link_routes.socket.getaddrinfo", return_value=PUBLIC_DNS_RESULT):
            blank_content = self.client.post(
                "/api/knowledge/public-link",
                headers=self.admin_headers,
                json=payload,
            )
            invalid_url = self.client.post(
                "/api/knowledge/public-link",
                headers=self.admin_headers,
                json={**payload, "text": "Reviewed content.", "url": "http://127.0.0.1/"},
            )
        self.assertEqual(blank_content.status_code, 422)
        self.assertEqual(invalid_url.status_code, 422)
        self.assertEqual(knowledge_store.list_sources(), [])

    def test_short_pages_skip_groq_without_model_configuration_or_expansion(self):
        for markdown in (
            "# Example Domain\nThis domain is for documentation examples.",
            "# प्रक्रिया\nकर्मचारी अनुरोध भेजता है।",
            "x" * public_link_routes.DIRECT_REVIEW_CHARS,
        ):
            with self.subTest(markdown=markdown[:40]), patch.dict(os.environ, {
                "GROQ_API_KEY": "", "GROQ_TEXT_MODEL": "",
                "GROQ_TEXT_MAX_COMPLETION_TOKENS": "invalid-but-unused",
            }):
                response, factory, completion = self.preview_markdown(markdown)
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertEqual(body["generated_content"], body["cleaned_content"])
                self.assertEqual(body["generated_content"], public_link_routes.clean_markdown(markdown))
                self.assertEqual(body["metadata"]["generation_batch_count"], 0)
                metrics = body["metadata"]["generation_metrics"]
                self.assertEqual(metrics["call_count"], 0)
                self.assertEqual(metrics["source_chars"], len(body["cleaned_content"]))
                self.assertEqual(metrics["submitted_chars"], 0)
                self.assertEqual(metrics["latency_ms"], 0)
                self.assertEqual(metrics["usage_status"], "not_requested")
                self.assertIsNone(metrics["actual_input_tokens"])
                self.assertIsNone(metrics["estimated_input_tokens"])
                factory.assert_not_called()
                completion.assert_not_called()

    def test_nested_firecrawl_markdown_and_metadata_are_used_without_html_fallback(self):
        markdown = "# API\n\nUse [the endpoint](https://example.com/v2) with timeout 300 ms."
        result = {
            "success": True,
            "data": {
                "markdown": markdown,
                "html": "<nav>Unrelated navigation</nav>",
                "rawHtml": "<html>Unrelated raw page</html>",
                "metadata": {"title": "API docs", "statusCode": 200, "author": "Docs team"},
            },
        }
        response, factory, completion = self.preview_markdown(markdown, scraped_result=result)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["title"], "API docs")
        self.assertEqual(body["metadata"]["author"], "Docs team")
        self.assertEqual(body["generated_content"], markdown)
        factory.assert_not_called()
        completion.assert_not_called()

    def test_extraction_metrics_measure_raw_and_cleaned_content(self):
        markdown = "<nav>[Home](/)</nav>\n# API\n\nCall /v2 with timeout 300 ms."
        with self.assertLogs("public_link_routes", level="INFO") as logs:
            response, factory, completion = self.preview_markdown(markdown)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["metadata"]["raw_content_chars"], len(markdown))
        self.assertEqual(body["metadata"]["content_chars"], len(body["cleaned_content"]))
        self.assertLess(body["metadata"]["content_chars"], body["metadata"]["raw_content_chars"])
        self.assertEqual(
            body["metadata"]["useful_content_chars"], len("Call/v2withtimeout300ms."),
        )
        self.assertIn(f"raw_chars={len(markdown)}", "\n".join(logs.output))
        self.assertNotIn("Call /v2", "\n".join(logs.output))
        factory.assert_not_called()
        completion.assert_not_called()

    def test_empty_or_unusable_extraction_fails_before_groq(self):
        for markdown in (
            None, "", " \n\t",
            "<script>track()</script><nav>[Home](/)</nav>",
            "## Navigation\n[Home](/)\n[About](/about)\nAccept all cookies",
            "[Home](/)\n[About us](/about)\n[Contact](/contact)",
            "# Title only",
            "# Access denied\nYou don't have permission to access this resource.",
            "# Just a moment...\nChecking your browser...",
            "----\n***",
        ):
            with self.subTest(markdown=markdown), self.assertLogs(
                "public_link_routes", level="WARNING"
            ):
                response, factory, completion = self.preview_markdown(markdown)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertIn("content URL", response.json()["detail"])
                self.assertNotIn("generated_content", response.json())
                factory.assert_not_called()
                completion.assert_not_called()

    def test_provider_error_page_is_not_treated_as_knowledge(self):
        result = {
            "markdown": "# Documentation\nA proxy-generated page with generic text.",
            "metadata": {"statusCode": 403},
        }
        with self.assertLogs("public_link_routes", level="WARNING") as logs:
            response, factory, completion = self.preview_markdown("", scraped_result=result)
        self.assertEqual(response.status_code, 422)
        self.assertIn("publicly accessible", response.json()["detail"])
        self.assertIn("category=page_http_error", "\n".join(logs.output))
        factory.assert_not_called()
        completion.assert_not_called()

    def test_missing_markdown_does_not_fall_back_to_html(self):
        with self.assertLogs("public_link_routes", level="WARNING"):
            response, factory, completion = self.preview_markdown(
                None, scraped_result={"html": "<p>Not the requested Markdown format.</p>"},
            )
        self.assertEqual(response.status_code, 422)
        self.assertIn("no readable Markdown", response.json()["detail"])
        factory.assert_not_called()
        completion.assert_not_called()

    def test_excessive_cleaning_returns_actionable_error_instead_of_small_success(self):
        markdown = "<nav>" + "[Home](/) " * 1000 + "</nav>\n# API\nUse port 443."
        with self.assertLogs("public_link_routes", level="WARNING") as logs:
            response, factory, completion = self.preview_markdown(markdown)
        self.assertEqual(response.status_code, 422)
        self.assertIn("lost too much", response.json()["detail"])
        self.assertIn("category=excessive_cleaning", "\n".join(logs.output))
        factory.assert_not_called()
        completion.assert_not_called()

    def test_accidental_cleaner_over_removal_is_not_success(self):
        markdown = "# API\n" + "Meaningful source details. " * 500
        with patch("public_link_routes.clean_markdown", return_value="# API\nUse port 443."), self.assertLogs(
            "public_link_routes", level="WARNING"
        ):
            response, factory, completion = self.preview_markdown(markdown)
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("generated_content", response.json())
        factory.assert_not_called()
        completion.assert_not_called()

    def test_documentation_about_access_errors_and_privacy_is_not_blocked(self):
        markdown = (
            "# Access denied\nThe API returns 403 when the token is expired.\n\n"
            "## Privacy policy\nPrivacy policy APIs return HTTP 200 and a revision number."
        )
        response, factory, completion = self.preview_markdown(markdown)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["generated_content"], markdown)
        factory.assert_not_called()
        completion.assert_not_called()

    def test_long_readable_page_keeps_all_content_without_groq(self):
        markdown = "\n".join(f"Step {index}: Managers review requests." for index in range(1000))
        response, factory, completion = self.preview_markdown(markdown)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["generated_content"], markdown)
        factory.assert_not_called()
        completion.assert_not_called()

    def test_readable_page_at_cleaned_limit_is_not_truncated(self):
        markdown = "\n".join(
            f"{index:03d}" + "x" * (997 if index == 0 else 996)
            for index in range(200)
        )
        self.assertEqual(len(markdown), public_link_routes.MAX_SOURCE_CHARS)
        response, factory, completion = self.preview_markdown(markdown)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["generated_content"], markdown)
        factory.assert_not_called()
        completion.assert_not_called()


    def test_structured_dense_source_returns_unchanged_with_zero_calls(self):
        markdown = "# Example\n\n```text\n" + "Technical example with port 443. " * 240 + "\n```"
        response, factory, completion = self.preview_markdown(markdown)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["generated_content"], markdown)
        self.assertEqual(response.json()["metadata"]["generation_batch_count"], 0)
        self.assertEqual(response.json()["metadata"]["generation_metrics"]["outcome"], "structured_source")
        factory.assert_not_called()
        completion.assert_not_called()

    def assert_no_generation(self, body):
        self.assertEqual(body["metadata"]["generation_batch_count"], 0)
        metrics = body["metadata"]["generation_metrics"]
        self.assertEqual(metrics["call_count"], 0)
        self.assertEqual(metrics["submitted_chars"], 0)
        self.assertEqual(metrics["source_chars"], len(body["cleaned_content"]))
        self.assertEqual(metrics["usage_status"], "not_requested")
        self.assertEqual(metrics["latency_ms"], 0)
        for field in (
            "estimated_input_tokens", "input_token_budget", "max_completion_tokens",
            "actual_input_tokens", "actual_output_tokens", "actual_total_tokens",
        ):
            self.assertIsNone(metrics[field])

    def test_dense_plain_page_formats_locally_with_truthful_metadata(self):
        markdown = ("Managers review requests. " * 300).strip()
        response, factory, completion = self.preview_markdown(markdown)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["cleaned_content"], markdown)
        self.assertEqual(body["generated_content"].replace("\n", " "), markdown)
        self.assertNotEqual(body["generated_content"], markdown)
        self.assertEqual(body["metadata"]["generation_metrics"]["outcome"], "deterministic_formatted")
        self.assert_no_generation(body)
        factory.assert_not_called()
        completion.assert_not_called()

    def test_structured_pages_above_old_generation_limit_preserve_long_lines(self):
        for markdown in (
            "# Code\n\n```python\nvalue = '" + "x" * 20_000 + "'\n```",
            "# Table\n\n| Name | Value |\n| --- | --- |\n| Data | " + "x" * 20_000 + " |",
            "# Steps\n\n1. " + "Open the valve and record the result. " * 600,
        ):
            with self.subTest(prefix=markdown[:20]):
                response, factory, completion = self.preview_markdown(markdown)
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertEqual(body["cleaned_content"], markdown.rstrip())
                self.assertEqual(body["generated_content"], body["cleaned_content"])
                self.assertEqual(body["metadata"]["generation_metrics"]["outcome"], "structured_source")
                self.assert_no_generation(body)
                factory.assert_not_called()
                completion.assert_not_called()

    def test_structured_page_at_overall_limit_is_preserved_and_next_character_rejected(self):
        prefix, suffix = "# Code\n\n```text\n", "\n```"
        markdown = prefix + "x" * (public_link_routes.MAX_SOURCE_CHARS - len(prefix) - len(suffix)) + suffix
        response, factory, completion = self.preview_markdown(markdown)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["generated_content"], markdown)
        self.assert_no_generation(response.json())
        factory.assert_not_called()
        completion.assert_not_called()
        oversized = markdown.replace("\n```", "x\n```")
        response, factory, completion = self.preview_markdown(oversized)
        self.assertEqual(response.status_code, 413)
        self.assertNotIn("generated_content", response.json())
        factory.assert_not_called()
        completion.assert_not_called()

    def test_plain_dense_content_above_old_generation_limit_needs_no_token_budget(self):
        for markdown in (
            "x" * 18_501,
            "界" * 7000,
            ("Managers review requests. " * 900).strip(),
        ):
            with self.subTest(size=len(markdown)):
                response, factory, completion = self.preview_markdown(markdown)
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertEqual(
                    public_link_routes.WHITESPACE.sub(" ", body["generated_content"]),
                    public_link_routes.WHITESPACE.sub(" ", body["cleaned_content"]),
                )
                self.assert_no_generation(body)
                factory.assert_not_called()
                completion.assert_not_called()

    def test_dense_content_does_not_read_groq_generation_settings(self):
        original_getenv = os.getenv

        def guarded_getenv(name, *args):
            if name in {"GROQ_API_KEY", "GROQ_TEXT_MODEL", "GROQ_TEXT_MAX_COMPLETION_TOKENS"}:
                raise AssertionError("Public Link must not read generation configuration.")
            return original_getenv(name, *args)

        with patch("public_link_routes.os.getenv", side_effect=guarded_getenv):
            response, factory, completion = self.preview_markdown(("Managers approve requests. " * 300).strip())
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_no_generation(response.json())
        factory.assert_not_called()
        completion.assert_not_called()

    def test_formatted_and_large_structured_previews_save_without_reprocessing(self):
        for markdown in (
            ("Managers review requests and record approvals. " * 200).strip(),
            "# API\n\n```text\n" + "x" * 20_000 + "\n```",
        ):
            with self.subTest(prefix=markdown[:20]):
                response, factory, completion = self.preview_markdown(markdown)
                self.assertEqual(response.status_code, 200, response.text)
                preview = response.json()
                with patch(
                    "public_link_routes.socket.getaddrinfo", return_value=PUBLIC_DNS_RESULT
                ), patch(
                    "public_link_routes.format_content", side_effect=AssertionError("Save must not reformat.")
                ), patch("openai.OpenAI") as groq:
                    saved_response = self.client.post(
                        "/api/knowledge/public-link",
                        headers=self.admin_headers,
                        json={
                            "url": preview["source_url"],
                            "title": preview["title"],
                            "text": preview["generated_content"],
                            "metadata": preview["metadata"],
                        },
                    )
                self.assertEqual(saved_response.status_code, 200, saved_response.text)
                saved = knowledge_store.get_source(saved_response.json()["source"]["id"])
                self.assertEqual(saved["original_text"], preview["generated_content"])
                self.assertEqual(saved["source_type"], "Public Link")
                self.assertEqual(
                    public_link_routes.GenerationMetrics.model_validate(
                        saved["metadata"]["generation_metrics"]
                    ).model_dump(),
                    preview["metadata"]["generation_metrics"],
                )
                self.assertEqual(saved["metadata"]["generation_batch_count"], 0)
                self.assertTrue(knowledge_store.search_chunks(
                    "Managers" if markdown.startswith("Managers") else "API"
                ))
                factory.assert_not_called()
                completion.assert_not_called()
                groq.assert_not_called()

    def test_raw_extraction_exact_limit_is_allowed_and_next_character_rejected(self):
        source = ("Managers review requests and record approvals. " * 10).strip()
        comment_size = public_link_routes.MAX_SOURCE_CHARS * 4 - len(source) - len("<!---->\n")
        markdown = "<!--" + "x" * comment_size + "-->\n" + source
        self.assertEqual(len(markdown), public_link_routes.MAX_SOURCE_CHARS * 4)
        response, factory, completion = self.preview_markdown(markdown)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["generated_content"], source)
        self.assert_no_generation(response.json())
        factory.assert_not_called()
        completion.assert_not_called()
        response, factory, completion = self.preview_markdown(markdown + "x")
        self.assertEqual(response.status_code, 413)
        factory.assert_not_called()
        completion.assert_not_called()

    def test_scrape_failure_remains_sanitized_without_groq_or_storage(self):
        firecrawl = MagicMock()
        firecrawl.v1.scrape_url.side_effect = RuntimeError("private-firecrawl-key page text")
        with patch(
            "public_link_routes._create_firecrawl_client", return_value=firecrawl
        ), patch(
            "public_link_routes.resolve_public_redirects", return_value="https://example.com/"
        ), patch(
            "public_link_routes.socket.getaddrinfo", return_value=PUBLIC_DNS_RESULT
        ), patch(
            "openai.OpenAI"
        ) as groq, patch.object(knowledge_store, "save_text") as save:
            response = self.client.post(
                "/api/knowledge/public-link/preview",
                headers=self.admin_headers,
                json={"url": "https://example.com/"},
            )
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("private-firecrawl-key", response.text)
        self.assertNotIn("page text", response.text)
        groq.assert_not_called()
        save.assert_not_called()
    def test_oversized_content_is_rejected_without_truncation_or_groq(self):
        for markdown in (
            "x" * (public_link_routes.MAX_SOURCE_CHARS + 1),
            "x" * (public_link_routes.MAX_SOURCE_CHARS * 4 + 1),
        ):
            with self.subTest(size=len(markdown)):
                response, factory, completion = self.preview_markdown(markdown)
                self.assertEqual(response.status_code, 413, response.text)
                self.assertIn("more specific page", response.json()["detail"])
                self.assertNotIn("generated_content", response.json())
                factory.assert_not_called()
                completion.assert_not_called()
if __name__ == "__main__":
    unittest.main()

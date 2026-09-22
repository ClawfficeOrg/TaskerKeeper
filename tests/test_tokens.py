"""Tests for per-project machine tokens (task 1.4.1).

Stdlib unittest on purpose: `python -m unittest` works in a bare checkout.
"""

from __future__ import annotations

import os
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from taskerkeeper import serve, tokens


class MintVerifyTest(unittest.TestCase):
    def test_mint_then_verify_ok(self):
        with TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "tokens.db")
            prefix, presented, stored = tokens.mint_token(
                db, ["demo"], ["read", "write"])
            self.assertTrue(prefix)
            self.assertIn(prefix, presented)
            result = tokens.verify_token(db, presented)
            assert result is not None
            slugs, scopes = result
            self.assertEqual(slugs, ["demo"])
            self.assertEqual(scopes, ["read", "write"])

    def test_secret_never_stored_plaintext(self):
        with TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "tokens.db")
            _, presented, stored = tokens.mint_token(db, ["demo"], ["read"])
            conn = sqlite3.connect(db)
            try:
                (db_hash,) = conn.execute(
                    "SELECT hash FROM machine_tokens").fetchone()
            finally:
                conn.close()
            self.assertEqual(db_hash, stored)
            self.assertNotIn(presented, db_hash)
            self.assertNotIn(presented.split(".")[-1], db_hash)

    def test_unknown_prefix_refused(self):
        with TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "tokens.db")
            tokens.mint_token(db, ["demo"], ["read"])
            self.assertIsNone(tokens.verify_token(db, "tk_deadbeef." + "0" * 48))

    def test_tampered_secret_refused(self):
        with TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "tokens.db")
            _, presented, _ = tokens.mint_token(db, ["demo"], ["read"])
            head, _, _ = presented.rpartition(".")
            self.assertIsNone(tokens.verify_token(db, head + "." + "f" * 48))

    def test_malformed_refused(self):
        with TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "tokens.db")
            for bad in ("", "not-a-token", "tk_nodot"):
                self.assertIsNone(tokens.verify_token(db, bad))

    def test_missing_db_verifies_nothing(self):
        with TemporaryDirectory() as tmp:
            self.assertIsNone(tokens.verify_token(
                str(Path(tmp) / "nope.db"), "tk_abcd1234." + "0" * 48))


class ScopeSlugGateTest(unittest.TestCase):
    def test_wrong_slug_refused(self):
        with TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "tokens.db")
            _, presented, _ = tokens.mint_token(db, ["demo"], ["read", "write"])
            self.assertIsNone(tokens.authorize(db, presented, slug="other"))
            ok = tokens.authorize(db, presented, slug="demo")
            assert ok is not None

    def test_wrong_scope_refused(self):
        with TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "tokens.db")
            _, presented, _ = tokens.mint_token(db, ["demo"], ["read"])
            self.assertIsNone(tokens.authorize(db, presented, scope="write"))
            ok = tokens.authorize(db, presented, scope="read")
            assert ok is not None

    def test_wildcard_scope_passes(self):
        with TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "tokens.db")
            _, presented, _ = tokens.mint_token(db, ["demo"], ["*"])
            ok = tokens.authorize(db, presented, slug="demo", scope="write")
            assert ok is not None

    def test_mint_requires_slugs_and_scopes(self):
        with TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "tokens.db")
            with self.assertRaises(ValueError):
                tokens.mint_token(db, [], ["read"])
            with self.assertRaises(ValueError):
                tokens.mint_token(db, ["demo"], [])


class RevokeExpiryTest(unittest.TestCase):
    def test_revoked_refused(self):
        with TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "tokens.db")
            prefix, presented, _ = tokens.mint_token(db, ["demo"], ["read"])
            self.assertTrue(tokens.revoke_token(db, prefix))
            self.assertIsNone(tokens.verify_token(db, presented))

    def test_expired_refused(self):
        with TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "tokens.db")
            _, presented, _ = tokens.mint_token(
                db, ["demo"], ["read"], expires_days=-1)
            self.assertIsNone(tokens.verify_token(db, presented))

    def test_no_expiry_verifies(self):
        with TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "tokens.db")
            _, presented, _ = tokens.mint_token(
                db, ["demo"], ["read"], expires_days=None)
            self.assertIsNotNone(tokens.verify_token(db, presented))


class ServeWiringTest(unittest.TestCase):
    def test_check_bearer_machine_token_first(self):
        with TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "tokens.db")
            _, presented, _ = tokens.mint_token(db, ["demo"], ["read"])
            headers = {"Authorization": f"Bearer {presented}"}
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("TK_API_TOKEN", None)
                # No shared token configured: machine token alone passes.
                self.assertTrue(serve.check_bearer(headers, db))

    def test_check_bearer_revoked_falls_back_to_401(self):
        with TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "tokens.db")
            prefix, presented, _ = tokens.mint_token(db, ["demo"], ["read"])
            tokens.revoke_token(db, prefix)
            headers = {"Authorization": f"Bearer {presented}"}
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("TK_API_TOKEN", None)
                self.assertFalse(serve.check_bearer(headers, db))

    def test_check_bearer_falls_back_to_shared_token(self):
        with TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "tokens.db")
            headers = {"Authorization": "Bearer shared-secret"}
            with mock.patch.dict(os.environ, {"TK_API_TOKEN": "shared-secret"}):
                self.assertTrue(serve.check_bearer(headers, db))
                self.assertFalse(
                    serve.check_bearer({"Authorization": "Bearer wrong"}, db))

    def test_bearer_auth_forbids_foreign_slug(self):
        with TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "tokens.db")
            registry = str(Path(tmp) / "registry.json")
            Path(registry).write_text('{"projects": []}', encoding="utf-8")
            _, presented, _ = tokens.mint_token(
                db, ["demo"], ["read", "write"])
            headers = {"Authorization": f"Bearer {presented}"}
            ok, forbidden = serve.bearer_auth(
                headers, slug="other", scope="write",
                registry=registry, tokens_db=db)
            self.assertFalse(ok)
            self.assertTrue(forbidden)
            ok, _ = serve.bearer_auth(
                headers, slug="demo", scope="write",
                registry=registry, tokens_db=db)
            self.assertTrue(ok)

    def test_bearer_auth_forbids_missing_scope(self):
        with TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "tokens.db")
            registry = str(Path(tmp) / "registry.json")
            Path(registry).write_text('{"projects": []}', encoding="utf-8")
            _, presented, _ = tokens.mint_token(db, ["demo"], ["read"])
            headers = {"Authorization": f"Bearer {presented}"}
            ok, forbidden = serve.bearer_auth(
                headers, slug="demo", scope="write",
                registry=registry, tokens_db=db)
            self.assertFalse(ok)
            self.assertTrue(forbidden)

    def test_tokens_db_defaults_beside_registry(self):
        self.assertTrue(
            serve.tokens_db_for_registry("deploy/registry.json").endswith(
                "tokens.db"))
        with mock.patch.dict(os.environ, {"TK_TOKENS_DB": "/srv/t.db"}):
            self.assertEqual(serve.tokens_db_for_registry("deploy/registry.json"),
                             "/srv/t.db")


if __name__ == "__main__":
    unittest.main()

"""SQLite-backed repository: the single place pipeline code and the Flask app
read and write opportunities, contacts, users, drafts, and outreach history.

Callers work with plain dicts; SQL, schema, and encryption details stay here.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from internship_agent.crypto import SecretBox
from internship_agent.db import Database
from internship_agent.text_utils import company_key as make_company_key
from internship_agent.text_utils import role_key as make_role_key


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _rows(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


class Repository:
    def __init__(self, db: Database, secret_box: SecretBox | None = None) -> None:
        self.db = db
        self._secrets = secret_box or SecretBox()

    # ---- opportunities ---------------------------------------------------

    def upsert_opportunities(self, items: list[dict[str, Any]]) -> int:
        """Insert opportunities not already known by (company, role). Returns
        the count of newly inserted rows."""
        inserted = 0
        with self.db.cursor() as cur:
            for item in items:
                ck = make_company_key(item.get("company"))
                rk = make_role_key(item.get("role"))
                if not ck or not rk:
                    continue
                cur.execute(
                    """
                    INSERT INTO opportunities
                        (company, company_key, role, role_key, description, location,
                         official_url, source_url, evidence, confidence, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(company_key, role_key) DO NOTHING
                    """,
                    (
                        item.get("company"),
                        ck,
                        item.get("role"),
                        rk,
                        item.get("description"),
                        item.get("location"),
                        item.get("official_url"),
                        item.get("source_url"),
                        item.get("evidence"),
                        item.get("confidence"),
                        _now(),
                    ),
                )
                if cur.rowcount:
                    inserted += 1
        return inserted

    def list_opportunities(self, limit: int | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM opportunities ORDER BY id DESC"
        params: tuple[Any, ...] = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (limit,)
        with self.db.cursor() as cur:
            cur.execute(query, params)
            return _rows(cur.fetchall())

    def count_opportunities(self) -> int:
        with self.db.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM opportunities")
            return cur.fetchone()["n"]

    # ---- contacts ----------------------------------------------------------

    def get_contact(self, company: str | None, role: str | None) -> dict[str, Any] | None:
        with self.db.cursor() as cur:
            cur.execute(
                "SELECT * FROM contacts WHERE company_key = ? AND role = ?",
                (make_company_key(company), role),
            )
            return _row(cur.fetchone())

    def upsert_contact(self, contact: dict[str, Any]) -> None:
        ck = make_company_key(contact.get("company"))
        with self.db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO contacts
                    (company, company_key, role, description, location, official_url,
                     source_url, evidence, confidence, domain, contact_name,
                     contact_position, email, contact_source, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(company_key, role) DO UPDATE SET
                    domain = excluded.domain,
                    contact_name = excluded.contact_name,
                    contact_position = excluded.contact_position,
                    email = excluded.email,
                    contact_source = excluded.contact_source,
                    updated_at = excluded.updated_at
                """,
                (
                    contact.get("company"),
                    ck,
                    contact.get("role"),
                    contact.get("description"),
                    contact.get("location"),
                    contact.get("official_url"),
                    contact.get("source_url"),
                    contact.get("evidence"),
                    contact.get("confidence"),
                    contact.get("domain"),
                    contact.get("contact_name"),
                    contact.get("contact_position"),
                    contact.get("email"),
                    contact.get("contact_source"),
                    _now(),
                ),
            )

    def list_contacts(self) -> list[dict[str, Any]]:
        with self.db.cursor() as cur:
            cur.execute("SELECT * FROM contacts ORDER BY id DESC")
            return _rows(cur.fetchall())

    def count_contacts(self) -> int:
        with self.db.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM contacts")
            return cur.fetchone()["n"]

    def contact_company_names(self) -> list[str]:
        with self.db.cursor() as cur:
            cur.execute("SELECT DISTINCT company FROM contacts")
            return [row["company"] for row in cur.fetchall()]

    # ---- users / BYO keys ---------------------------------------------------

    def get_user(self, email: str) -> dict[str, Any] | None:
        with self.db.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE email = ?", (email,))
            row = cur.fetchone()
        if row is None:
            return None
        data = dict(row)
        data["gemini_api_key"] = self._secrets.decrypt(data.pop("gemini_api_key_enc"))
        data["tavily_api_key"] = self._secrets.decrypt(data.pop("tavily_api_key_enc"))
        data["hunter_api_key"] = self._secrets.decrypt(data.pop("hunter_api_key_enc"))
        data["search_locations"] = json.loads(data["search_locations"] or "[]")
        data["search_roles"] = json.loads(data["search_roles"] or "[]")
        return data

    @staticmethod
    def _ensure_user(cur: sqlite3.Cursor, email: str, user_key: str) -> None:
        cur.execute(
            "INSERT INTO users (email, user_key, created_at, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(email) DO NOTHING",
            (email, user_key, _now(), _now()),
        )

    def save_resume_path(self, email: str, user_key: str, resume_path: str) -> None:
        with self.db.cursor() as cur:
            self._ensure_user(cur, email, user_key)
            cur.execute(
                "UPDATE users SET resume_path = ?, updated_at = ? WHERE email = ?",
                (resume_path, _now(), email),
            )

    def save_search_prefs(
        self, email: str, user_key: str, *, locations: list[str], roles: list[str]
    ) -> None:
        with self.db.cursor() as cur:
            self._ensure_user(cur, email, user_key)
            cur.execute(
                "UPDATE users SET search_locations = ?, search_roles = ?, updated_at = ? WHERE email = ?",
                (json.dumps(locations), json.dumps(roles), _now(), email),
            )

    def save_api_keys(
        self,
        email: str,
        user_key: str,
        *,
        gemini_api_key: str | None = None,
        tavily_api_key: str | None = None,
        hunter_api_key: str | None = None,
    ) -> None:
        updates: list[str] = []
        params: list[Any] = []
        for value, column in (
            (gemini_api_key, "gemini_api_key_enc"),
            (tavily_api_key, "tavily_api_key_enc"),
            (hunter_api_key, "hunter_api_key_enc"),
        ):
            if value:
                updates.append(f"{column} = ?")
                params.append(self._secrets.encrypt(value))
        if not updates:
            return
        updates.append("updated_at = ?")
        params.append(_now())
        params.append(email)
        with self.db.cursor() as cur:
            self._ensure_user(cur, email, user_key)
            cur.execute(f"UPDATE users SET {', '.join(updates)} WHERE email = ?", params)

    def get_gmail_token(self, email: str) -> dict[str, Any] | None:
        with self.db.cursor() as cur:
            cur.execute("SELECT gmail_oauth_token_enc FROM users WHERE email = ?", (email,))
            row = cur.fetchone()
        if row is None or row["gmail_oauth_token_enc"] is None:
            return None
        decrypted = self._secrets.decrypt(row["gmail_oauth_token_enc"])
        return json.loads(decrypted) if decrypted else None

    def save_gmail_token(self, email: str, user_key: str, token: dict[str, Any]) -> None:
        encrypted = self._secrets.encrypt(json.dumps(token))
        with self.db.cursor() as cur:
            self._ensure_user(cur, email, user_key)
            cur.execute(
                "UPDATE users SET gmail_oauth_token_enc = ?, updated_at = ? WHERE email = ?",
                (encrypted, _now(), email),
            )

    def clear_gmail_token(self, email: str) -> None:
        with self.db.cursor() as cur:
            cur.execute(
                "UPDATE users SET gmail_oauth_token_enc = NULL, updated_at = ? WHERE email = ?",
                (_now(), email),
            )

    # ---- drafts --------------------------------------------------------------

    @staticmethod
    def _with_to_alias(row: dict[str, Any]) -> dict[str, Any]:
        row["to"] = row.pop("to_email")
        return row

    def add_draft(self, user_email: str, draft: dict[str, Any]) -> int:
        with self.db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO drafts
                    (user_email, company, company_key, role, recipient_name, to_email,
                     subject, body, status, resume_path, source_url, contact_source,
                     gmail_message_id, fit_score, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_email,
                    draft.get("company"),
                    make_company_key(draft.get("company")),
                    draft.get("role"),
                    draft.get("recipient_name"),
                    draft.get("to"),
                    draft.get("subject"),
                    draft.get("body"),
                    draft.get("status", "pending_approval"),
                    draft.get("resume_path"),
                    draft.get("source_url"),
                    draft.get("contact_source"),
                    draft.get("gmail_message_id"),
                    draft.get("fit_score"),
                    _now(),
                    _now(),
                ),
            )
            assert cur.lastrowid is not None
            return cur.lastrowid

    def training_examples(self, user_email: str) -> list[dict[str, Any]]:
        """Past drafts with a decided outcome, joined with the opportunity/
        contact fields `ranking.extract_features` needs.

        One query per draft rather than a single join: role_key normalization
        happens in Python (text_utils.role_key), not SQL, and a user's total
        draft history is small enough (tens to low hundreds) that this is
        simpler and fine.
        """
        with self.db.cursor() as cur:
            cur.execute(
                "SELECT role, company_key, status FROM drafts "
                "WHERE user_email = ? AND status IN ('sent', 'skipped', 'removed')",
                (user_email,),
            )
            decided = _rows(cur.fetchall())

        examples = []
        for row in decided:
            # get_contact() re-derives company_key from a company *name* via
            # make_company_key, which is idempotent — passing an already-
            # normalized key through it again is a no-op, so this is safe.
            contact = self.get_contact(row["company_key"], row["role"])
            opp = self._opportunity_by_key(row["company_key"], row["role"])
            examples.append(
                {
                    "role": row["role"],
                    "description": (opp or {}).get("description") or "",
                    "confidence": (opp or {}).get("confidence"),
                    "contact_source": (contact or {}).get("contact_source") or "",
                    "label": 1 if row["status"] == "sent" else 0,
                }
            )
        return examples

    def _opportunity_by_key(self, company_key: str, role: str) -> dict[str, Any] | None:
        with self.db.cursor() as cur:
            cur.execute(
                "SELECT * FROM opportunities WHERE company_key = ? AND role_key = ?",
                (company_key, make_role_key(role)),
            )
            return _row(cur.fetchone())

    def list_drafts(
        self, user_email: str, *, exclude_removed: bool = True
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM drafts WHERE user_email = ?"
        params: list[Any] = [user_email]
        if exclude_removed:
            query += " AND status != 'removed'"
        query += " ORDER BY id ASC"
        with self.db.cursor() as cur:
            cur.execute(query, params)
            rows = _rows(cur.fetchall())
        return [self._with_to_alias(row) for row in rows]

    def get_draft(self, user_email: str, draft_id: int) -> dict[str, Any] | None:
        with self.db.cursor() as cur:
            cur.execute(
                "SELECT * FROM drafts WHERE id = ? AND user_email = ?",
                (draft_id, user_email),
            )
            row = _row(cur.fetchone())
        return self._with_to_alias(row) if row is not None else None

    def existing_draft_company_keys(self, user_email: str) -> set[str]:
        with self.db.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT company_key FROM drafts WHERE user_email = ? AND status != 'removed'",
                (user_email,),
            )
            return {row["company_key"] for row in cur.fetchall()}

    def update_draft_status(
        self,
        user_email: str,
        draft_id: int,
        status: str,
        *,
        gmail_message_id: str | None = None,
    ) -> dict[str, Any] | None:
        with self.db.cursor() as cur:
            if gmail_message_id is not None:
                cur.execute(
                    "UPDATE drafts SET status = ?, gmail_message_id = ?, updated_at = ? "
                    "WHERE id = ? AND user_email = ?",
                    (status, gmail_message_id, _now(), draft_id, user_email),
                )
            else:
                cur.execute(
                    "UPDATE drafts SET status = ?, updated_at = ? WHERE id = ? AND user_email = ?",
                    (status, _now(), draft_id, user_email),
                )
        return self.get_draft(user_email, draft_id)

    # ---- company history (per-user outreach memory) --------------------------

    def history(self, user_email: str) -> list[dict[str, Any]]:
        with self.db.cursor() as cur:
            cur.execute(
                "SELECT * FROM company_history WHERE user_email = ? ORDER BY updated_at DESC",
                (user_email,),
            )
            return _rows(cur.fetchall())

    def history_company_keys(self, user_email: str) -> set[str]:
        with self.db.cursor() as cur:
            cur.execute(
                "SELECT company_key FROM company_history WHERE user_email = ?",
                (user_email,),
            )
            return {row["company_key"] for row in cur.fetchall()}

    def remember_company(self, user_email: str, draft: dict[str, Any], status: str) -> None:
        ck = make_company_key(draft.get("company"))
        if not ck:
            return
        with self.db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO company_history
                    (user_email, company_key, company, role, to_email, subject, status,
                     gmail_message_id, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_email, company_key) DO UPDATE SET
                    role = excluded.role,
                    to_email = excluded.to_email,
                    subject = excluded.subject,
                    status = excluded.status,
                    gmail_message_id = COALESCE(excluded.gmail_message_id, company_history.gmail_message_id),
                    updated_at = excluded.updated_at
                """,
                (
                    user_email,
                    ck,
                    draft.get("company"),
                    draft.get("role"),
                    draft.get("to", ""),
                    draft.get("subject", ""),
                    status,
                    draft.get("gmail_message_id"),
                    _now(),
                ),
            )

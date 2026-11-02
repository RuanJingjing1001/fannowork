from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any


def canonical_json(value: object) -> str:
    """Produce stable JSON for storage and later hashing."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class StateDatabase:
    """Persistent business state for one environment instance."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        """Create the minimal database schema."""
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS collections (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE
                );

                CREATE TABLE IF NOT EXISTS records (
                    collection_id TEXT NOT NULL,
                    id TEXT NOT NULL,
                    fields_json TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    PRIMARY KEY (collection_id, id),
                    FOREIGN KEY (collection_id)
                        REFERENCES collections(id)
                        ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS policies (
                    actor TEXT NOT NULL,
                    collection_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    allowed_fields_json TEXT NOT NULL,
                    PRIMARY KEY (
                        actor,
                        collection_id,
                        operation   
                    ),
                    FOREIGN KEY (collection_id)
                        REFERENCES collections(id)
                        ON DELETE CASCADE
);

                CREATE TABLE IF NOT EXISTS seed_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    payload_json TEXT NOT NULL
                );
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO metadata(key, value)
                VALUES ('state_version', '0')
                """
            )

    def seed(self, payload: dict[str, Any]) -> None:
        """Replace current business state with a seed payload."""
        if payload.get("schema_version") != 1:
            raise ValueError("unsupported seed schema_version")

        collections = payload.get("collections")
        policies = payload.get("policies", [])
        if not isinstance(collections, list):
            raise ValueError("seed collections must be a list")
       

        if not isinstance(policies, list):
            raise ValueError("seed policies must be a list")

        with self._connect() as connection:
            # Deleting collections also deletes records because of
            # ON DELETE CASCADE.
            connection.execute("DELETE FROM collections")

            for collection in collections:
                if not isinstance(collection, dict):
                    raise ValueError(
                        "each collection must be an object"
                    )

                collection_id = collection.get("id")
                collection_name = collection.get("name")
                records = collection.get("records", [])

                if not isinstance(collection_id, str):
                    raise ValueError(
                        "collection id must be a string"
                    )
                if not isinstance(collection_name, str):
                    raise ValueError(
                        "collection name must be a string"
                    )
                if not isinstance(records, list):
                    raise ValueError(
                        "collection records must be a list"
                    )

                connection.execute(
                    """
                    INSERT INTO collections(id, name)
                    VALUES (?, ?)
                    """,
                    (collection_id, collection_name),
                )

                for record in records:
                    if not isinstance(record, dict):
                        raise ValueError(
                            "each record must be an object"
                        )

                    record_id = record.get("id")
                    fields = record.get("fields")

                    if not isinstance(record_id, str):
                        raise ValueError(
                            "record id must be a string"
                        )
                    if not isinstance(fields, dict):
                        raise ValueError(
                            "record fields must be an object"
                        )

                    connection.execute(
                        """
                        INSERT INTO records(
                            collection_id,
                            id,
                            fields_json,
                            version
                        )
                        VALUES (?, ?, ?, 1)
                        """,
                        (
                            collection_id,
                            record_id,
                            canonical_json(fields),
                        ),
                    )
            for policy in policies:
                if not isinstance(policy, dict):
                    raise ValueError(
                        "each policy must be an object"
                    )

                actor = policy.get("actor")
                collection = policy.get("collection")
                operation = policy.get("operation")
                allowed_fields = policy.get(
                    "allowed_fields"
                )

                if not isinstance(actor, str) or not actor:
                    raise ValueError(
                        "policy actor must be a string"
                    )
                if not isinstance(
                    collection,
                    str,
                ) or not collection:
                    raise ValueError(
                        "policy collection must be a string"
                    )   
                if not isinstance(
                    operation,
                    str,
                ) or not operation:
                    raise ValueError(
                        "policy operation must be a string"
                    )
                if not isinstance(
                    allowed_fields,
                    list,
                ) or not all(
                    isinstance(field, str)
                    for field in allowed_fields
                ):
                    raise ValueError(
                        "policy allowed_fields "
                        "must be a string list"
                    )

                connection.execute(
                    """
                    INSERT INTO policies(
                        actor,
                        collection_id,
                        operation,
                        allowed_fields_json
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        actor,
                        collection,
                        operation,
                        canonical_json(allowed_fields),
                    ),
                )
            # Keep a copy of the original seed so reset() can replay it.
            connection.execute(
                """
                INSERT INTO seed_state(id, payload_json)
                VALUES (1, ?)
                ON CONFLICT(id)
                DO UPDATE SET payload_json = excluded.payload_json
                """,
                (canonical_json(payload),),
            )

            connection.execute(
                """
                UPDATE metadata
                SET value = '0'
                WHERE key = 'state_version'
                """
            )

    def get_state_version(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT value
                FROM metadata
                WHERE key = 'state_version'
                """
            ).fetchone()

        if row is None:
            raise RuntimeError("state version is missing")

        return int(row["value"])

    def get_record(
        self,
        collection: str,
        record_id: str,
    ) -> dict[str, Any] | None:
        """Read one record by collection ID and record ID."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    r.id,
                    r.collection_id,
                    r.fields_json,
                    r.version
                FROM records AS r
                WHERE r.collection_id = ?
                  AND r.id = ?
                """,
                (collection, record_id),
            ).fetchone()

        if row is None:
            return None

        return {
            "id": row["id"],
            "collection": row["collection_id"],
            "fields": json.loads(row["fields_json"]),
            "version": row["version"],
        }

    def list_records(
        self,
        collection: str,
    ) -> list[dict[str, Any]]:
        """List all records in a collection."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    id,
                    collection_id,
                    fields_json,
                    version
                FROM records
                WHERE collection_id = ?
                ORDER BY id
                """,
                (collection,),
            ).fetchall()

        return [
            {
                "id": row["id"],
                "collection": row["collection_id"],
                "fields": json.loads(row["fields_json"]),
                "version": row["version"],
            }
            for row in rows
        ]

    def update_record(
        self,
        collection: str,
        record_id: str,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge fields into an existing record."""
        if not isinstance(fields, dict):
            raise ValueError("fields must be an object")

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT fields_json, version
                FROM records
                WHERE collection_id = ?
                  AND id = ?
                """,
                (collection, record_id),
            ).fetchone()

            if row is None:
                raise KeyError(
                    f"record not found: "
                    f"{collection}/{record_id}"
                )

            current_fields = json.loads(
                row["fields_json"]
            )
            current_fields.update(fields)

            new_record_version = int(row["version"]) + 1

            state_row = connection.execute(
                """
                SELECT value
                FROM metadata
                WHERE key = 'state_version'
                """
            ).fetchone()
            new_state_version = int(state_row["value"]) + 1

            connection.execute(
                """
                UPDATE records
                SET fields_json = ?,
                    version = ?
                WHERE collection_id = ?
                  AND id = ?
                """,
                (
                    canonical_json(current_fields),
                    new_record_version,
                    collection,
                    record_id,
                ),
            )

            connection.execute(
                """
                UPDATE metadata
                SET value = ?
                WHERE key = 'state_version'
                """,
                (str(new_state_version),),
            )

        result = self.get_record(
            collection,
            record_id,
        )
        assert result is not None
        return result
    def get_policy(
        self,
        actor: str,
        collection: str,
        operation: str,
    ) -> dict[str, Any] | None:
        """Return one permission rule."""
        with self._connect() as connection:
            row = connection.execute(
            """
            SELECT
                actor,
                collection_id,
                operation,
                allowed_fields_json
            FROM policies
            WHERE actor = ?
              AND collection_id = ?
              AND operation = ?
            """,
            (
                actor,
                collection,
                operation,
            ),
        ).fetchone()

            if row is None:
                return None

        return {
            "actor": row["actor"],
            "collection": row["collection_id"],
            "operation": row["operation"],
            "allowed_fields": json.loads(
                row["allowed_fields_json"]
         ),
        }

    def reset(self) -> None:
        """Restore the original seed state."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM seed_state
                WHERE id = 1
                """
            ).fetchone()

        if row is None:
            raise RuntimeError(
                "database has not been seeded"
            )

        payload = json.loads(row["payload_json"])
        self.seed(payload)

    def business_snapshot(self) -> dict[str, Any]:
        """Return stable evaluator-visible business state."""
        with self._connect() as connection:
            collections = connection.execute(
                """
                SELECT id, name
                FROM collections
                ORDER BY id
                """
            ).fetchall()

        result: list[dict[str, Any]] = []

        for collection in collections:
            result.append(
                {
                    "id": collection["id"],
                    "name": collection["name"],
                    "records": self.list_records(
                        collection["id"]
                    ),
                }
            )

        return {
            "schema_version": 1,
            "state_version": self.get_state_version(),
            "collections": result,
        }
    def advance_state_version(self) -> int:
        """Atomically increment and return the environment state version."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")

            row = connection.execute(
            """
            SELECT value
            FROM metadata
            WHERE key = 'state_version'
            """
            ).fetchone()

            if row is None:
                raise RuntimeError(
                "state version is missing"
            )

            next_version = int(row["value"]) + 1

            connection.execute(
            """
            UPDATE metadata
            SET value = ?
            WHERE key = 'state_version'
            """,
            (str(next_version),),
            )

        return next_version

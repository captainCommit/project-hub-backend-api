"""Recently deleted: a copy of everything a portfolio, program or project delete removes, so it can be restored.

Deleting still removes the rows (so nothing else in the app has to skip deleted records). Just before, `take_snapshot`
copies every row the delete will remove: the item, every row that reaches it through ON DELETE CASCADE foreign keys
(found from the models' metadata, so new child tables are covered automatically), and the comments and attachments
that point at those rows by entity id. It also notes rows elsewhere whose ON DELETE SET NULL reference will be
cleared (a resource's current sprint, say). `restore_snapshot` inserts the rows again with their original ids,
in foreign key order, and puts the cleared references back.

What a restore cannot bring back: notifications (they were transient), and links to things that were removed since
(a user who left, a deleted option value): those references are cleared, and rows that cannot exist without them
are skipped, as a delete of that thing would have done.
"""

import base64
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import Table, and_, delete, insert, select, update
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models.attachment import Attachment
from app.models.comment import Comment
from app.models.deleted_record import DeletedRecord

RETENTION_DAYS = 30
CHUNK = 500

Row = dict[str, Any]
PkKey = tuple[Any, ...]


# Values to and from JSON


def encode(value: Any) -> Any:
    if isinstance(value, UUID):
        return {"$uuid": str(value)}
    if isinstance(value, datetime):
        return {"$dt": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, time):
        return {"$time": value.isoformat()}
    if isinstance(value, Decimal):
        return {"$dec": str(value)}
    if isinstance(value, bytes):
        return {"$b64": base64.b64encode(value).decode()}
    if isinstance(value, Enum):
        return value.value
    return value


DECODERS = {
    "$uuid": UUID,
    "$dt": datetime.fromisoformat,
    "$date": date.fromisoformat,
    "$time": time.fromisoformat,
    "$dec": Decimal,
    "$b64": base64.b64decode,
}


def decode(value: Any) -> Any:
    if isinstance(value, dict) and len(value) == 1:
        (key, raw), = value.items()
        if key in DECODERS:
            return DECODERS[key](raw)
    return value


# Foreign keys from the models


def tables() -> dict[str, Table]:
    return dict(Base.metadata.tables)


def pk_of(table: Table, row: Row) -> PkKey:
    return tuple(row[column.name] for column in table.primary_key.columns)


def single_column_fks(table: Table):
    """(column name, referenced table name, referenced column name, ondelete) for each one-column foreign key."""
    for constraint in table.foreign_key_constraints:
        if len(constraint.elements) != 1:
            continue
        element = constraint.elements[0]
        yield element.parent.name, element.column.table.name, element.column.name, (constraint.ondelete or "").upper()


def chunks(values: list[Any]):
    for start in range(0, len(values), CHUNK):
        yield values[start:start + CHUNK]


# Taking the snapshot


def take_snapshot(db: Session, *, account_id: UUID, root_table: str, root_id: UUID) -> dict[str, Any]:
    all_tables = tables()
    # parent table name -> [(child table, child column, parent column, ondelete)]
    children: dict[str, list[tuple[Table, str, str, str]]] = defaultdict(list)
    for table in all_tables.values():
        for column, parent, parent_column, ondelete in single_column_fks(table):
            children[parent].append((table, column, parent_column, ondelete))

    collected: dict[str, dict[PkKey, Row]] = defaultdict(dict)

    def add(table: Table, rows: list[Row]) -> list[Row]:
        new = [row for row in rows if pk_of(table, row) not in collected[table.name]]
        for row in new:
            collected[table.name][pk_of(table, row)] = row
        return new

    def fetch(table: Table, column: str, values: list[Any]) -> list[Row]:
        rows: list[Row] = []
        for part in chunks(values):
            rows.extend(dict(r._mapping) for r in db.execute(select(table).where(table.c[column].in_(part))))
        return rows

    def cascade(frontier: dict[str, list[Row]]) -> None:
        while frontier:
            following: dict[str, list[Row]] = defaultdict(list)
            for parent_name, rows in frontier.items():
                for child, column, parent_column, ondelete in children[parent_name]:
                    if ondelete != "CASCADE":
                        continue
                    values = list({row[parent_column] for row in rows if row[parent_column] is not None})
                    if values:
                        following[child.name].extend(add(child, fetch(child, column, values)))
            frontier = {name: rows for name, rows in following.items() if rows}

    root = all_tables[root_table]
    cascade({root_table: add(root, fetch(root, "id", [root_id]))})

    # Comments and attachments name their record by entity type and id, with no foreign key.
    def entity_ids() -> list[Any]:
        return [row["id"] for rows in collected.values() for row in rows.values() if "id" in row]

    comments = Comment.__table__
    cascade({"comments": add(comments, [
        row for row in fetch(comments, "entity_id", entity_ids()) if row["account_id"] == account_id
    ])})
    attachments = Attachment.__table__
    cascade({"attachments": add(attachments, [
        row for row in fetch(attachments, "entity_id", entity_ids()) if row["account_id"] == account_id
    ])})

    # References outside the deleted rows that the delete will clear (ON DELETE SET NULL).
    cleared: list[dict[str, Any]] = []
    for parent_name, rows_by_pk in list(collected.items()):
        for child, column, parent_column, ondelete in children[parent_name]:
            if ondelete != "SET NULL":
                continue
            values = list({row[parent_column] for row in rows_by_pk.values() if row[parent_column] is not None})
            for row in fetch(child, column, values) if values else []:
                if pk_of(child, row) in collected[child.name]:
                    continue
                cleared.append({
                    "table": child.name,
                    "pk": [encode(v) for v in pk_of(child, row)],
                    "column": column,
                    "value": encode(row[column]),
                })

    return {
        "version": 1,
        "rows": {
            name: [{key: encode(value) for key, value in row.items()} for row in rows.values()]
            for name, rows in collected.items() if rows
        },
        "cleared": cleared,
    }


# Restoring it


class RestoreError(Exception):
    pass


def restore_snapshot(db: Session, snapshot: dict[str, Any], *, root_table: str, root_id: UUID,
                     overrides: dict[str, Any] | None = None) -> None:
    """Insert the snapshot's rows again. Raises RestoreError if the item itself cannot go back.

    overrides: column values to set on the root row (a project follows its program's current portfolio).
    Runs inside the caller's transaction; the caller commits.
    """
    all_tables = tables()
    snap_rows: dict[str, list[Row]] = {
        name: [{key: decode(value) for key, value in row.items()} for row in rows]
        for name, rows in snapshot.get("rows", {}).items()
        if name in all_tables
    }
    for row in snap_rows.get(root_table, []):
        if row.get("id") == root_id:
            row.update(overrides or {})

    order = [table for table in Base.metadata.sorted_tables if table.name in snap_rows]
    position = {table.name: index for index, table in enumerate(order)}
    # Values of each referenced column among the snapshot's rows, and among those actually inserted.
    in_snapshot: dict[tuple[str, str], set[Any]] = defaultdict(set)
    inserted: dict[tuple[str, str], set[Any]] = defaultdict(set)
    referenced = {(parent, parent_column) for table in order for _, parent, parent_column, _ in single_column_fks(table)}
    for name, column in referenced:
        for row in snap_rows.get(name, []):
            if row.get(column) is not None:
                in_snapshot[(name, column)].add(row[column])

    exists_cache: dict[tuple[str, str], set[Any]] = {}

    def existing(parent: str, parent_column: str, values: set[Any]) -> set[Any]:
        known = exists_cache.setdefault((parent, parent_column), set())
        missing = [v for v in values if v not in known]
        target = all_tables[parent].c[parent_column]
        for part in chunks(missing):
            known.update(db.scalars(select(target).where(target.in_(part))).all())
        return known

    deferred: list[tuple[Table, PkKey, str, str, str, Any]] = []

    for table in order:
        rows = snap_rows[table.name]
        fks = list(single_column_fks(table))
        # Foreign keys to rows outside the snapshot: do those rows still exist?
        outside: dict[tuple[str, str], set[Any]] = defaultdict(set)
        for row in rows:
            for column, parent, parent_column, _ in fks:
                value = row.get(column)
                if value is not None and value not in in_snapshot.get((parent, parent_column), set()):
                    outside[(parent, parent_column)].add(value)
        still_there = {key: existing(*key, values) for key, values in outside.items()}

        keep: list[Row] = []
        for row in rows:
            row = dict(row)
            dropped = False
            for column, parent, parent_column, _ in fks:
                value = row.get(column)
                if value is None:
                    continue
                nullable = table.c[column].nullable
                if value in in_snapshot.get((parent, parent_column), set()):
                    if parent in position and position[parent] >= position[table.name] and nullable:
                        # Same table or later in the order: link it once everything is in.
                        deferred.append((table, pk_of(table, row), column, parent, parent_column, value))
                        row[column] = None
                    elif parent in position and position[parent] < position[table.name] \
                            and value not in inserted[(parent, parent_column)]:
                        if nullable:
                            row[column] = None
                        else:
                            dropped = True
                elif value not in still_there.get((parent, parent_column), set()):
                    if nullable:
                        row[column] = None
                    else:
                        dropped = True
            if dropped:
                if table.name == root_table and row.get("id") == root_id:
                    raise RestoreError("Something this item belongs to no longer exists.")
                continue
            keep.append(row)

        for part in chunks(keep):
            db.execute(insert(table), part)
        for row in keep:
            for name, column in referenced:
                if name == table.name and row.get(column) is not None:
                    inserted[(name, column)].add(row[column])

    def where_pk(table: Table, pk: PkKey):
        return and_(*(column == value for column, value in zip(table.primary_key.columns, pk)))

    for table, pk, column, parent, parent_column, value in deferred:
        if value in inserted[(parent, parent_column)]:
            db.execute(update(table).where(where_pk(table, pk)).values({column: value}))

    for entry in snapshot.get("cleared", []):
        table = all_tables.get(entry["table"])
        if table is None:
            continue
        value = decode(entry["value"])
        target = next(((p, pc) for c, p, pc, _ in single_column_fks(table) if c == entry["column"]), None)
        if target is None or value not in inserted[target]:
            continue
        pk = tuple(decode(v) for v in entry["pk"])
        db.execute(
            update(table)
            .where(where_pk(table, pk), table.c[entry["column"]].is_(None))
            .values({entry["column"]: value})
        )


def purge_expired(db: Session, *, account_id: UUID) -> None:
    """Items are kept for RETENTION_DAYS; after that they are gone for good."""
    cutoff = datetime.now(UTC) - timedelta(days=RETENTION_DAYS)
    db.execute(delete(DeletedRecord).where(DeletedRecord.account_id == account_id, DeletedRecord.deleted_at < cutoff))

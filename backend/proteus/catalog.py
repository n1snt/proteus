"""Read the supported subset of a PostgreSQL schema from its system catalogs."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any
from uuid import uuid4

from pglast import parse_sql, stream
from psycopg.rows import dict_row

_SUPPORTED_TYPES = {
    'boolean': 'boolean',
    'smallint': 'smallint',
    'integer': 'integer',
    'bigint': 'bigint',
    'real': 'real',
    'double precision': 'double precision',
    'numeric': 'numeric',
    'text': 'text',
    'character varying': 'varchar',
    'uuid': 'uuid',
    'date': 'date',
    # PostgreSQL prints the default qualifier, while the schema model canonicalizes
    # it to the shorter spelling accepted by the editor.
    'timestamp without time zone': 'timestamp',
    'timestamp with time zone': 'timestamp with time zone',
    'jsonb': 'jsonb',
    'bytea': 'bytea',
}
_TYPE_PATTERN = re.compile(r'^(character varying|numeric)\(([^)]+)\)$')


def _unsupported(kind: str, name: str, reason: str) -> dict[str, str]:
    return {'object': kind, 'name': name, 'reason': reason}


def _reference_ids(reference: dict[str, Any] | None) -> dict[tuple[str, ...], str]:
    """Match IDs only by names. OIDs are local to one PostgreSQL database."""
    ids: dict[tuple[str, ...], str] = {}
    for table in (reference or {}).get('tables', []):
        table_name = table.get('name')
        if not table_name:
            continue
        if table.get('id'):
            ids[('table', table_name)] = table['id']
        for column in table.get('columns', []):
            if column.get('id'):
                ids[('column', table_name, column['name'])] = column['id']
        for constraint in table.get('constraints', []):
            if constraint.get('id'):
                ids[('constraint', table_name, constraint['name'])] = constraint['id']
        for index in table.get('indexes', []):
            if index.get('id'):
                ids[('index', table_name, index['name'])] = index['id']
    return ids


def _id(ids: dict[tuple[str, ...], str], *key: str) -> str:
    return ids.get(key, str(uuid4()))


def _normal_type(value: str) -> str | None:
    value = value.lower()
    if value in _SUPPORTED_TYPES:
        return _SUPPORTED_TYPES[value]
    match = _TYPE_PATTERN.match(value)
    if not match:
        return None
    base, parameters = match.groups()
    if base == 'character varying' and parameters.isdigit():
        return f'varchar({parameters})'
    if base == 'numeric' and re.fullmatch(r'\d+(,\d+)?', parameters):
        return f'numeric({parameters})'
    return None


def _canonical_expression(expression: str) -> str | None:
    """Accept the small expression language, using PostgreSQL's parser not regexes."""
    try:
        statement = parse_sql(f'SELECT {expression}')[0].stmt
        target = statement.targetList[0].val
        rendered = stream.RawStream()(target)
    except Exception:
        return None

    # PostgreSQL prints now() for a default supplied as CURRENT_TIMESTAMP.
    if rendered.lower() in {'now()', 'current_timestamp'}:
        return 'CURRENT_TIMESTAMP'
    node_name = type(target).__name__
    if node_name == 'A_Const':
        return rendered
    if node_name == 'TypeCast' and type(target.arg).__name__ == 'A_Const':
        return rendered
    return None


def _canonical_check(expression: str, column_names: set[str]) -> str | None:
    """Use the validation grammar so import never accepts a check the engine rejects."""
    try:
        from .schema import _check_expression

        return _check_expression(expression, column_names, 'catalog check')
    except ValueError:
        return None


def _identity_is_default(row: dict[str, Any]) -> bool:
    if row['seqincrement'] != 1 or row['seqcache'] != 1 or row['seqcycle']:
        return False
    if row['seqstart'] != 1 or row['seqmin'] != 1:
        return False
    maximums = {'smallint': 32767, 'integer': 2147483647, 'bigint': 9223372036854775807}
    return row['seqmax'] == maximums.get(row['type_name'])


def _integer_vector(value: Any) -> list[int]:
    """Psycopg exposes PostgreSQL's int2vector as text on some server versions."""
    if isinstance(value, str):
        return [int(item) for item in value.split()]
    return [int(item) for item in value]


async def _rows(conn: Any, query: str, parameters: Iterable[Any] = ()) -> list[dict[str, Any]]:
    async with conn.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(query, tuple(parameters))
        return await cursor.fetchall()


async def introspect(
    conn: Any, schema_name: str, reference: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Return a normalized supported snapshot plus every import blocker found."""
    unsupported: list[dict[str, str]] = []
    ids = _reference_ids(reference)
    version_row = (await _rows(conn, 'SHOW server_version_num'))[0]
    server_version = int(next(iter(version_row.values())))
    if not 170000 <= server_version < 180000:
        return {
            'snapshot': {'tables': []},
            'server_version': server_version,
            'unsupported': [_unsupported('database', 'server', 'PostgreSQL 17 is required')],
        }
    if not await _rows(conn, 'SELECT 1 FROM pg_namespace WHERE nspname = %s', (schema_name,)):
        return {
            'snapshot': {'tables': []},
            'server_version': server_version,
            'unsupported': [_unsupported('schema', schema_name, 'schema does not exist')],
        }

    relations = await _rows(
        conn,
        """
        SELECT c.oid, c.relname, c.relkind, c.relpersistence, c.relispartition,
               c.relrowsecurity, c.relforcerowsecurity, c.reloptions, c.reltablespace,
               EXISTS (SELECT 1 FROM pg_inherits i WHERE i.inhrelid = c.oid OR i.inhparent = c.oid) AS inherited
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s
          AND c.relkind <> 'i'
          AND (c.relkind <> 'S' OR NOT EXISTS (
              SELECT 1 FROM pg_depend dep
              WHERE dep.classid = 'pg_class'::regclass AND dep.objid = c.oid AND dep.deptype = 'i'
          ))
        ORDER BY c.relname
        """,
        (schema_name,),
    )
    tables: dict[int, dict[str, Any]] = {}
    for relation in relations:
        if relation['relkind'] != 'r' or relation['relispartition']:
            unsupported.append(
                _unsupported('relation', relation['relname'], 'only ordinary tables are supported')
            )
            continue
        if relation['relpersistence'] != 'p':
            unsupported.append(
                _unsupported(
                    'table', relation['relname'], 'temporary and unlogged tables are unsupported'
                )
            )
            continue
        if relation['inherited'] or relation['relrowsecurity'] or relation['relforcerowsecurity']:
            unsupported.append(
                _unsupported(
                    'table',
                    relation['relname'],
                    'inheritance and row-level security are unsupported',
                )
            )
        if relation['reloptions'] or relation['reltablespace']:
            unsupported.append(
                _unsupported(
                    'table',
                    relation['relname'],
                    'custom storage settings and tablespaces are unsupported',
                )
            )
        tables[relation['oid']] = {
            'id': _id(ids, 'table', relation['relname']),
            'name': relation['relname'],
            'columns': [],
            'constraints': [],
            'indexes': [],
        }

    columns = await _rows(
        conn,
        """
        SELECT a.attrelid, a.attnum, a.attname, a.attnotnull, a.attidentity,
               a.attgenerated, format_type(a.atttypid, a.atttypmod) AS type_name,
               pg_get_expr(d.adbin, d.adrelid) AS default_expression,
               coll.collname AS collation, a.attstorage <> ty.typstorage AS custom_storage,
               a.attcompression,
               s.seqstart, s.seqincrement, s.seqmin, s.seqmax, s.seqcache, s.seqcycle
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_type ty ON ty.oid = a.atttypid
        LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        LEFT JOIN pg_collation coll ON coll.oid = a.attcollation
        LEFT JOIN pg_depend dep ON dep.refobjid = a.attrelid AND dep.refobjsubid = a.attnum
            AND dep.classid = 'pg_class'::regclass AND dep.deptype = 'i'
        LEFT JOIN pg_sequence s ON s.seqrelid = dep.objid
        WHERE n.nspname = %s AND c.relkind = 'r' AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY a.attrelid, a.attnum
        """,
        (schema_name,),
    )
    columns_by_number: dict[tuple[int, int], dict[str, Any]] = {}
    for column in columns:
        table = tables.get(column['attrelid'])
        if table is None:
            continue
        qualified_name = f'{table["name"]}.{column["attname"]}'
        if column['custom_storage'] or column['attcompression']:
            unsupported.append(
                _unsupported(
                    'column', qualified_name, 'custom storage or compression is unsupported'
                )
            )
        normal_type = _normal_type(column['type_name'])
        if normal_type is None:
            unsupported.append(
                _unsupported('column', qualified_name, f'unsupported type {column["type_name"]}')
            )
        if column['attgenerated']:
            unsupported.append(
                _unsupported('column', qualified_name, 'generated columns are unsupported')
            )
        if column['collation'] not in {None, 'default'}:
            unsupported.append(
                _unsupported('column', qualified_name, 'user-defined collations are unsupported')
            )
        identity = {'a': 'always', 'd': 'by_default'}.get(column['attidentity'])
        if identity and not _identity_is_default(column):
            unsupported.append(
                _unsupported(
                    'column',
                    qualified_name,
                    'non-default identity sequence settings are unsupported',
                )
            )
        default = None
        if column['default_expression'] is not None:
            default = _canonical_expression(column['default_expression'])
            if default is None:
                unsupported.append(
                    _unsupported('column', qualified_name, 'unsupported default expression')
                )
        model = {
            'id': _id(ids, 'column', table['name'], column['attname']),
            'name': column['attname'],
            'data_type': normal_type or column['type_name'],
            'nullable': not column['attnotnull'],
            'default': default,
            'identity': identity,
        }
        table['columns'].append(model)
        columns_by_number[(column['attrelid'], column['attnum'])] = model

    constraints = await _rows(
        conn,
        """
        SELECT con.oid, con.conrelid, con.conname, con.contype, con.conkey, con.confkey,
               con.confrelid, con.confdeltype, con.confupdtype, con.confmatchtype,
               con.condeferrable, con.condeferred, con.convalidated,
               con.conindid, coalesce(conidx.indnullsnotdistinct, false) AS nulls_not_distinct,
               pg_get_constraintdef(con.oid) AS definition,
               rn.nspname AS reference_schema, rc.relname AS reference_table
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_class rc ON rc.oid = con.confrelid
        LEFT JOIN pg_namespace rn ON rn.oid = rc.relnamespace
        LEFT JOIN pg_index conidx ON conidx.indexrelid = con.conindid
        WHERE n.nspname = %s AND c.relkind = 'r'
        ORDER BY con.conrelid, con.conname
        """,
        (schema_name,),
    )
    constraint_index_oids: set[int] = set()
    actions = {
        'a': 'NO ACTION',
        'r': 'RESTRICT',
        'c': 'CASCADE',
        'n': 'SET NULL',
        'd': 'SET DEFAULT',
    }
    kinds = {'p': 'primary_key', 'u': 'unique', 'f': 'foreign_key', 'c': 'check'}
    for constraint in constraints:
        table = tables.get(constraint['conrelid'])
        if table is None:
            continue
        name = f'{table["name"]}.{constraint["conname"]}'
        kind = kinds.get(constraint['contype'])
        if kind is None:
            unsupported.append(_unsupported('constraint', name, 'unsupported constraint kind'))
            continue
        if constraint['condeferrable'] or constraint['condeferred']:
            unsupported.append(
                _unsupported('constraint', name, 'deferrable constraints are unsupported')
            )
        if not constraint['convalidated']:
            unsupported.append(
                _unsupported('constraint', name, 'not-valid constraints are unsupported at import')
            )
        if constraint['nulls_not_distinct']:
            unsupported.append(
                _unsupported('constraint', name, 'NULLS NOT DISTINCT is unsupported')
            )
        key_columns = [
            columns_by_number.get((constraint['conrelid'], number))
            for number in constraint['conkey'] or []
        ]
        if any(column is None for column in key_columns):
            unsupported.append(
                _unsupported('constraint', name, 'constraint uses an unsupported expression')
            )
            continue
        model: dict[str, Any] = {
            'id': _id(ids, 'constraint', table['name'], constraint['conname']),
            'name': constraint['conname'],
            'kind': kind,
            'columns': [column['id'] for column in key_columns],
            'expression': None,
        }
        if constraint['conindid']:
            constraint_index_oids.add(constraint['conindid'])
        if kind == 'foreign_key':
            reference = tables.get(constraint['confrelid'])
            if constraint['reference_schema'] != schema_name or reference is None:
                unsupported.append(
                    _unsupported('constraint', name, 'cross-schema foreign keys are unsupported')
                )
                continue
            if constraint['confmatchtype'] != 's':
                unsupported.append(
                    _unsupported('constraint', name, 'non-default foreign-key match is unsupported')
                )
                continue
            reference_columns = [
                columns_by_number.get((constraint['confrelid'], number))
                for number in constraint['confkey'] or []
            ]
            if any(column is None for column in reference_columns):
                unsupported.append(
                    _unsupported('constraint', name, 'foreign key uses unsupported columns')
                )
                continue
            model.update(
                {
                    'reference_table': reference['id'],
                    'reference_columns': [column['id'] for column in reference_columns],
                    'on_delete': actions.get(constraint['confdeltype']),
                    'on_update': actions.get(constraint['confupdtype']),
                }
            )
        elif kind == 'check':
            expression = constraint['definition'][6:]  # Strip PostgreSQL's "CHECK " prefix.
            model['expression'] = _canonical_check(
                expression, {column['name'] for column in table['columns']}
            )
            if model['expression'] is None:
                unsupported.append(_unsupported('constraint', name, 'unsupported check expression'))
                continue
        table['constraints'].append(model)

    indexes = await _rows(
        conn,
        """
        SELECT i.indexrelid, i.indrelid, ic.relname AS index_name, i.indisunique,
               i.indnullsnotdistinct,
               i.indisvalid, i.indisready, i.indnkeyatts, i.indnatts, i.indkey,
               i.indoption, i.indpred IS NOT NULL AS partial, i.indexprs IS NOT NULL AS expressions,
               am.amname, array_agg(coll.collname ORDER BY ord_coll.n)
                   FILTER (WHERE coll.oid <> 0 AND coll.collname <> 'default') AS collations,
               array_agg(opc.opcdefault ORDER BY ord_coll.n) AS default_opclasses
        FROM pg_index i
        JOIN pg_class tc ON tc.oid = i.indrelid
        JOIN pg_namespace n ON n.oid = tc.relnamespace
        JOIN pg_class ic ON ic.oid = i.indexrelid
        JOIN pg_am am ON am.oid = ic.relam
        LEFT JOIN LATERAL unnest(i.indcollation) WITH ORDINALITY AS ord_coll(oid, n) ON true
        LEFT JOIN pg_collation coll ON coll.oid = ord_coll.oid
        LEFT JOIN LATERAL unnest(i.indclass) WITH ORDINALITY AS ord_opc(oid, n) ON ord_opc.n = ord_coll.n
        LEFT JOIN pg_opclass opc ON opc.oid = ord_opc.oid
        WHERE n.nspname = %s AND tc.relkind = 'r'
        GROUP BY i.indexrelid, i.indrelid, ic.relname, i.indisunique, i.indnullsnotdistinct,
                 i.indisvalid, i.indisready,
                 i.indnkeyatts, i.indnatts, i.indkey, i.indoption, i.indpred, i.indexprs, am.amname
        ORDER BY i.indrelid, ic.relname
        """,
        (schema_name,),
    )
    for index in indexes:
        if index['indexrelid'] in constraint_index_oids:
            continue
        table = tables.get(index['indrelid'])
        if table is None:
            continue
        name = f'{table["name"]}.{index["index_name"]}'
        if index['amname'] != 'btree':
            unsupported.append(_unsupported('index', name, 'only B-tree indexes are supported'))
            continue
        if not index['indisvalid'] or not index['indisready']:
            unsupported.append(_unsupported('index', name, 'invalid or unfinished index'))
            continue
        if index['indnullsnotdistinct']:
            unsupported.append(_unsupported('index', name, 'NULLS NOT DISTINCT is unsupported'))
            continue
        if index['partial'] or index['expressions'] or index['indnkeyatts'] != index['indnatts']:
            unsupported.append(
                _unsupported(
                    'index', name, 'partial, expression, and covering indexes are unsupported'
                )
            )
            continue
        numbers = _integer_vector(index['indkey'])
        keys = [columns_by_number.get((index['indrelid'], number)) for number in numbers]
        if any(number == 0 for number in numbers) or any(key is None for key in keys):
            unsupported.append(_unsupported('index', name, 'index uses an expression'))
            continue
        if (
            any(option != 0 for option in _integer_vector(index['indoption']))
            or index['collations']
        ):
            unsupported.append(
                _unsupported('index', name, 'non-default index order or collation is unsupported')
            )
            continue
        if not all(index['default_opclasses']):
            unsupported.append(
                _unsupported('index', name, 'custom operator classes are unsupported')
            )
            continue
        table['indexes'].append(
            {
                'id': _id(ids, 'index', table['name'], index['index_name']),
                'name': index['index_name'],
                'columns': [key['id'] for key in keys],
                'unique': index['indisunique'],
            }
        )

    for row in await _rows(
        conn,
        """
        SELECT c.relname || '.' || t.tgname AS name
        FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND NOT t.tgisinternal
        """,
        (schema_name,),
    ):
        unsupported.append(_unsupported('trigger', row['name'], 'user triggers are unsupported'))
    for row in await _rows(
        conn,
        """
        SELECT c.relname || '.' || p.polname AS name
        FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s
        """,
        (schema_name,),
    ):
        unsupported.append(_unsupported('policy', row['name'], 'row-level security is unsupported'))
    for row in await _rows(
        conn,
        """
        SELECT c.relname || '.' || r.rulename AS name
        FROM pg_rewrite r JOIN pg_class c ON c.oid = r.ev_class
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND r.rulename <> '_RETURN'
        """,
        (schema_name,),
    ):
        unsupported.append(_unsupported('rule', row['name'], 'rules are unsupported'))
    for row in await _rows(
        conn,
        """
        SELECT p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')' AS name
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = %s
        """,
        (schema_name,),
    ):
        unsupported.append(_unsupported('function', row['name'], 'user functions are unsupported'))

    snapshot = {'tables': sorted(tables.values(), key=lambda table: table['name'])}
    if not unsupported:
        # Keep catalog output on exactly the same canonical form used for planning
        # and final fingerprints. Unsupported imports remain reportable instead of
        # being hidden by a validation error from a partial snapshot.
        from .schema import validate_snapshot

        snapshot = validate_snapshot(snapshot)
    return {'snapshot': snapshot, 'unsupported': unsupported, 'server_version': server_version}

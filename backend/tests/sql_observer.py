"""Keep corpus query budgets separate from fixed replica transaction fences."""


class SQLReadLog:
    def __init__(self):
        self.statements = []
        self.data_selects = []
        self.guard_selects = []
        self.other_statements = []

    def clear(self):
        for values in (self.statements, self.data_selects, self.guard_selects, self.other_statements):
            values.clear()

    def __call__(self, _conn, _cursor, statement, _parameters, context, _many):
        self.statements.append(statement)
        upper = statement.lstrip().upper()
        if not upper.startswith("SELECT"):
            # SQLite table-existence probes and explicit read transactions are
            # fixed guard overhead. Other SQL remains visible and must be empty
            # in these read-only contracts, including INSERT/UPDATE/DELETE.
            if upper != "BEGIN" and not upper.startswith(("PRAGMA MAIN.TABLE_INFO(", "PRAGMA TEMP.TABLE_INFO(")):
                self.other_statements.append(statement)
            return
        query = context.compiled.statement if context.compiled is not None else None
        tables = getattr(query, "get_final_froms", lambda: [])()
        guards = {"replica_maintenance_state", "artifact_storage_control", "replica_runtime_identity"}
        is_guard = tables and all(getattr(table, "name", None) in guards for table in tables)
        # Unknown or raw SELECTs count as data; they cannot silently hide N+1.
        (self.guard_selects if is_guard else self.data_selects).append(statement)

# Runbook: Database Operations

Covers the Phase 1 database surface: initialization, migrations, seeding, backup, and
recovery. All commands read connection details from `SURPLUS_AI_DATABASE_URL`.

## Initialize a new environment

```bash
export SURPLUS_AI_DATABASE_URL="postgresql+psycopg://user:pass@host:5432/surplus_ai"
export SURPLUS_AI_ENV=prod
surplusai db init
surplusai db status   # expect "up to date"
```

`db init` first runs `SELECT 1` to confirm connectivity, then upgrades to the newest
revision. It exits non-zero with a logged reason if either step fails.

## Apply pending migrations to an existing environment

```bash
surplusai db status    # confirm what is pending before changing anything
surplusai db backup /backups/pre-migration-$(date +%F).dump
surplusai db migrate
surplusai db status
```

Always take a backup before migrating a non-dev environment. Migrations run in a
transaction (Postgres supports transactional DDL), so a failure rolls back rather than
leaving a half-applied schema, but a backup protects against a successful-but-wrong
migration.

## Roll back a migration

```bash
alembic downgrade -1        # one revision
alembic downgrade base      # everything
```

The initial migration's downgrade drops both the tables and the 15 Postgres enum types.
If you write a migration that adds an enum and forget to drop it in `downgrade()`, the
downgrade appears to succeed but the next upgrade fails with
`type "..." already exists`. `tests/integration/test_db_lifecycle.py` guards against this
with a full upgrade → downgrade → upgrade cycle.

## Seed development data

```bash
SURPLUS_AI_ENV=dev surplusai db seed
```

Creates two dev users on the reserved `.invalid` domain. Idempotent — reruns create
nothing. The command refuses to run when `SURPLUS_AI_ENV` is not `dev`, so it cannot
pollute staging or production.

## Back up

```bash
surplusai db backup /backups/surplus_ai-$(date +%F-%H%M).dump
```

Writes a `pg_dump` custom-format archive. Verify a backup before relying on it:

```bash
pg_restore --list /backups/surplus_ai-<stamp>.dump | head
```

## Restore

```bash
createdb -h host -U user surplus_ai_restored
pg_restore -h host -U user -d surplus_ai_restored /backups/surplus_ai-<stamp>.dump
```

Restore into a **new** database first and inspect it. Only after verifying should you
repoint `SURPLUS_AI_DATABASE_URL` at the restored database.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Configuration error: ... database_url` | `SURPLUS_AI_DATABASE_URL` unset or not using `postgresql+psycopg://` | Set it with the correct driver scheme |
| `db init` exits 1 with no migration output | Database unreachable | Check host/port/credentials and that Postgres is accepting connections |
| `type "..." already exists` on upgrade | A prior downgrade left enum types behind | Drop the listed types manually, then re-run; fix the migration's `downgrade()` |
| `alembic check` reports pending operations | Models and migrations have drifted | Generate a new migration for the difference |
| `Refusing to seed in env '...'` | Seeding attempted outside dev | Intended safety guard; do not override |
| `pg_dump not found on PATH` | Postgres client tools missing | Install `postgresql-client` |

## Logs

Structured JSON, written to `$SURPLUS_AI_LOG_DIR/surplus_ai.log` (default `logs/`) and
rotated at 10 MB with 5 generations. Relevant events: `db_init_connected`,
`db_init_connect_failed`, `migration_upgrade_start`, `migration_upgrade_complete`,
`migration_upgrade_failed`, `seed_user_created`, `seed_dev_data_complete`,
`db_backup_start`, `db_backup_complete`, `db_backup_failed`. Connection strings are held
as secrets and are never logged.

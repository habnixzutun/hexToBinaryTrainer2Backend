# Test Setup

## PostgreSQL Test Database

DB-backed tests (migrations, repositories, endpoint tests) run against a
**dedicated** PostgreSQL instance — never the development database on port
5432 (`hextobin`). The migration tests execute `DROP SCHEMA public CASCADE`
per test; `tests/conftest.py` refuses any target database whose name does
not contain `test`.

Defaults: `test_user` / `test_password` @ `localhost:5434`, database `test_db`
(matches `docker-compose.test.yaml` and the CI Postgres service 1:1).

### Start Test Database (local)

```bash
docker compose -f docker-compose.test.yaml up -d
```

If the container is not running, tests fail fast with this hint instead of
an opaque connection error per test.

### Run Tests

```bash
# Run all tests
uv run pytest tests/ -v

# Run specific test file
uv run pytest tests/test_migrations.py -v

# Run with coverage
uv run pytest tests/ --cov=src --cov-report=term-missing
```

### Stop Test Database

```bash
docker compose -f docker-compose.test.yaml down
```

### Test Database Configuration

- **Host:** localhost
- **Port:** 5434 (host) → 5432 (container)
- **User:** test_user
- **Password:** test_password
- **Database:** test_db

### Overriding the Connection

Resolution order (first match wins), implemented in `tests/conftest.py`:

1. **Real environment variables** (`DB_USER`, `DB_PW`, `DB_NAME`, `DB_HOST`,
   `DB_PORT`) — used by CI (GitHub Actions Postgres service on
   `localhost:5432`, see `.github/workflows/deploy.yml`).
2. **`.env.test`** file in the repo root — developer-local overrides
   (gitignored; start from `.env.test.example`).
3. **`TEST_DB_*`** environment variables:

   ```bash
   export TEST_DB_HOST=localhost
   export TEST_DB_PORT=5434
   export TEST_DB_USER=test_user
   export TEST_DB_PASSWORD=test_password
   export TEST_DB_NAME=test_db
   ```

4. **Defaults** matching `docker-compose.test.yaml` (see above).

## Important Notes

- Tests use PostgreSQL (not SQLite) to match production environment
- PostgreSQL-specific features (CASCADE, constraints) are tested correctly
- Each migration test resets the public schema (isolation); the whole test
  session additionally resets the schema on teardown (clean slate for the
  next run)
- Migration tests verify idempotency and schema correctness
- Coverage: `src/migrations/v001_auth_schema.py` ≥ 80 % (currently 93 %)

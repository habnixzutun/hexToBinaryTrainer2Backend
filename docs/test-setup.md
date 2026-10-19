# Test Setup

## PostgreSQL Test Database

Tests require a PostgreSQL database running on localhost:5434.

### Start Test Database

```bash
docker-compose -f docker-compose.test.yaml up -d
```

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
docker-compose -f docker-compose.test.yaml down
```

### Test Database Configuration

- **Host:** localhost
- **Port:** 5434
- **User:** test_user
- **Password:** test_password
- **Database:** test_db

### Environment Variables

Override default test database settings:

```bash
export TEST_DB_HOST=localhost
export TEST_DB_PORT=5434
export TEST_DB_USER=test_user
export TEST_DB_PASSWORD=test_password
export TEST_DB_NAME=test_db
```

## Important Notes

- Tests use PostgreSQL (not SQLite) to match production environment
- PostgreSQL-specific features (CASCADE, constraints) are tested correctly
- Each test gets a fresh session with automatic rollback
- Migration tests verify idempotency and schema correctness

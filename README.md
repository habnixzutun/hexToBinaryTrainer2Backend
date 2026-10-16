# HexToBinaryTrainer2 Backend

FastAPI backend for a hex-to-binary training application with user tracking and leaderboard.

## Features

- **User Management**: Auto-create users on first access
- **Answer Tracking**: Track correct/incorrect answers with point calculations
  - Correct answer: +bits points
  - Incorrect answer: -4×bits points
- **Leaderboard**: Top 10 users sorted by points (DESC), then incorrect count (ASC)
- **RESTful API**: Clean endpoints for user operations and leaderboard

## Tech Stack

- **Framework**: FastAPI
- **Database**: PostgreSQL (async via asyncpg)
- **ORM**: SQLAlchemy 2.0 (async)
- **Validation**: Pydantic v2
- **Testing**: pytest with pytest-asyncio, pytest-cov

## Setup

### Prerequisites

- Python 3.12+
- PostgreSQL 15+
- uv (recommended) or pip

### Installation

```bash
# Clone repository
git clone https://github.com/habnixzutun/hexToBinaryTrainer2Backend.git
cd hexToBinaryTrainer2Backend

# Install dependencies with uv
uv sync

# Or with pip
pip install -e .

# Set up environment variables
cp .env.example .env
# Edit .env with your database credentials
```

### Configuration

Create a `.env` file with:

```env
DB_NAME=your_database
DB_USER=your_user
DB_PW=your_password
DB_HOST=localhost
DB_PORT=5432
```

## Running the Application

```bash
# Start the server
uvicorn src.main:app --reload

# Or with uv
uv run uvicorn src.main:app --reload
```

The API will be available at `http://localhost:8000`

## API Endpoints

### GET /api/leaderboard

Get top 10 users sorted by points.

**Response:**
```json
{
  "entries": [
    {
      "name": "user1",
      "correct": 10,
      "incorrect": 2,
      "points": 50
    }
  ]
}
```

### POST /api/user

Get or create a user.

**Request:**
```json
{
  "name": "username"
}
```

**Response:**
```json
{
  "name": "username",
  "correct": 0,
  "incorrect": 0,
  "points": 0
}
```

### POST /api/correct

Submit a correct answer.

**Request:**
```json
{
  "name": "username",
  "bits": 5
}
```

**Response:**
```json
{
  "message": "Success",
  "user": {
    "name": "username",
    "correct": 1,
    "incorrect": 0,
    "points": 5
  }
}
```

### POST /api/incorrect

Submit an incorrect answer (subtracts 4×bits from points).

**Request:**
```json
{
  "name": "username",
  "bits": 5
}
```

**Response:**
```json
{
  "message": "Success",
  "user": {
    "name": "username",
    "correct": 0,
    "incorrect": 1,
    "points": -20
  }
}
```

## Testing

### Run Tests

```bash
# Run all tests
uv run pytest

# Run with coverage report
uv run pytest --cov=src --cov-report=term-missing

# Run specific test file
uv run pytest tests/test_endpoints.py

# Run with verbose output
uv run pytest -v
```

### Coverage Requirements

The project enforces a minimum coverage of **80%** on all God Nodes:

- AnswerRequest: 100%
- User: 100%
- get_user: 100%
- correct_answer: 91%
- incorrect_answer: 91%
- UserType: 100%
- update_user_data: 100%
- get_users: 100%
- get_leaderboard: 91%

**Total Coverage: 95%**

### Test Structure

- `tests/test_smoke.py`: Infrastructure tests
- `tests/test_db_fixtures.py`: Database fixture tests
- `tests/test_pydantic_schemas.py`: Schema validation tests
- `tests/test_user_repository.py`: Repository tests
- `tests/test_endpoints.py`: API endpoint tests
- `tests/test_integration_flow.py`: Cross-community integration tests

## Architecture

### God Nodes (High Connectivity)

The codebase has been analyzed with graphify to identify core abstractions:

1. **AnswerRequest**: Pydantic schema for answer submissions (8 connections)
2. **User**: SQLAlchemy model for user data (7 connections)
3. **get_user, correct_answer, incorrect_answer**: API endpoints (6 connections each)
4. **UserType**: Pydantic schema for user responses (6 connections)
5. **update_user_data**: Repository function for point calculations (6 connections)

### Communities

The codebase is organized into 11 communities:

- User Data Model & Repository
- Application Core & Infrastructure
- API Endpoints & Request Types
- Database Initialization
- Pydantic Type Definitions
- Containerized Deployment
- Configuration Management
- Leaderboard Endpoint
- Package Root
- Database Init Module
- Init Module

## Development

### Code Quality

```bash
# Run linter
uv run ruff check src/

# Auto-fix linting issues
uv run ruff check --fix src/

# Format code
uv run ruff format src/
```

### Database Initialization

```bash
# Initialize database tables
uv run python src/init_db.py
```

## Docker

Build and run with Docker:

```bash
# Build image
docker build -t hextobinarytrainer2-backend .

# Run container
docker run -p 8000:8000 --env-file .env hextobinarytrainer2-backend
```

## Sprint Progress

### Sprint 1 - US-01: Test Foundation (COMPLETED)

**Status:** ✅ All tasks completed

**Tasks:**
1. ✅ Test environment bootstrap (pytest, conftest.py, env vars)
2. ✅ In-memory DB fixtures (SQLite, isolation)
3. ✅ Pydantic schema tests (25 tests)
4. ✅ Repository tests (14 tests)
5. ✅ Endpoint tests (10 tests)
6. ✅ Integration tests (2 tests)
7. ✅ Coverage gate enforcement (95% coverage)
8. ✅ README documentation

**Results:**
- 58 tests passed
- 95% total coverage
- All God Nodes >80% coverage
- Coverage gate enforced via `--cov-fail-under=80`

### Next Steps

- US-02: Graph hygiene (dangling/collapsed edges, inferred relationships)
- US-03: AnswerRequest decoupling (Repository DTO-free)
- US-04: User ↔ UserType consolidation (mapping 3× → 1×)
- US-05: Settings bridge resolution (no import side-effects)
- US-06: Deployment hardening + CI test gate
- US-07: init_db robustness (timeout, logging, schema source-of-truth)
- US-08: Documentation (isolated nodes, thin communities)

## License

MIT

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/us-XX-description`)
3. Write tests first (TDD)
4. Implement feature
5. Ensure 80%+ coverage
6. Create pull request

## Support

For issues and questions, please open an issue on GitHub.

# Architektur-Dokumentation

> Stand: 2026-10-16 · Sprint-1 Abschluss

## Überblick

Die hexToBinaryTrainer2Backend ist eine FastAPI-Anwendung mit PostgreSQL-Datenbank, die in Docker-Containern deployt wird. Die Architektur folgt dem Repository-Pattern mit klarer Trennung zwischen API-Layer, Business-Logic und Datenzugriff.

## Community-Struktur (Knowledge Graph)

Basierend auf dem Knowledge Graph (graphify-out/GRAPH_REPORT.md) gliedert sich das Projekt in folgende signifikante Communities:

### 1. API Endpoints & Request Types (Community 1)
**Hauptknoten:** `AnswerRequest`, `UserRequest`, `UserType`, `ResultResponse`, `UserList`

**Responsibility:** HTTP-API-Schnittstelle, Request/Response-Validierung

**Nachbarn:** `main.py` Endpoints, `pydantic_types.py`

**Status:** ✅ Konsolidiert (US-04: UserType.model_validate)

---

### 2. User Data Model & Repository (Community 2)
**Hauptknoten:** `User` (SQLAlchemy), `get_user`, `get_users`, `update_user_data`

**Responsibility:** Domänenmodell, Datenbankzugriff, Business-Logik

**Nachbarn:** `user_repository.py`, `database/engine.py`

**Status:** ✅ DTO-frei (US-03: Repository entkoppelt)

---

### 3. Configuration & Settings (Community 3)
**Hauptknoten:** `Settings`, `get_settings`, `config.py`

**Responsibility:** Anwendungskonfiguration, Environment-Variablen, Datenbank-URLs

**Nachbarn:** `main.py`, `engine.py`, `init_db.py`

**Status:** ✅ Factory-Pattern (US-05: get_settings mit lru_cache)

---

### 4. Database Initialization (Community 4)
**Hauptknoten:** `init_db`, `wait_for_db`, `init_models`

**Responsibility:** Datenbank-Schema-Erstellung, Connection-Management, Timeout-Handling

**Nachbarn:** `engine.py`, `config.py`

**Status:** ✅ Robust (US-07: Timeout + Logging)

---

### 5. Testing Infrastructure (Community 5)
**Hauptknoten:** `conftest.py`, `test_db_config`, `async_engine`, `session`

**Responsibility:** Test-Fixtures, In-Memory-DB, Dependency-Override

**Nachbarn:** Alle Test-Dateien

**Status:** ✅ Vollständig (US-01: Testfundament)

---

## Isolierte Nodes

### `postgres_data` Volume
**Typ:** Docker-Volume (kein Code-Knoten)

**Zweck:** Persistente Speicherung der PostgreSQL-Daten

**Status:** ✅ Dokumentiert (kein Code, nur Infrastruktur)

**Entscheidung:** Kein Merge in andere Community, da Volume ein reines Deployment-Artefakt ist.

---

### Package-Root `hextobinarytrainer2backend`
**Typ:** Python-Package-Init (`src/__init__.py`)

**Zweck:** Markiert `src/` als Python-Package für Imports

**Status:** ✅ Dokumentiert (Standard-Python-Pattern)

**Entscheidung:** Bewusst isoliert, da `__init__.py` keine Logik enthält.

---

## Dünne Communities (≤ 2 Nodes)

| Community | Inhalt | Entscheidung |
|-----------|--------|--------------|
| Deployment | `Dockerfile`, `docker-compose.yaml` | ✅ Bewusst klein: Nur 2 Infra-Dateien, keine Code-Logik |
| CI/CD | `.github/workflows/deploy.yml` | ✅ Bewusst klein: Single-File-Workflow, Test-Gate + Deploy |
| Environment | `.env.example`, `.env` | ✅ Bewusst klein: Nur Konfig-Dokumentation |
| Graphify-Output | `graphify-out/*` | ✅ Verschwindet bei nächstem Rebuild: Generierte Artefakte, kein Source-Code |

**Status nach US-08:** 4 dünne Communities (alle begründet)

**Sprint-Gate erfüllt:** ≤ 4 dünne Communities ✓

---

## BEHOBENE Code-Gaps

### 1. Debug-Print in incorrect_answer
**Status:** ✅ BEHOBEN (US-03)

**Fix:** `print(request)` in `incorrect_answer` entfernt (main.py Zeile 74)

**Referenz:** US-03 Acceptance Criteria #4

---

### 2. Fehlende bits-Validierung
**Status:** ✅ BEHOBEN (US-03)

**Fix:** `Field(gt=0)` zu `AnswerRequest.bits` hinzugefügt

**Referenz:** US-03 Acceptance Criteria #3

---

### 3. Doppelte Schema-Init
**Status:** ✅ BEHOBEN (US-07)

**Fix:** `init_db.py` ist Single Source of Truth, `main.lifespan` erstellt nur bei Bedarf

**Referenz:** US-07 Acceptance Criteria #4

---

## Architektur-Entscheidungen

### Repository-Pattern
**Entscheidung:** Repository-Schicht arbeitet mit primitiven Typen (str, int), nicht mit Pydantic-DTOs

**Begründung:** Entkopplung von Web-Framework, bessere Testbarkeit, Wiederverwendbarkeit

**Referenz:** US-03

---

### Factory-Pattern für Settings
**Entscheidung:** `get_settings()` mit `@lru_cache` statt Modul-Level-Singleton

**Begründung:** Bessere Testbarkeit, explizite Abhängigkeiten, kein Import-Side-Effect

**Referenz:** US-05

---

### Model-Validate für ORM-Mapping
**Entscheidung:** `UserType.model_validate(user)` statt manueller Feldzuordnung

**Begründung:** DRY, weniger Fehler, automatische Validierung

**Referenz:** US-04

---

### CI-Test-Gate
**Entscheidung:** Deploy nur nach erfolgreicher Test-Pipeline

**Begründung:** Verhindert kaputte Deployments, automatisiert Quality-Gate

**Referenz:** US-06

---

## Weitere Informationen

- **Backlog:** `docs/backlog.md`
- **Knowledge Graph:** `graphify-out/GRAPH_REPORT.md`
- **README:** `README.md`

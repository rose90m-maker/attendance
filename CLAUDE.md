# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Korean workplace attendance management system (㈜태인 근태관리). Flask web app deployed as a Docker container on a Synology NAS. Includes adjacent subsystems: TBM (Tool Box Meeting), MES (제조실행), 위험물 관리, 교육 관리, RAG 문서 검색, Tuya 화재센서, KEPCO 전기요금 수집, CAPS 출입통제 동기화.

## Architecture

### Main Flask app (`app_maria.py`)
- Single large monolith. Deployed to NAS as `app.py`.
- Registers blueprints from `edu_bp.py`, `hazmat_bp.py`, `mes_bp.py`, `tbm_bp.py`. Each blueprint is self-contained: defines its own `_conn()`, `_login_required`, `_admin_required`, and an `init_*_db(app)` that creates tables on app startup. They share session keys (`user_id`, `role`, `e_id`, `user_name`) with the main app — effectively SSO.
- `MARIA` dict at top of `app_maria.py` is the DB config. Blueprints either import it (`from app_maria import MARIA`) or read `current_app.config["MARIA"]` / env vars.
- DB connection helper pattern: `pymysql.connect(**MARIA)` returned from `_conn()`.
- Korean public holidays via `holidays.KR`.

### Separate apps
- **`tbm_app.py`** — standalone Flask on port 5051. Has the same TBM logic as `tbm_bp.py` but as a top-level app. Both share the same `attendance` DB. Runs as its own container `attendance-tbm` via `Dockerfile.tbm`.
- **`rag_server.py`** — FastAPI on Mac Mini, port 8765. Uses `rag_docs.py` for ChromaDB+FTS document indexing. Started via `start_rag.sh` (watchdog: auto-restart on crash).
- **`tuya_fire.py` / `tuya_poller_local.py`** — Tuya IoT fire/smoke sensor polling.
- **`caps_sync.py`** — runs on a **Windows PC** at the access-control server. Reads `C:\Caps\ACServer\access.mdb` (`tenter` table) → pushes to NAS MariaDB. Has `--setup` mode that installs itself as a startup task with watchdog.
- **`kepco_*.py`** — Scrapers for KEPCO (Korea Electric Power) electricity bill data.
- **`backup.py`** — full-system backup to Mac local + NAS sidecar. See `MEMORY.md` for procedure.

### Storage
- **MariaDB** at `127.0.0.1:3307` on NAS (Synology MariaDB10 package — *not* a container). DB name: `attendance`.
- **ChromaDB** in `chroma_db/` (RAG vectors) — lives on Mac Mini, not in main app.
- **Uploads**: `uploads/` (bind-mounted into container as `/app/uploads`).

## Deployment — Docker

**The Flask app runs as a Docker container on the NAS.** Direct `nohup python app.py` does not work because docker-proxy holds the port.

- NAS: `192.168.100.11`, user `rose90m` (or `admin`), SSH port 22
- Containers: `attendance-app` (port 5050), `attendance-tbm` (port 5051)
- Container app path: `/app/`
- NAS staging path: `/volume1/web/attendance/`
- Mounted volume: `/volume1/docker/attendance/uploads:/app/uploads` (uploads only)
- Other files (templates/, static/, app_maria.py) live in the container's writable layer
- `docker` binary on NAS: `/usr/local/bin/docker`
- sudo password is the NAS password; sudo output is prefixed with `"Password: "` which must be stripped

**Deploy**: `python deploy_and_restart.py` (handles SCP → `docker cp` → `docker restart`, and auto-commits dirty git state with message `배포: <changed files>`, prepending to `static/dev_history.json`). Use `--rebuild` flag to rebuild the image (~3 min) instead of just restarting (~15 sec).

`docker-compose.yml` exists as a *future migration target* (different ports: app 5060/5080, db 3308). It is **not** the current production runtime — production still uses the NAS-package MariaDB on 3307.

## Running Locally

```bash
source .venv/bin/activate
python app_maria.py              # main app
python tbm_app.py                # TBM standalone (port 5051)
bash start_rag.sh                # RAG server (Mac Mini)
bash start_tbm.sh {start|stop|restart|status}
```

## Common Tasks

- **Backup** ("백업해"): just run `python3 backup.py`. No questions — memory says run immediately. Produces 5 artifacts (source tar.gz, DB dump, docker inspect JSON, crontab, LaunchAgents). Saves to `/Users/changkooji/` and NAS `/volume1/backup/attendance/` (keeps 14 most recent each).
- **Deploy**: `python deploy_and_restart.py` (or `--rebuild`).
- **Add a blueprint table**: edit the relevant `init_*_db(app)` in the blueprint — runs idempotently on app start.

## Conventions

- All UI text and most comments are **Korean**. Match that style when editing.
- Session keys: `user_id` (login), `role` ("admin" gates), `e_id` (employee id), `user_name`.
- Files prefixed `_check_*.py`, `_test_*.py`, `_run_*.py` at the repo root are **throwaway debug scratch** — not load-bearing, safe to ignore.
- `app_maria.py.bak`, `nohup.out`, `rag_server.log` are local cruft.

## Sensitive — Do Not Commit

- `.env` — API keys (OpenAI, Groq, Gemini, Cohere)
- `deploy_and_restart.py`, `backup.py`, `caps_sync.py` — NAS/DB credentials in plaintext
- `app_maria.py`, `tbm_app.py`, `tbm_bp.py` — Telegram token, MariaDB password inline
- `db-init/`, `db-slave/` — DB seed data and replication configs (gitignored)

## Key DB Tables

Discoverable via `init_*_db()` in each blueprint. Highlights:
- `tuser`, `employee_roster` — users / employee master (joined for dept info)
- `tenter` — access-control entry log (populated by `caps_sync.py`)
- `lp_group_members`, `lp_group_reviewers`, `leave_records`, `annual_leave` — 휴가/연차
- `edu_courses`, `edu_sessions` — 교육
- `hazmat_items` — 위험물
- `mes_devices`, `mes_env_log` + production count tables — MES

---

## 🚧 진행 중 작업: .env 자격증명 이관 (2026-05-18 시작 / 2026-05-19 대부분 완료)

### 작업 목적
하드코딩된 자격증명/토큰을 모두 `.env`로 이관. 값 변경은 **하지 않음** (이관만, fail-fast 적용).

### 진행 현황 — 9단계 중 8단계 완료

#### ✅ 사전 작업 (2026-05-18)
- 폴더 백업: `/Users/changkooji/attendance_snapshot_2026-05-18/` (158MB)
- 디버그 스크립트 20개 → `_archive/` 이관 (커밋 `76da180`)
- `.env` 백업: `.env.backup_2026-05-18`, `.env.before_step_B_073559` (gitignore 무시)
- `.gitignore` 보강: `.env.backup_*`, `.env.local` (커밋 `97db34e`)
- 자격증명 전수조사 완료

#### ✅ .env 키 추가 (2026-05-19)
- DB 백업 실행 (`20260519_073151`)
- `.env`에 11개 신규 키 추가 완료 (24줄 → 56줄, 사용자 직접 입력)
- 추가 키: `FLASK_SECRET_KEY`, `TBM_SECRET_KEY`, `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, `MAIL_USER`, `MAIL_PASS`, `NAS_HOST`, `NAS_USER`, `NAS_PASS`, `NAS_SUDO`, `CAPS_MDB_PWD`

#### ✅ 코드 수정 완료 8개 파일 (2026-05-19)

| # | 파일 | 변경 요약 | 커밋 |
|---|---|---|---|
| 1 | `tuya_poller_local.py` | TUYA_ACCESS_ID/SECRET → os.environ, load_dotenv 추가 | `f0a0d1c` |
| 2 | `kepco_collector.py` | DB_PASSWORD 기본값 제거 (fail-fast) | `e7059f1` |
| 3 | `kepco_pp_scraper.py` | DB_PASSWORD 기본값 제거 (fail-fast) | `e7059f1` |
| 4 | `tbm_bp.py` | load_dotenv 추가 + DB_PASSWORD fail-fast | `f0d1f32` |
| 5 | `tbm_app.py` | load_dotenv + TBM_SECRET_KEY + DB_PASSWORD fail-fast | `f0d1f32` |
| 6 | `backup.py` | load_dotenv + NAS_* 4개 환경변수화 | `ef9a1f2` |
| 7 | `deploy_and_restart.py` (이관) | load_dotenv + NAS_* 4개 환경변수화 | `ef9a1f2` |
| 8 | `caps_sync.py` | load_dotenv + CAPS_MDB_PWD + MARIA_* → DB_* 통합 (5개) | `5611621` |
| **부가** | `deploy_and_restart.py` (보강) | `docker_files_tbm`에 .env 1줄 추가 (tbm 컨테이너에도 .env 전달) | `76ca1bb` |

#### ✅ NAS 컨테이너 사전 점검 (2026-05-19)
- `attendance-app`: Up 6 days, python-dotenv 1.2.2 설치됨, `/app/.env` 존재 (969B, 옛 24줄 버전)
- `attendance-tbm`: Up 6 days, python-dotenv 1.2.2 설치됨, `/app/.env` **없음** (deploy 보강으로 다음 배포부터 들어감)
- `docker` 명령은 NAS에서 **sudo 필수**
- 컨테이너 환경변수: 둘 다 DB_* 5개만 존재. 신규 11개는 배포 시 `/app/.env`로 주입 예정 (`load_dotenv()` 또는 수동 파서가 읽음)
- 점검 스크립트: `_archive/_nas_check.py` (보관)

### 🌅 다음 시작점: 9단계 — `app_maria.py` 수정 (마지막)

**옵션 C 확정** (load_dotenv 추가 + 기존 수동 파서 유지 + 자격증명 6곳 fail-fast)

**Edit 방식 B 확정**: 7회 분할 (load_dotenv 1회 + 자격증명 6회)

#### 변경 위치 (라인 grep 확정)

| # | 라인 | 변경 |
|---|---|---|
| 1 | L5 다음 | `+ from dotenv import load_dotenv` + `+ load_dotenv()` (수동 파서 L7-15 직전, 수동 파서는 그대로 유지) |
| 2 | L48 | `app.secret_key = ...` → `os.environ["FLASK_SECRET_KEY"]` |
| 3 | L87 | `TELEGRAM_TOKEN = ...` → `os.environ["TELEGRAM_TOKEN"]` |
| 4 | L88 | `TELEGRAM_CHAT_ID = ...` → `os.environ["TELEGRAM_CHAT_ID"]` |
| 5 | L96 | `MAIL_USER = os.environ.get(..., "...")` → `os.environ["MAIL_USER"]` |
| 6 | L97 | `MAIL_PASS = os.environ.get(..., "...")` → `os.environ["MAIL_PASS"]` |
| 7 | L103 | `"password": os.environ.get(..., "...")` → `os.environ["DB_PASSWORD"]` |

#### 검증 방식
- ✅ `ast.parse` + `py_compile` (마지막 1회)
- ❌ `import app_maria` 절대 금지 — 9,666줄 톱레벨 실행 위험 (tuya 폴러 시작, Flask 초기화 등)

### 🚀 배포 (별도 결정)
- 9단계 완료 후 진행
- `python deploy_and_restart.py` (rebuild 없이 일반 배포)
- 사용자 활동 시간 회피
- 배포 후 즉시 헬스체크 권장

### `app.py` (배포본)
- 수정 안 함 — 다음 배포 시 자동 반영

### 안전 수칙 (작업 재개 시 준수)
1. 코드 백업 (git commit / 폴더 복사)
2. DB 백업
3. 운영 시간 회피
4. .env 절대 커밋/공유 금지
5. **값 변경 금지 — 이관만**
6. 자격증명 값은 Claude가 직접 다루지 않음 (사용자가 .env에 직접 입력) — `feedback_credential_handling.md`

### 별도 처리 예정 (본 작업 끝난 후)
- `backup.py` 생성 tar.gz에서 `.env` 제외 검토 (보안)
- `caps_sync.py:5` SyntaxWarning (`\C` escape) 정리 (선택)
- `app_maria.py` IDE 힌트 (미사용 변수 등) 정리 (선택)

### 롤백 지점
- `da3db5a` — 작업 전 스냅샷
- `76da180` — 디버그 정리 완료
- `cdde236` — 어제 작업 종료 시점
- `76ca1bb` — 현재 시점 (8단계 완료, 9단계 진입 전)
- 파일 시스템: `/Users/changkooji/attendance_snapshot_2026-05-18/`
- .env 원본: `.env.backup_2026-05-18`, `.env.before_step_B_073559`
- DB 백업: `attendance_db_FULL_20260519_073151.sql` (Mac 로컬 + NAS)


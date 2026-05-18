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

## 🚧 진행 중 작업: .env 자격증명 이관 (2026-05-18 시작)

### 작업 목적
하드코딩된 자격증명/토큰을 모두 `.env`로 이관. 값 변경은 **하지 않음** (이관만).

### 오늘 완료 (2026-05-18)
- ✅ **사전 작업**: 폴더 백업(`/Users/changkooji/attendance_snapshot_2026-05-18/`, 158MB) + git 스냅샷 커밋(`da3db5a`)
- ✅ **디버그 정리**: `_check_*.py`/`_test_*.py`/`_run_*.py` 20개 → `_archive/`로 이관 (커밋 `76da180`)
- ✅ **.env 백업**: `.env.backup_2026-05-18` (gitignore로 무시됨)
- ✅ **.gitignore 보강**: `.env.backup_*`, `.env.local` 패턴 추가 (커밋 완료)
- ✅ **자격증명 전수조사**: 11개 신규 .env 키 매핑 + 9개 코드 수정 대상 파일 확정

### 🌅 내일 시작점
**[B] 단계부터: `.env`에 11개 키의 실제 값 추가**

작업 순서: `[B]` 값 입력 → `[C]` 검증 → `[D]` (.env는 어차피 git 무시 → 코드 수정 단계로 자연스럽게 연결)

### 추가할 11개 .env 키 매핑

| # | .env 키 | 소스 (파일:라인) | 비고 |
|---|---|---|---|
| 1 | `FLASK_SECRET_KEY` | app_maria.py:48 | Flask 세션 |
| 2 | `TBM_SECRET_KEY` | tbm_app.py:17 | TBM 세션 |
| 3 | `TELEGRAM_TOKEN` | app_maria.py:87 | 봇 토큰 |
| 4 | `TELEGRAM_CHAT_ID` | app_maria.py:88 | 채팅방 ID |
| 5 | `MAIL_USER` | app_maria.py:96 | O365 계정 |
| 6 | `MAIL_PASS` | app_maria.py:97 | O365 비밀번호 |
| 7 | `NAS_HOST` | backup.py:9 | 192.168.100.11 |
| 8 | `NAS_USER` | backup.py:10 | NAS 계정 |
| 9 | `NAS_PASS` | backup.py:11 | NAS SSH 비밀번호 |
| 10 | `NAS_SUDO` | backup.py:12 | NAS sudo (동일값) |
| 11 | `CAPS_MDB_PWD` | caps_sync.py:35 | CAPS Access DB |

### 코드 수정 대상 9개 파일

| # | 파일 | 수정 사항 | 사용할 .env 키 |
|---|---|---|---|
| 1 | `app_maria.py` | L48, L87-88, L96-97, L103 | FLASK_SECRET_KEY, TELEGRAM_*, MAIL_*, DB_PASSWORD |
| 2 | `backup.py` | L9-12 NAS_* 4개 | NAS_* |
| 3 | `deploy_and_restart.py` | L133-136 NAS_* | NAS_* (재사용) |
| 4 | `caps_sync.py` | L35 MDB_PWD + L38-42 MARIA_* | CAPS_MDB_PWD + DB_* |
| 5 | `tbm_bp.py` | L24 DB_PASSWORD 기본값 | DB_PASSWORD |
| 6 | `tbm_app.py` | L17 secret_key + L24 DB_PASSWORD 기본값 | TBM_SECRET_KEY + DB_PASSWORD |
| 7 | `kepco_collector.py` | L30 DB_PASSWORD 기본값 | DB_PASSWORD |
| 8 | `tuya_poller_local.py` | L10-11 TUYA_* 하드코딩 | TUYA_ACCESS_ID/SECRET (기존 .env) |
| 9 | `kepco_pp_scraper.py` | L33 DB_PASSWORD 기본값 | DB_PASSWORD |

**[app.py](app.py) (배포본)은 수정하지 않음** — 다음 배포 시 자동 반영. 단 작업 완료 후 즉시 배포 필요.

### caps_sync.py 수정 시 주석 추가 필수
```python
# caps_sync.py: 메인 시스템과 동일한 MariaDB 사용
# 환경변수는 DB_HOST/DB_PORT/DB_USER/DB_PASSWORD 공유
# 미래에 분리 필요 시 CAPS_DB_* 별도 키 추가
```

### 안전 수칙 (작업 재개 시 준수)
1. 코드 백업 (git commit / 폴더 복사)
2. DB 백업
3. 운영 시간 회피
4. .env 절대 커밋/공유 금지
5. **값 변경 금지 — 이관만**

### 롤백 지점
- `da3db5a` — 작업 전 스냅샷
- `76da180` — 디버그 정리 완료 시점
- `/Users/changkooji/attendance_snapshot_2026-05-18/` — 파일 시스템 백업
- `.env.backup_2026-05-18` — .env 원본


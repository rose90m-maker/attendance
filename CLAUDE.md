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

> ⚠️ **새 `*_bp.py` 를 추가할 때 `deploy_and_restart.py` 의 목록 3곳을 모두 고칠 것.**
> `files`(SCP 전송) 하나만 추가하면 파일이 NAS 까지만 가고 컨테이너에는 안 들어가
> `ModuleNotFoundError` 로 앱이 크래시 루프에 빠진다 (2026-07-29 사고, 다운타임 약 7분).
> - `files` — NAS staging 으로 SCP (templates/ 는 여기만 추가하면 자동으로 따라감)
> - `docker_files_main` — **`.py` 는 여기 하드코딩 목록에 반드시 추가**
> - `STANDBY_PY` — .5 대기서버용. 빠지면 대기서버가 기동 실패
>
> 또한 `docker cp` 가 NAS 의 ACL 을 옮기면서 파일 권한이 `000` 이 되는 경우가 있어
> 파이썬이 모듈을 못 읽는다. cp 루프 뒤에 `chmod a+r` 보정 단계를 넣어 두었다.

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

## ✅ 완료된 작업: .env 자격증명 이관 (2026-05-18 ~ 2026-05-19) — 배포 대기

### 작업 목적
하드코딩된 자격증명/토큰을 모두 `.env`로 이관. 값 변경은 **하지 않음** (이관만, fail-fast 적용).

### 진행 현황 — 9/9 완료 (배포 대기)

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
| **9** | `app_maria.py` | load_dotenv + 자격증명 6곳 fail-fast (옵션 C) — L51, L90, L91, L99, L100, L106 | `f9446a4` |

#### ✅ NAS 컨테이너 사전 점검 (2026-05-19)
- `attendance-app`: Up 6 days, python-dotenv 1.2.2 설치됨, `/app/.env` 존재 (969B, 옛 24줄 버전)
- `attendance-tbm`: Up 6 days, python-dotenv 1.2.2 설치됨, `/app/.env` **없음** (deploy 보강으로 다음 배포부터 들어감)
- `docker` 명령은 NAS에서 **sudo 필수**
- 컨테이너 환경변수: 둘 다 DB_* 5개만 존재. 신규 11개는 배포 시 `/app/.env`로 주입 예정 (`load_dotenv()` 또는 수동 파서가 읽음)
- 점검 스크립트: `_archive/_nas_check.py` (보관)

### ✅ 9단계 완료 (2026-05-19)
- 옵션 C 적용 (load_dotenv + 기존 수동 파서 모두 유지)
- 자격증명 6곳 fail-fast: L51(secret_key), L90(TELEGRAM_TOKEN), L91(TELEGRAM_CHAT_ID), L99(MAIL_USER), L100(MAIL_PASS), L106(DB_PASSWORD)
- Edit 방식 B (7회 분할)로 안전하게 적용
- 검증: ast.parse OK, py_compile OK (9,669줄)
- 커밋: `f9446a4`

### 🚀 배포 (별도 결정 — 사용자 활동 시간 회피 필수)

**현재 상태**: 모든 로컬 수정 완료. 운영 컨테이너는 아직 옛 코드 사용 중.

**권장 순서**:
1. `.env`의 `FLASK_SECRET_KEY`를 강한 값으로 교체
   - 생성 예시: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` 또는 `secrets.token_hex(32)` (256bit+ 엔트로피)
   - 현재는 placeholder 상태
2. 사용자 활동 적은 시간 확보 (전 사용자 강제 재로그인 발생)
3. `python deploy_and_restart.py` 실행 (rebuild 없이 ~15초 다운타임)
4. 즉시 `https://app.taein.biz` 로그인/근태 등록 검증
5. 모든 사용자에게 재로그인 안내 공지

**배포 실행 시 Claude 사용 방식**: 9단계와 동일하게 **한 단계씩 권한 요청 가이드**로 진행 권장. 자동 실행 금지. 각 단계 결과 확인 후 다음 진행.

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

#### 코드 정리 (선택)
- `backup.py` 생성 tar.gz에서 `.env` 제외 검토 (보안)
- `caps_sync.py:5` SyntaxWarning (`\C` escape) 정리
- `app_maria.py` IDE 힌트 (미사용 변수 등) 정리

#### 🔐 자격증명 강도 점검 (배포 전/후 권장)
1. **`FLASK_SECRET_KEY` 강화** — 현재 placeholder 수준의 짧은 문자열. `secrets.token_urlsafe(48)` 또는 `secrets.token_hex(32)`로 교체 권장 (256bit+ 엔트로피). 교체 시 **전 사용자 세션 무효화** → 운영 시간 회피 필수.
2. **`MAIL_PASS` 형식 점검** — 현재 길이가 11자. O365 앱 비밀번호는 보통 16자 고정. MFA 환경이면 앱 비밀번호 사용 검토.
3. **`DB_PASSWORD` 강도 점검** — 길이/복잡도 점검 권장.
4. **git 히스토리 잔존 점검** — 과거 커밋에 평문 자격증명 잔존 가능. `git log --all -p -S "<검색어>"`로 영향 평가. 필요 시 `git filter-repo`로 히스토리 재작성 (외부 remote 없으므로 비교적 안전).

### 롤백 지점
- `da3db5a` — 작업 전 스냅샷
- `76da180` — 디버그 정리 완료
- `cdde236` — 어제 작업 종료 시점
- `76ca1bb` — 8단계 완료, 9단계 진입 전
- `ca08030` — 9단계 진입 직전 (CLAUDE.md 업데이트)
- `f9446a4` — **9단계 완료 = 현재 시점** (모든 로컬 수정 완료, 배포 전)
- 파일 시스템: `/Users/changkooji/attendance_snapshot_2026-05-18/`
- .env 원본: `.env.backup_2026-05-18`, `.env.before_step_B_073559`
- DB 백업: `attendance_db_FULL_20260519_073151.sql` (Mac 로컬 + NAS)

---

## 🚨 배포 후 사고 + 복구 (2026-05-19)

### 사고 개요
9단계 완료 후 배포(`python deploy_and_restart.py`) 실행 → `attendance-app` 재시작 루프 (23회), `pymysql.err.OperationalError: (1045, "Access denied for user 'root'@...")`. `attendance-tbm`은 HTTP 200처럼 보였으나 실제로는 DB init 동시 실패. **다운타임 약 2.5시간** (사용자가 사고 인지부터 복구 완료까지).

### 근본 원인
1. **컨테이너 env 우선순위 > /app/.env** — 컨테이너 최초 생성 시 `docker run --env-file`로 박힌 옛 DB_PASSWORD(11자)가 `os.environ`에 잔존. 9단계의 `load_dotenv()` / 수동 파서는 `override=False`/`setdefault` 정책이라 새 .env(32자)를 무시.
2. **이미지 baseline 코드 vs docker cp 의존성 (구조 이슈)** — `python deploy_and_restart.py`(no --rebuild)는 `docker cp`로 최신 코드를 컨테이너에 푸시 + `docker restart`만 수행. 컨테이너 자체는 이미지 baseline 코드(빌드 시점 상태) 위에 docker cp로 패치된 상태. **컨테이너 재생성(`docker stop && rm && run`) 시 docker cp 변경분 전부 손실**.

### 복구 흐름 (4단계, 일회용 진단 스크립트로 진행)
1. **진단** (`_recovery_diag.py`): 양 컨테이너 env DB_PASSWORD 길이/.env sha256/로그 확인 → 양쪽 다 옛 비번 사용 확정
2. **인증 검증** (`_recovery_auth_check.py`): Mac → NAS:3307 새 비번으로 직접 인증 성공 + mysql.user 6개 root 엔트리 해시 일치 확인
3. **재생성** (`_recovery_recreate.py`): docker stop && rm && run `--env-file /volume1/web/attendance/.env` (32자 비번 .env)로 양 컨테이너 재생성 → app 복구, **tbm은 command 인자 빠뜨려 이미지 default CMD(`python app_maria.py`) 사용 → port 5050에 listening → 5051 매핑과 불일치**
4. **tbm-fix** (`_recovery_tbm_fix.py`): tbm 재재생성 + 마지막 인자에 `python tbm_app.py` 명시 → 5051 listening 정상화

### 최종 결과
- attendance-app: HTTP 5050 → **302** ✅
- attendance-tbm: HTTP 5051 → **404** ✅ (Flask 응답, root path 없을 뿐)
- 양 컨테이너 모두 DB 인증 정상 (1045 에러 0건)
- 일회용 진단 스크립트 6개는 `_archive/`에 보관 (gitignore `_archive/_recovery_*.py` 패턴으로 추적 X)

### 후속 과제 5건 (별도 작업으로)

#### A. 구조 개선 (높음)
1. **이미지 정기 재빌드 정책 수립** — 현재 `docker cp + restart` 방식은 컨테이너 재생성 시 모든 변경 손실. 주기적(주/월 단위) `python deploy_and_restart.py --rebuild`로 이미지 baseline을 최신화하거나, CI/CD로 이미지 빌드 파이프라인 구축 검토.
2. **`Dockerfile.tbm` CMD 점검** — 이미지 default CMD가 `python app_maria.py`로 잘못 박혀있을 가능성. `CMD ["python", "tbm_app.py"]`로 명시되어 있는지 확인. 안 그러면 재빌드 시 또 같은 사고.

#### B. 보안/운영 (중간)
3. **`docker-compose.yml` 평문 비번 정리** (이전 합의) — L32/L56/L75의 `DB_PASSWORD: "..."` 평문 + L83 healthcheck의 평문 비번. compose도 `--env-file` 또는 `${VAR}` 참조로 통일.
4. **마스킹 정규식 강화** — 일회용 진단 도구에서 YAML/JSON quoted value(`KEY: "VALUE"`) 마스킹 누락 발견. 정규식 패턴: `["\']?[^\s"\'\n]+["\']?` 보강 (이미 `_recovery_tbm_fix.py`엔 적용됨).

#### C. 운영 모니터링 (낮음)
5. **Tuya API quota 초과** — `attendance-tbm` 로그에 반복 발생: `[Tuya] smoke-001 상태 조회 실패: code=28841004, msg='Please upgrade to the official version: Your quota of Trial Edition is used up.'`. **IoT 화재 알람 기능 영향 가능** — Tuya 유료 전환 또는 폴링 간격 조정 검토.

### 교훈 (재발 방지)
- `docker run` 명령 작성 시 **CMD/ENTRYPOINT 인자 명시 필수** (이미지 default가 의도와 다를 수 있음)
- 컨테이너 재생성 = 이미지 baseline으로 회귀. docker cp로 패치된 부분은 별도 보존 필요
- `load_dotenv()` `override=False` 기본값 인지 — `--env-file`로 주입된 옛 env가 있으면 .env 파일이 무시됨. 재생성하지 않는 한 환경변수 갱신 안 됨

### 복구 사용 커밋
- 추가 변경 없음 (모든 작업이 `docker run` / `docker cp` / `docker restart` 만으로 진행, 로컬 코드 수정 없음)
- `.gitignore`에 `_archive/_recovery_*.py` 추가는 deploy의 _git_auto_commit으로 함께 처리됨


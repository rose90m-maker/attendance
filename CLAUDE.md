# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Korean workplace attendance management system (㈜태인 근태관리). Flask web app deployed as a Docker container on a Synology NAS. Includes adjacent subsystems: TBM (Tool Box Meeting), MES (제조실행), 위험물 관리, 교육 관리, RAG 문서 검색, Tuya 화재센서, KEPCO 전기요금 수집, CAPS 출입통제 동기화.

## Architecture

### Main Flask app (`app_maria.py`)
- Single large monolith. 컨테이너에도 **`app_maria.py` 그대로** 들어가고
  gunicorn 이 `app_maria:app` 으로 띄운다 (이미지 CMD 확인, 2026-08-11).
  > ⚠️ 컨테이너 안의 `/app/app.py` 는 2026-04-14 자 유물이고 **아무도 안 쓴다.**
  > 배포 반영 여부를 그 파일로 확인하면 "반영 안 됨"으로 잘못 읽는다.
  > 확인은 `_archive/_verify_msgfav.py` 처럼 `app_maria.py` 를 봐야 한다.
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
- **AI 서버 (192.168.100.6)** — Ollama API `:11434` (safety_ai_bp 가 사용, 비전모델 `qwen2.5vl:32b`), 채팅 웹 UI `:3000` (Open WebUI 로 추정).

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

**Deploy**: `python deploy_and_restart.py` (handles SCP → `docker cp` → `docker restart`, and auto-commits dirty git state with message `배포: <changed files>`, prepending to `static/dev_history.json`).

**이미지 재빌드**: `python deploy_and_restart.py --rebuild` 또는 `python rebuild_containers.py`.
`--rebuild` 는 코드 전송까지만 하고 `rebuild_containers.py` 에 위임한다. 재빌드는
컨테이너를 재생성하므로 **컨테이너당 30초~1분 다운타임**이 있고, Chromium 설치 탓에
빌드에만 10~15분 걸린다(빌드 중에는 무중단). 재생성 전 현재 이미지를
`backup-YYYYMMDDHHMM` 으로 태그해 두므로 문제 시 `--rollback` 으로 되돌린다.

> ⚠️ 예전 `--rebuild` 는 `docker compose`(공백형)를 불렀는데 Synology 에는 그 명령이
> 없어 **조용히 실패하면서 화면에는 성공으로 표시**됐다. 그 탓에 이미지가 2026-05-26 자로
> 3개월간 멈춰 있었고, 새 파이썬 패키지는 컨테이너에 직접 `pip install` 해서 쓰다가
> 재생성 시 소실될 위험을 안고 있었다 (2026-08-07 해소).
> Dockerfile 을 고쳤으면 **재빌드 전에 코드가 NAS 로 전송됐는지 확인**할 것 —
> staging 의 옛 Dockerfile 로 빌드되면 폰트·Chromium 이 빠진 이미지가 나온다.

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

---

## 🔧 재빌드 실패 + 해소 (2026-08-09)

### 증상
`python deploy_and_restart.py --rebuild` 가 계속 실패. 8/7 부터 여러 차례 시도.
```
❌ 실패 — 위 로그를 확인하세요
❌ 재빌드 실패 — 컨테이너 상태를 확인하세요.
```
**서비스 자체는 정상**이었다 (롤백이 먹었고 app/tbm 모두 가동 중). 다운타임 없음.

### 근본 원인 — `Dockerfile` 의 `freetds-dev` 한 줄
```
Unable to locate package freetds-dev
```
apt 설치가 실패하면 `RUN` 이 죽고 빌드 전체가 중단된다. **`pymssql 2.3.13` 은 FreeTDS 를
내장한 manylinux 휠을 제공하므로 이 시스템 패키지가 애초에 필요 없었다.** 제거 후 정상 빌드.

### 진단이 오래 걸린 이유 (다음에 줄이려면)
빗나간 가설 셋 — DB_PASSWORD 불일치, 디스크 부족, Chromium 단계. 전부 아니었다.
**답은 처음부터 실행 로그에 있었다.** 컨테이너·이미지 상태만 보고 역추적하려 하면
시간이 배로 든다. 실패 로그부터 확보할 것. 터미널에 안 남았으면
`~/.claude/projects/*/*.jsonl` (Claude Code 세션 기록)에 남아 있다.

### 함께 고친 결함 4건

| 파일 | 문제 | 수정 |
|---|---|---|
| `Dockerfile` | `freetds-dev` 로 빌드 실패 | 제거 + `import pymssql` 검증 추가 |
| `deploy_and_restart.py` | `.dockerignore` 가 전송 목록에 없어 `COPY . .` 가 **`.env` 를 이미지 레이어에 구움** (운영 이미지 `/app/.env` 2093B 확인) | `.dockerignore`·`Dockerfile.tbm` 전송 추가 |
| `rebuild_containers.py` | 빌드 성공 판정이 **문자열 매칭** — 빌더마다 문구가 다르고 sudo 프롬프트가 섞여 오판 | 종료 코드(`BUILD_EXIT_$?`)로 변경 |
| `rebuild_containers.py` | `sudo()` 가 `startswith("Password: ")` 만 봐서 `Password:`(공백 없음) 를 못 뗌 → 위 판정 오염 | 정규식으로 변경 |
| `rebuild_containers.py` | `cmd` 하드코딩이 이미지 CMD 를 덮어써 **운영이 gunicorn 대신 Flask 개발 서버로 구동** | 덮어쓰기 제거 + `expect_proc` 로 기동 후 검증 |
| `rebuild_containers.py` | 롤백 시 백업 태그 없는 컨테이너를 조용히 건너뜀 (tbm 이 실제로 그 상태) | 경고 명시 |

### 결과
```
attendance-app  BUILD_EXIT_0 → 302 → 프로세스 확인: gunicorn ✅
attendance-tbm  BUILD_EXIT_0 → 200 → 프로세스 확인: tbm_app.py ✅
```
원천징수영수증 PDF 발급까지 정상 확인 (pymssql·chromium·나눔폰트 전부 동작).
`attendance-tbm:backup-*` 태그가 처음 생성되어 이제 양쪽 롤백이 가능하다.

### 진단 스크립트 (`_archive/`, 재사용 가능)
전부 **조회 전용**이며 컨테이너를 변경하지 않는다. `.env` 의 `NAS_*` 를 그대로 쓴다.
- `_archive/_check_containers.py` — ps/inspect/logs/stats/포트/OOM
- `_archive/_check_build.py` — `:buildtest` 태그로 **무중단** 빌드 재현
- `_archive/_verify_image.py` — 이미지 안에서 pymssql/chromium/폰트/gunicorn + `.env` 혼입 검증. 인자로 이미지명
- `_archive/_build_progress.py` — 빌드 진행 확인 (레이어 개수)

> 💡 NAS 는 **SFTP 가 꺼져 있고**(paramiko `open_sftp()` → `Channel closed`) **레거시 빌더**를 쓴다
> (BuildKit 의 `Build Cache` 는 증가하지 않으므로 진행 지표로 쓰면 안 된다 — 중간 이미지 개수를 볼 것).
> 또 `sudo sh -c '...'` 안에서 `docker --format '{{...}}'` 처럼 작은따옴표를 겹치면 조기에 닫힌다.
> 명령을 base64 로 실어 보내면 이 문제가 사라진다.

### 남은 것
- `Dockerfile` 의 `import pymssql` 검증 줄이 `playwright install` **앞**에 있어, 이 줄이 바뀌면
  chromium 레이어 캐시가 통째로 무효화된다 (재빌드 20분 추가). **뒤로 옮기면 해소.**
- 빌드 캐시 14.33GB / 회수 가능 이미지 9.5GB — `docker builder prune` 검토 (디스크는 1.7T 여유라 급하지 않음)
- `rebuild_containers.py` 에 `backup-*` 태그 정리 로직 없음 — 계속 누적된다


---

## 🧾 원천징수영수증 — 결함 17건 해소, 국세청 신고파일 기준 189명 전수 일치 (2026-08-10)

`wht_receipt.py` 가 ERP 서식 템플릿(`_TWPRAdjTotAbrIncomeHTML`)에 값을 채워
원천징수영수증을 만든다. **ERP 발급본 PDF 와 실제로 대조**하기 전까지는
전 직원 자동검사가 189명 전원 통과였는데, 발급본 3장을 대조하니 결함이 9건 나왔다.

### 자동검사가 왜 못 잡았나 (핵심 교훈)
`wht_watch.py` 는 "ERP DB 가 가진 **합계** 금액이 서식 **어딘가에** 있는가" 만 본다.
그래서 다음이 전부 통과한다.
- 칸이 밀려도 (숫자는 있으니까)
- 열이 통째로 비어도 (합계만 맞으면)
- 라벨이 틀려도 (숫자만 보니까)
- 같은 숫자가 다른 칸에도 있으면 빠진 칸을 못 본다 (16.계 총계가 그랬다)

**발급본 대조가 유일한 정답지다.** 값 검사만으로는 구조적 결함이 안 보인다.

### 고친 것

| # | 결함 | 영향 |
|---|---|---|
| 1 | 16.계 총계 칸(`Data3_TotAmt`)에 값을 넣는 코드가 없어 빈칸 | 189명 |
| 2 | 서식 라벨 `26.본인` (ERP 템플릿 원본 오타, 발급본은 24) | 189명 |
| 3 | 1쪽 종(전)근무지 열 전체가 하드코딩 빈칸 + 주(현) 열에 합계 혼입 | 5명 |
| 4 | ⑫감면기간 종(전) 열 빈칸 | 5명 |
| 5 | Ⅱ 비과세·감면 소득명세가 빈 행 14개 (중소기업 취업자 감면 누락) | 감면 대상자 |
| 6 | 52.조특법§30 / 54.세액감면 계가 ERP 매핑 없이 계산값 | 감면 대상자 |
| 7 | 75.주(현) 기납부세액이 ERP 아닌 급여집계 출처 (73·77과 원 단위 어긋남 위험) | 전원 |
| 8 | 61~63번 공제대상금액 칸이 빔 (세액공제액만 매핑) | 해당자 |
| 9 | 3쪽 부양가족 명세 전체 + `Data8_repeat` 마커 누락 | 전원 |

### ERP 데이터 위치 (재조사 금지 — 여기 다 있다)
- `_TWPRAdjTotResultDtl` — 연말정산 계산결과. `Amt`=한도적용 후, `OrgAmt`=대상금액.
  항목명은 `_TWPRAdjTotItem.AdjItemName`. **ERP 는 계산값을 DB 에 갖고 있다**
  (`wht_receipt.py` 헤더의 옛 주석이 반대로 적혀 있었고 그게 버그 두 개의 원인이었다)
- `_TWPRAdjTotNtsIncomeSum` — 근무처별 소득명세. `Amt`=종(전) **포함** 합계,
  `PreAmt`=종(전)분. 주(현) = `Amt - PreAmt`. `SMPerCoAllType=3502001` 하나뿐이고
  이건 "당사분"이 **아니다**
- `_TWPRAdjTotPreWork` — 종(전)근무지 회사명·사업자번호·근무기간·감면기간
- `_TWPRAdjTotPreWorkDtl` — 종(전)근무지 **별** 금액 (`Seq` + `NtsItemSeq`)
- `_TWPRAdjTotEmpDepenList` — 3쪽 부양가족별 금액. **컬럼명이 서식 토큰명과 1:1**
- `_TWPRAdjTotPrintMapping(.Dtl)` — Ⅱ영역 인쇄코드(`Remark`='T13')와
  표시명(`Dtl.ForName`, `LanguageSeq=1`). `Seq` = `NtsItemSeq` 로 잇는다.
  `SMType` 3931003=비과세 / 3931006=감면

### 검증 도구 (세 겹)
```bash
python3 wht_release.py --yes                 # 검증 통과 시에만 배포 + 사후 확인
python3 wht_watch.py --force                 # 전 직원 값 대조 (매일 07:30 자동)
python3 _archive/_wht_cells.py --name 홍길동   # 금액이 '어느 칸'에 들어갔나
python3 _archive/_check_prework.py           # 종(전)근무지 열 + 74/75/77 검산
python3 _archive/_check_nontax.py            # Ⅱ영역 + 52/54 세액감면
python3 _archive/_diff_all_pdf.py 발급본.pdf  # ★ 발급본과 전수 대조 (가장 강함)
python3 _archive/_pick_samples.py            # 발급본을 누구 걸 뽑아야 다 덮이는지
```
`_diff_all_pdf.py` 가 회귀 테스트의 본체다. ERP 에서 전 직원을 한 번에 출력해
넘기면 남은 차이가 한 번에 나온다. 발급본만 있으면 언제든 다시 돌릴 수 있다.

### 알아둘 것
- 차감징수세액(77)은 **10원 미만 절사**다. `73-74-75-76` 과 몇 원 다른 건 정상
- 서식 토큰을 문자열 `find` 로 찾으면 안 된다 — `Data6_Amt4` 가 `Data6_Amt40` 에 걸린다
- pymssql 은 금액을 `decimal.Decimal` 로 준다. `isinstance(v, (int, float))` 면 다 놓친다
- 20 / 20-1 계 토큰 이름(`Data4_DeducSum*` / `Data4_NonTaxSum*`)은 뜻과 어긋나 보인다.
  `nontax_sum_tokens()` 가 서식 행 글자를 읽어 판단한다 — 이름으로 추측하지 말 것

### 2차 라운드 — 발급 없이 잡은 결함 6건 (같은 날 저녁)
발급본 3장으로 9건을 잡은 뒤, 발급 없이 가는 검증 두 개를 더 만들었다.

1. **산술 항등식 전수검사** (`_archive/_check_identities.py`) — 서식에 인쇄된
   검산식 10종을 189명 전원에게 돌린다. 105명 위반에서 시작해 다음을 잡았다:
   72.결정세액 출처(계산기→ERP), 64 기부금 계산기 잔값, 61~63 표준세액공제자
   잔값, 고향사랑기부금 매핑, 장애인전용보장성(Amt71/72) 배선.
2. **국세청 전자신고 파일 전수대조** (`_archive/_diff_efile.py`) — ERP
   「연말정산_처리/신고」의 [파일생성]으로 만든 전산매체(C+사업자번호 파일,
   3305002, A~K 레코드 2,010B)를 레이아웃 표(`_TWPRAdjTotRecordItem`)로 파싱해
   189명 전 칸을 대조한다. 여기서 추가로: **비과세 야간근로수당 행**(비과세는
   `NtsIncomeSum` 이 아니라 `_TWPRAdjTotNtsNonTaxSum` 에 있다 — 9명),
   **주택자금 ㉯ 9행 미배선**(7명), **연금계좌 59/60**, **42.신용카드**(ERP 값이
   곧 공제금액), **70.월세 한도**, **56.혼인세액공제(2025 신설) 미배선**.

최종 상태: **신고파일 대조 189/189 완전일치 · 항등식 189/189 통과.**
검증 3종이 `wht_release.py` 게이트로 매 배포마다 돈다.

핵심 규칙 (재발 방지): **서식 칸은 ERP 값만 쓴다.** 계산기(wht_calc)가 채운 값이
ERP 에 근거 없으면 지운다 — 한도적용·표준세액공제 선택 등 ERP 만 아는 사정이 있다.
전산매체 파일(`C*.759`)은 주민번호 평문이라 gitignore 에 있고, 대조 후 지운다.

### 남은 미확인
- ~~2024년 귀속~~ — 검증 완료 (2026-08-10 저녁). 결함 1건: 3쪽 신용카드
  전년/금년 토큰의 대소문자 불일치(`LastYear` vs `Lastyear`)로 159명 8칸 빈칸
  → 토큰 치환을 대소문자 무시로 보완, 205명 항등식 전원 통과. 2024 ERP 항목명
  '결혼세액공제'(2025는 '혼인') 별칭 추가. 신고파일 대조는 미실시 — 필요 시
  ERP 에서 2024 귀속 [파일생성] 후 `_diff_efile.py --yy 2024 --file …`
- **2023년 이전 귀속** — 미검증. 요청이 생기면 2024 와 같은 순서로:
  항등식(`_check_identities.py --yy`) → 신고파일 대조
- **부속명세(Detail)** — 29,079자, `load_template()` 에서 제외 중
- 부양가족 주민등록번호 — varbinary 암호화라 복호화 불가, 담당자가 수기 입력
- 발급본과 대조 완료: 지창구(3쪽 전체 0/0) · 김미선 · 이재현 (2025 귀속)
- ~~발급본 8장 추가 대조 계획~~ — 신고파일 전수대조가 대체, 불필요

# Attendance Project — CLAUDE.md

## Project Overview

Korean workplace attendance management system (근태관리). Runs as a Flask web app deployed on a Synology NAS.

## Architecture

| Component | Description |
|---|---|
| `app_maria.py` | Main Flask app (MariaDB version) — deployed to NAS as `app.py` |
| `rag_server.py` | FastAPI RAG server on Mac Mini, port 8765 |
| `rag_docs.py` | Document indexing + search (ChromaDB + FTS) |
| `tuya_fire.py` | Tuya IoT fire/smoke sensor integration |
| `tuya_poller_local.py` | Local Tuya device poller |
| `deploy_and_restart.py` | SSH deploy + Flask restart script |
| `chroma_db/` | ChromaDB vector store for RAG |
| `templates/` | Jinja2 HTML templates |
| `static/` | CSS, images, manifest |
| `uploads/` | Uploaded documents |

## Stack

- **Backend**: Flask (Python), FastAPI (RAG server)
- **Database**: MariaDB — `127.0.0.1:3307`, db: `attendance`
- **Vector DB**: ChromaDB (local, `chroma_db/`)
- **LLM APIs**: OpenAI, Groq, Gemini, Cohere (keys in `.env`)
- **IoT**: Tuya cloud API (fire/smoke sensors)
- **Notifications**: Telegram bot

## Deployment

- **Target**: Synology NAS at `192.168.100.11` (port 22, user: `admin`)
- **App path on NAS**: `/volume1/web/attendance/`
- **Flask port**: `5050`
- **RAG server port**: `8765` (runs on Mac Mini, separate process)
- **Deploy command**: `python deploy_and_restart.py`
- **NAS Python**: `/opt/bin/python3`
- **Logs on NAS**: `/var/log/attendance.log`

## Key Conventions

- All UI text and code comments are in Korean
- `app_maria.py` is the local dev file — it gets deployed as `app.py`
- DB connection via `_conn()` helper (returns `pymysql.connect(**MARIA)`)
- Session key `e_id` = logged-in employee ID; `role == "admin"` = admin
- Korean public holidays via `holidays.KR`

## Sensitive Files — Do Not Commit

- `.env` — API keys (OpenAI, Groq, Gemini, Cohere)
- `deploy_and_restart.py` — contains NAS credentials in plaintext
- `app_maria.py` — contains Telegram token and MariaDB password inline

## Running Locally

```bash
# Activate venv
source .venv/bin/activate

# Run Flask app (local)
python app_maria.py

# Run RAG server
bash start_rag.sh
# or: uvicorn rag_server:app --host 0.0.0.0 --port 8765
```

## Database Schema Notes

Key tables (inferred from code):
- `lp_group_members`, `lp_group_reviewers` — leave plan group membership
- `leave_records` — employee leave (연차/반차)
- `annual_leave` — annual leave balance per employee/year

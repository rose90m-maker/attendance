#!/usr/bin/env python3
"""wht_watch.py — 원천징수영수증 자동 감시 (읽기 전용 + 알림)

ERP 는 연말정산 계산 결과를 _TWPRAdjTotResultDtl 에 저장한다
(AdjItemSeq 별 Amt=한도적용 공제금액, OrgAmt=대상금액. 2026-08-10 확인).
그래서 발급본 PDF 없이 **ERP 자체 값을 정답지로** 전 직원을 대조할 수 있다.

이 스크립트를 하루 한 번 돌려두면, 사람이 아무것도 안 해도
어긋나는 순간에만 텔레그램으로 알린다. 정상일 때는 조용하다.

■ 하는 일
  1. 전 직원 영수증을 렌더 (파일로 저장하지 않는다)
  2. ERP 가 가진 금액이 서식에 실제로 찍히는지 대조
  3. 지난번보다 나빠졌을 때만 텔레그램 발송
  4. 결과를 wht_watch.log 에 남긴다

■ 안 하는 일
  ERP·NAS·DB 를 고치지 않는다. SELECT 와 텔레그램 발송뿐이다.

사용:
  python3 wht_watch.py               한 번 실행
  python3 wht_watch.py --install     매일 07:30 자동 실행 등록 (macOS)
  python3 wht_watch.py --uninstall   등록 해제
  python3 wht_watch.py --test        텔레그램 발송 확인
  python3 wht_watch.py --force       변화가 없어도 결과 발송
"""
import argparse
import contextlib
import io
import json
import os
import plistlib
import re
import subprocess
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(HERE, ".env"))
except ImportError:
    pass

STATE = os.path.join(HERE, ".wht_watch_state.json")
IGNORE_FILE = os.path.join(HERE, "wht_ignore_items.json")
LOG = os.path.join(HERE, "wht_watch.log")
LABEL = "com.taein.wht-watch"
PLIST = os.path.expanduser(f"~/Library/LaunchAgents/{LABEL}.plist")
MIN_AMT = 1000          # 이 금액 미만은 노이즈로 무시


def log(msg):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def notify(text):
    """텔레그램 발송. 토큰이 없으면 조용히 건너뛴다."""
    tok = os.environ.get("TELEGRAM_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        log("텔레그램 설정이 없어 발송을 건너뜁니다 (.env 의 TELEGRAM_TOKEN/CHAT_ID)")
        return False
    try:
        import requests
        r = requests.post(
            f"https://api.telegram.org/bot{tok}/sendMessage",
            json={"chat_id": chat, "text": text}, timeout=20)
        ok = r.status_code == 200
        log(f"텔레그램 발송 {'성공' if ok else '실패 ' + r.text[:120]}")
        return ok
    except Exception as e:
        log(f"텔레그램 발송 오류: {type(e).__name__}: {e}")
        return False


# ── 자동 실행 등록 (macOS LaunchAgent) ───────────────────────
def _python():
    venv = os.path.join(HERE, ".venv", "bin", "python3")
    return venv if os.path.exists(venv) else sys.executable


def install():
    os.makedirs(os.path.dirname(PLIST), exist_ok=True)
    plist = {
        "Label": LABEL,
        "ProgramArguments": [_python(), os.path.join(HERE, "wht_watch.py")],
        "WorkingDirectory": HERE,
        "StartCalendarInterval": {"Hour": 7, "Minute": 30},
        "StandardOutPath": os.path.join(HERE, "wht_watch.out"),
        "StandardErrorPath": os.path.join(HERE, "wht_watch.err"),
        "RunAtLoad": False,
    }
    with open(PLIST, "wb") as f:
        plistlib.dump(plist, f)
    subprocess.run(["launchctl", "unload", PLIST],
                   capture_output=True)
    r = subprocess.run(["launchctl", "load", PLIST], capture_output=True, text=True)
    if r.returncode == 0:
        log(f"등록 완료 — 매일 07:30 자동 실행\n  {PLIST}")
        log("맥이 꺼져 있으면 켜진 뒤 다음 주기에 실행됩니다.")
    else:
        log(f"등록 실패: {r.stderr.strip()[:200]}")
    return r.returncode


def uninstall():
    subprocess.run(["launchctl", "unload", PLIST], capture_output=True)
    if os.path.exists(PLIST):
        os.remove(PLIST)
    log("등록 해제 완료")
    return 0


# ── 검증 ─────────────────────────────────────────────────────
def load_ignore():
    """서식에 인쇄되지 않는 ERP 내부 항목(AdjItemSeq).

    ERP 는 한도 계산용 중간값과 합산값도 저장한다. 예를 들어 '결정세액계'는
    소득세와 지방소득세를 더한 값인데, 서식은 둘을 따로 찍으므로 그 합계는
    어디에도 안 나온다. 이런 걸 오류로 잡으면 진짜 누락이 묻힌다.

    목록은 추측이 아니라 발급본과 검증된 사람 기준으로 산출한다:
      python3 _archive/_verify_wht_all.py --calibrate 지창구
    """
    if not os.path.exists(IGNORE_FILE):
        return set()
    try:
        return set(json.load(open(IGNORE_FILE, encoding="utf-8"))
                   .get("ignore_seq", []))
    except Exception:
        return set()


def verify(yy):
    import wht_receipt as W
    ignore = load_ignore()
    conn = W._conn()
    cur = conn.cursor()

    cur.execute("SELECT AdjItemSeq, AdjItemName FROM _TWPRAdjTotItem WHERE YY=%s", (yy,))
    item = {r[0]: (r[1] or "").strip() for r in cur.fetchall()}

    cur.execute("""SELECT EmpSeq, EmpID, EmpName FROM _TWPRAdjTotResult
                   WHERE YY=%s ORDER BY EmpName""", (yy,))
    emps = cur.fetchall()

    problems = []
    rendered = 0
    for emp_seq, emp_id, name in emps:
        emp_id = str(emp_id).strip()
        name = (name or "").strip()

        # 서식에 인쇄되는 항목만 본다.
        # ERP 는 115개쯤 저장하지만 서식이 찍는 건 40개 남짓이다. 한도·중간
        # 계산값까지 전부 대조하면 끝없이 오탐이 난다 (2026-08-10, 184명 오탐).
        # wht_receipt.ERP_ITEM_NAME 에 매핑된 항목 = 서식에 나가야 할 항목이다.
        cur.execute("""SELECT i.AdjItemName, d.Amt
                       FROM _TWPRAdjTotResultDtl d
                       LEFT JOIN _TWPRAdjTotItem i
                         ON i.YY=d.YY AND i.AdjItemSeq=d.AdjItemSeq
                       WHERE d.YY=%s AND d.EmpSeq=%s""", (yy, emp_seq))
        erp = {}
        for nm, amt in cur.fetchall():
            nm = (nm or "").strip()
            if nm not in W.ERP_ITEM_NAME:
                continue
            try:
                iv = int(float(amt or 0))
            except (TypeError, ValueError):
                continue
            if abs(iv) >= MIN_AMT:
                erp.setdefault(iv, set()).add(nm)

        try:
            with contextlib.redirect_stdout(io.StringIO()):
                html, _t, _f, _m = W.render(cur, emp_id, yy)
            rendered += 1
        except Exception as e:
            problems.append({"name": name, "emp": emp_id, "kind": "render",
                             "detail": f"{type(e).__name__}: {str(e)[:120]}"})
            continue

        text = re.sub(r"<[^>]+>", " ", html)
        got = {int(m.replace(",", ""))
               for m in re.findall(r"-?\d{1,3}(?:,\d{3})+", text)}
        absent = [v for v in erp if v not in got]
        if absent:
            names = sorted({n for v in absent for n in erp[v]})
            problems.append({"name": name, "emp": emp_id, "kind": "missing",
                             "count": len(absent),
                             "detail": ", ".join(names[:6])})

    conn.close()
    return {"total": len(emps), "rendered": rendered, "problems": problems,
            "ignored": len(ignore)}


def signature(res):
    """알림 중복을 막기 위한 결과 요약본"""
    return sorted((p["kind"], p["emp"], p.get("count", 0)) for p in res["problems"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yy", default=str(datetime.now().year - 1))
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    if a.install:
        return install()
    if a.uninstall:
        return uninstall()
    if a.test:
        return 0 if notify("원천징수영수증 감시기 발송 테스트입니다.") else 1

    log(f"=== 검증 시작 ({a.yy} 귀속) ===")
    try:
        res = verify(a.yy)
    except Exception as e:
        log(f"검증 자체가 실패: {type(e).__name__}: {e}")
        notify(f"⚠️ 원천징수영수증 감시기가 실패했습니다.\n"
               f"{type(e).__name__}: {str(e)[:200]}")
        return 1

    n = len(res["problems"])
    log(f"대상 {res['total']}명 · 렌더 {res['rendered']}명 · 이상 {n}명")
    for p in res["problems"][:20]:
        log(f"  ▸ {p['name']}({p['emp']}) {p['kind']} — {p['detail']}")

    prev = {}
    if os.path.exists(STATE):
        try:
            prev = json.load(open(STATE, encoding="utf-8"))
        except Exception:
            prev = {}

    sig = signature(res)
    changed = prev.get("signature") != [list(x) for x in sig]

    if n and (changed or a.force):
        lines = [f"⚠️ 원천징수영수증 이상 {n}명 / {res['total']}명 ({a.yy} 귀속)"]
        for p in res["problems"][:8]:
            lines.append(f"· {p['name']} — {p['detail'][:60]}")
        if n > 8:
            lines.append(f"… 외 {n-8}명")
        lines.append("\n확인: python3 _archive/_verify_wht_all.py --all")
        notify("\n".join(lines))
    elif not n and prev.get("problem_count", 0) and changed:
        notify(f"✅ 원천징수영수증 이상이 해소됐습니다 ({res['total']}명 전원 정상)")
    elif a.force:
        notify(f"✅ 원천징수영수증 {res['total']}명 전원 정상 ({a.yy} 귀속)")
    else:
        log("변화 없음 — 알림을 보내지 않습니다")

    try:
        json.dump({"at": datetime.now().isoformat(),
                   "yy": a.yy,
                   "total": res["total"],
                   "problem_count": n,
                   "signature": [list(x) for x in sig]},
                  open(STATE, "w", encoding="utf-8"), ensure_ascii=False)
    except OSError as e:
        log(f"상태 저장 실패: {e}")

    log("=== 검증 끝 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())

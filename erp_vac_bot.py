#!/usr/bin/env python3
"""영림원 K-System Ace 휴가신청 자동 입력 봇 (Playwright).

안전 원칙:
  - 기본은 드라이런(--apply 없이): 저장 버튼을 절대 누르지 않고 화면만 채워 스크린샷.
  - --apply 일 때만 저장. 저장 후 ERP DB를 다시 읽어 실제 반영 검증(별도 단계).
  - 대상은 attendance 승인휴가 중 ERP 미입력분만 (leave_erp 대조 로직과 동일).

단계별 실행:
  python erp_vac_bot.py --step login          # 로그인만 검증
  python erp_vac_bot.py --step menu           # 휴가신청 화면 진입 확인
  python erp_vac_bot.py --step fill           # 1건 드라이런(저장 안함)
  python erp_vac_bot.py --apply               # 실제 입력 (확인 후)
"""
import os
import sys
import time

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

ERP_URL = os.environ.get("ERP_WEB_URL", "http://210.118.143.10:8100")
ERP_ID = os.environ["ERP_WEB_ID"]
ERP_PWD = os.environ["ERP_WEB_PWD"]

SHOT_DIR = "/tmp/erp_bot"
os.makedirs(SHOT_DIR, exist_ok=True)


def shot(page, name):
    p = os.path.join(SHOT_DIR, name + ".png")
    page.screenshot(path=p, full_page=False)
    print(f"  📸 {p}")
    return p


def do_login(page):
    page.goto(ERP_URL, wait_until="networkidle")
    time.sleep(1)
    # 로그인 폼 (실측 DOM: 보이는 아이디는 #txtLoginId, 비번은 #inputLoginPwd)
    page.fill('#txtLoginId', ERP_ID)
    page.fill('#inputLoginPwd', ERP_PWD)
    shot(page, "01_login_filled")
    page.keyboard.press("Enter")
    page.wait_for_load_state("networkidle")
    time.sleep(3)
    shot(page, "02_after_login")
    # 로그인 성공 판정: 로그인 폼이 사라졌는가
    still_login = page.locator('input[placeholder="비밀번호"]').count() > 0
    print("  로그인 성공:" , (not still_login))
    return not still_login


def dump_header(page):
    """상단 헤더의 클릭 가능한 요소들을 덤프 (셀렉터 파악용)."""
    js = """
    () => {
      const out = [];
      document.querySelectorAll('*').forEach(el => {
        const r = el.getBoundingClientRect();
        if (r.top >= 0 && r.top < 42 && r.x > 1350 && r.width > 4 && r.height > 4) {
          out.push({tag: el.tagName, id: el.id||'', cls: (el.className||'').toString().slice(0,70),
                    title: el.title||'', text: (el.textContent||'').trim().slice(0,20),
                    x: Math.round(r.x), w: Math.round(r.width)});
        }
      });
      return out.slice(0, 50);
    }
    """
    for item in page.evaluate(js):
        print("   ", item)


def goto_vacation(page):
    """상단 메뉴검색으로 '휴가신청' 화면 열기."""
    # 우상단 '프로그램조회'(#btnProgramQuery) → 검색창에 휴가신청
    page.click('#btnProgramQuery')
    time.sleep(1.5)
    shot(page, "03_search_open")
    # 열린 패널의 보이는 입력창에 실제 키 입력 (fill 은 Angular 이벤트 미발생)
    box = page.locator('input:visible').last
    box.click()
    box.press_sequentially("휴가신청", delay=60)
    page.keyboard.press("Enter")
    # 결과 목록 로딩 대기 후, '근태신청' 경로가 붙은 관리자용 '휴가신청' 클릭
    page.wait_for_selector('text=근태신청', timeout=10000)
    time.sleep(1)
    shot(page, "04_search_typed")
    # 첫 번째 결과(휴가신청 — 급여-[급여] 근태신청) 좌표 찾아 실제 마우스 클릭
    pos = page.evaluate("""
    () => {
      for (const el of document.querySelectorAll('a.txtProgramName')) {
        if ((el.textContent || '').trim() !== '휴가신청') continue;
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.height > 0)
          return {x: r.x + r.width / 2, y: r.y + r.height / 2};
      }
      return null;
    }
    """)
    if not pos:
        print("  ❌ '휴가신청' 결과 항목을 못 찾음 — DOM 덤프:")
        print("     iframe 수:", page.frames.__len__())
        dbg = page.evaluate("""
        () => {
          const out = [];
          document.querySelectorAll('*').forEach(el => {
            const r = el.getBoundingClientRect();
            const t = (el.textContent||'').trim();
            if (r.top > 250 && r.top < 650 && r.width > 20 && t && t.length < 40)
              out.push({tag: el.tagName, cls: (el.className||'').toString().slice(0,45),
                        kids: el.children.length, y: Math.round(r.top), t: t.slice(0,30)});
          });
          return out.slice(0, 25);
        }
        """)
        for d in dbg:
            print("    ", d)
        return False
    print("  결과 클릭 좌표:", pos)
    page.mouse.click(pos["x"], pos["y"])
    page.wait_for_load_state("networkidle")
    time.sleep(3)
    shot(page, "06_vacation_screen")
    # 진입 판정: 화면이 iframe 안에 뜨므로 모든 프레임에서 '내역생성' 탐색
    fr = find_vac_frame(page)
    print("  휴가신청 프레임:", (fr.url[-45:] if fr else None))
    return fr is not None


def _grid_frame(page):
    """SS1 캔버스 그리드가 실제로 그려진 프레임 반환."""
    best = None; best_h = -1
    for f in page.frames:
        try:
            if f.locator('#SS1_cvp_vp').count() == 0:
                continue
            h = f.evaluate("() => document.documentElement.scrollHeight || 0")
        except Exception:
            continue
        if h >= best_h:
            best, best_h = f, h
    return best


def load_targets(limit=None):
    """attendance 앱에서 ERP 미입력 대상자 목록 가져오기 (leave_erp 대조 결과)."""
    import json
    import urllib.request
    url = os.environ.get("ATT_API", "http://192.168.100.11:5050") + "/api/leave_erp_pending"
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            data = json.load(r)
        rows = data.get("rows", [])
    except Exception as e:
        print(f"  ⚠️ 대상자 API 실패({e}) — 로컬 DB 직접 조회로 대체")
        rows = _load_targets_db()
    return rows[:limit] if limit else rows


def _load_targets_db():
    """API 불가 시: attendance DB 직접 조회 (ERP 대조는 생략, 승인휴가 전체)."""
    import pymysql
    from datetime import date, timedelta, datetime as _dt
    from collections import defaultdict
    c = pymysql.connect(host=os.environ.get("DB_HOST", "192.168.100.11"),
                        port=int(os.environ.get("DB_PORT", 3307)),
                        user=os.environ.get("DB_USER", "root"),
                        password=os.environ["DB_PASSWORD"],
                        database="attendance", charset="utf8mb4")
    cur = c.cursor()
    today = date.today()
    frm = (today - timedelta(days=45)).strftime("%Y%m%d")
    to = (today + timedelta(days=60)).strftime("%Y%m%d")
    cur.execute("""SELECT u.name, u.idno, lr.leave_date, lr.leave_type
                   FROM leave_records lr LEFT JOIN tuser u ON lr.e_id=u.id
                   WHERE lr.status='승인' AND lr.leave_date BETWEEN %s AND %s
                   ORDER BY u.name, lr.leave_date""", (frm, to))
    grp = defaultdict(list)
    for name, idno, d, lt in cur.fetchall():
        grp[(name, idno, lt)].append(d)
    c.close()
    out = []
    for (name, idno, lt), dates in grp.items():
        dates = sorted(set(dates))
        seg = [dates[0]]; segs = []
        for p, q in zip(dates, dates[1:]):
            if _dt.strptime(q, "%Y%m%d") - _dt.strptime(p, "%Y%m%d") == timedelta(days=1):
                seg.append(q)
            else:
                segs.append(seg); seg = [q]
        segs.append(seg)
        for s in segs:
            out.append({"name": name, "emp_no": idno, "wkitem": lt,
                        "fr": f"{s[0][:4]}-{s[0][4:6]}-{s[0][6:]}",
                        "to": f"{s[-1][:4]}-{s[-1][4:6]}-{s[-1][6:]}"})
    out.sort(key=lambda r: (r["fr"], r["name"]))
    return out


# 그리드 컬럼 x 오프셋 (캔버스 좌상단 기준, 실측 필요)
_GRID_COLS = {"확정": 140, "사원": 222, "사번": 322, "부서": 422,
              "휴가항목": 702, "휴가시작일": 792, "휴가종료일": 872}
_GRID_ROW0_Y = 239      # 첫 데이터 행 중앙 y (페이지 좌표)
_GRID_ROW_H = 26


def paste_into_grid(page, fr, tsv, col="사원", row=0):
    """캔버스 그리드의 지정 셀을 클릭하고 클립보드 붙여넣기."""
    # 캔버스 절대좌표 = iframe 위치 + 프레임 내 캔버스 위치
    try:
        fe = fr.frame_element().bounding_box() or {"x": 0, "y": 0}
    except Exception:
        fe = {"x": 0, "y": 0}
    rect = fr.evaluate("""
    () => {
      const c = document.querySelector('#SS1_cvp_vp');
      if (!c) return null;
      const r = c.getBoundingClientRect();
      return {x: r.x, y: r.y, w: r.width, h: r.height};
    }
    """)
    if not rect:
        print("  ❌ 캔버스 위치 확인 실패")
        return False
    box = {"x": fe["x"] + rect["x"], "y": fe["y"] + rect["y"],
           "width": rect["w"], "height": rect["h"]}
    print(f"  캔버스(절대): x={box['x']:.0f} y={box['y']:.0f} "
          f"w={box['width']:.0f} h={box['height']:.0f}")
    # 컬럼/행 오프셋은 캔버스 좌상단 기준 상대값으로 계산
    x = box["x"] + _GRID_COLS.get(col, 222) - 4
    y = box["y"] + (_GRID_ROW0_Y - 154) + row * _GRID_ROW_H
    print(f"  셀 클릭: {col} 열 → ({x}, {y})")
    page.mouse.click(x, y)
    time.sleep(0.8)
    # 클립보드에 넣기
    page.evaluate("(t) => navigator.clipboard.writeText(t)", tsv)
    time.sleep(0.5)
    page.keyboard.press("Control+v")
    time.sleep(2)
    return True


def open_program(page, prog_name, search_kw=None):
    """프로그램조회로 임의 화면 열기.
    검색어에 괄호가 있으면 ERP 검색이 실패하므로, 괄호 앞부분으로 검색하고
    결과에서 정확한 이름을 골라 클릭한다."""
    kw = search_kw or prog_name.split("(")[0]
    page.click('#btnProgramQuery')
    time.sleep(1.5)
    box = page.locator('input:visible').last
    box.click()
    box.press_sequentially(kw, delay=60)
    page.keyboard.press("Enter")
    time.sleep(3)
    pos = page.evaluate("""
    (want) => {
      for (const el of document.querySelectorAll('a.txtProgramName')) {
        if ((el.textContent || '').trim() !== want) continue;
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.height > 0)
          return {x: r.x + r.width / 2, y: r.y + r.height / 2};
      }
      return null;
    }
    """, prog_name)
    if not pos:
        print(f"  ❌ '{prog_name}' 메뉴를 못 찾음")
        shot(page, "err_openprog")
        return False
    page.mouse.click(pos["x"], pos["y"])
    page.wait_for_load_state("networkidle")
    time.sleep(3)
    print(f"  ✅ '{prog_name}' 화면 열림")
    return True


def _codehelp_open(page):
    """코드도움 팝업이 '실제로 화면에 보이는지' (class 만 남아있는 경우 제외)."""
    try:
        return bool(page.evaluate("""
        () => {
          const d = document.querySelector('#divSheetCodeHelp');
          if (!d) return false;
          const r = d.getBoundingClientRect();
          const st = getComputedStyle(d);
          return r.width > 100 && r.height > 100 &&
                 st.display !== 'none' && st.visibility !== 'hidden';
        }
        """))
    except Exception:
        return False


def _codehelp_pick(page, want):
    """코드도움 팝업에서 want 와 정확히 일치하는 행을 더블클릭 선택."""
    time.sleep(1.2)
    hit = page.evaluate("""
    (want) => {
      const dlg = document.querySelector('#divSheetCodeHelp');
      if (!dlg) return null;
      // 팝업 안 셀 중 want 와 정확히 같은 텍스트
      for (const el of dlg.querySelectorAll('td,div,span')) {
        if (el.children.length) continue;
        if ((el.textContent||'').trim() !== want) continue;
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.height > 0)
          return {x: r.x + r.width/2, y: r.y + r.height/2};
      }
      return null;
    }
    """, want)
    if not hit:
        return False
    page.mouse.dblclick(hit["x"], hit["y"])
    time.sleep(1.5)
    return True


def _set(page, fr, sel, value, codehelp_want=None):
    """K-System 입력칸에 값 넣기. 코드도움 팝업이 뜨면 정확일치 행 선택."""
    el = fr.locator(sel)
    el.click()
    el.fill("")                      # 기존 값 확실히 제거 (Ctrl+A 는 iframe 에서 안 먹음)
    time.sleep(0.2)
    el.press_sequentially(value, delay=40)
    el.press("Tab")
    time.sleep(1.5)
    if _codehelp_open(page):
        picked = _codehelp_pick(page, codehelp_want or value)
        print(f"     (코드도움 팝업 → {'선택됨' if picked else '선택 실패'})")
        if not picked:
            shot(page, "err_codehelp")
            return False
    return True


def fill_form(page, fr, t, apply=False):
    """휴가신청 폼 채우기. apply=False 면 '내역생성'까지만 하고 저장은 절대 안 함."""
    print(f"\n=== 폼 입력: {t['name']} / {t['wkitem']} / {t['fr']}~{t['to']} ===")
    print("   모드:", "🔴 실제 저장" if apply else "🟢 드라이런 (저장 안 함)")

    # 1) 신규 버튼으로 폼 초기화
    try:
        page.click('text=신규', timeout=3000)
        time.sleep(1.5)
    except Exception:
        pass

    fr = find_vac_frame(page) or fr

    # 2) 사원
    if not _set(page, fr, '#txtEmpName_txt', t["name"]):
        return False
    empid = fr.locator('#txtEmpID_txt').input_value()
    dept = fr.locator('#txtDeptName_txt').input_value()
    print(f"   사원 → 사번 {empid} / 부서 {dept}")
    if not empid:
        print("   ❌ 사번 자동채움 실패 — 동명이인이거나 팝업 선택 필요")
        shot(page, "10_emp_fail")
        return False

    # 3) 휴가항목
    if not _set(page, fr, '#txtWkItemName_txt', t["wkitem"]):
        return False
    print("   휴가항목 →", fr.locator('#txtWkItemName_txt').input_value())

    # 4) 휴가기간
    _set(page, fr, '#datVacFrDate_dat', t["fr"])
    _set(page, fr, '#datVacToDate_dat', t["to"])
    print("   기간 →", fr.locator('#datVacFrDate_dat').input_value(),
          "~", fr.locator('#datVacToDate_dat').input_value())
    shot(page, "11_filled_header")

    # 5) 내역생성 (그리드 행 생성 — 아직 저장 아님)
    fr.click('#btnInPut_btn')
    time.sleep(2.5)
    shot(page, "12_after_generate")
    gen_msg = _dismiss_alert(page)      # '내역이 생성되었습니다' 알림 닫기 (안 닫으면 저장 클릭 막힘)
    if gen_msg:
        print("   내역생성 알림:", gen_msg[:60])
    time.sleep(1)
    days = fr.locator('#fltAppDays_flt').input_value()
    print("   내역생성 → 신청일수:", days)

    if not apply:
        print("\n🟢 드라이런 종료 — 저장하지 않았습니다. 스크린샷을 확인하세요.")
        print("   /tmp/erp_bot/11_filled_header.png, 12_after_generate.png")
        return True

    print("\n🔴 저장 실행")
    # '내역생성' 알림창이 떠 있으면 먼저 확인
    _dismiss_alert(page)
    # 저장 버튼은 화면 iframe 툴바 안의 <a> (메인 페이지의 '저장 후 계속'은 숨김요소라 클릭 불가)
    fr = find_vac_frame(page) or fr
    clicked = fr.evaluate("""
    () => {
      for (const el of document.querySelectorAll('a')) {
        if ((el.textContent||'').trim() !== '저장') continue;
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) { el.click(); return true; }
      }
      return false;
    }
    """)
    print("   저장 버튼 클릭:", clicked)
    if not clicked:
        shot(page, "err_savebtn")
        return False
    time.sleep(2.5)
    shot(page, "13_after_save_click")
    # 저장 확인/완료 팝업 처리
    msg = _dismiss_alert(page)
    if msg:
        print("   ERP 메시지:", msg)
    time.sleep(2)
    shot(page, "14_after_save_done")
    return True


_ALERT_JS = """
() => {
  const btns = Array.from(document.querySelectorAll('button,a,span,div'))
    .filter(b => {
      const t = (b.textContent||'').trim();
      if (t !== '확인' && t !== '예') return false;
      if (b.children.length) return false;
      const r = b.getBoundingClientRect();
      const st = getComputedStyle(b);
      return r.width > 20 && r.height > 10 && st.display !== 'none' && st.visibility !== 'hidden';
    });
  if (!btns.length) return null;
  const b = btns[btns.length - 1];
  // 팝업 전체 메시지 (조상 중 적당한 컨테이너)
  let box = b, hop = 0;
  while (box.parentElement && hop < 6) {
    box = box.parentElement; hop++;
    const r = box.getBoundingClientRect();
    if (r.width > 250 && r.height > 100) break;
  }
  b.click();
  return (box.textContent||'').trim().slice(0, 130);
}
"""


def _dismiss_alert(page):
    """ERP 알림/확인 팝업(메인/iframe 어디든)을 찾아 '확인'을 누르고 메시지 반환."""
    msgs = []
    for _ in range(4):
        found = None
        for f in [page] + list(page.frames):
            try:
                r = f.evaluate(_ALERT_JS)
            except Exception:
                continue
            if r:
                found = r
                break
        if not found:
            break
        msgs.append(found)
        time.sleep(1.2)
    return " | ".join(msgs) if msgs else None


def find_vac_frame(page):
    """휴가신청 화면이 로드된 iframe(frame) 반환. 없으면 None."""
    for f in page.frames:
        try:
            if f.locator('text=내역생성').count() > 0 and f.locator('text=휴가기간').count() > 0:
                return f
        except Exception:
            continue
    return None


def main():
    step = "login"
    if "--step" in sys.argv:
        step = sys.argv[sys.argv.index("--step") + 1]
    headless = "--headed" not in sys.argv

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(viewport={"width": 1600, "height": 1000},
                                  locale="ko-KR",
                                  permissions=["clipboard-read", "clipboard-write"])
        page = ctx.new_page()
        ok = do_login(page)
        if not ok:
            print("❌ 로그인 실패 — 계정/비밀번호 또는 화면 구조 확인 필요")
            browser.close()
            return 1
        if step == "login":
            print("✅ 로그인 검증 완료")
            browser.close()
            return 0

        if step == "fill":
            if not goto_vacation(page):
                browser.close()
                return 1
            fr = find_vac_frame(page)
            # 드라이런 대상 1건 (실제로는 leave_erp 대조결과에서 가져옴)
            target = {"name": "지창구", "wkitem": "연차",
                      "fr": "2026-07-24", "to": "2026-07-24"}
            if "--name" in sys.argv:
                i = sys.argv.index("--name")
                target["name"] = sys.argv[i + 1]
            if "--from" in sys.argv:
                i = sys.argv.index("--from")
                target["fr"] = sys.argv[i + 1]
                target["to"] = sys.argv[i + 1]
            if "--to" in sys.argv:
                target["to"] = sys.argv[sys.argv.index("--to") + 1]
            ok = fill_form(page, fr, target, apply="--apply" in sys.argv)
            browser.close()
            return 0 if ok else 1

        if step == "form":
            if not goto_vacation(page):
                browser.close()
                return 1
            fr = find_vac_frame(page)
            print("\n=== 휴가신청 폼 입력요소 ===")
            info = fr.evaluate("""
            () => Array.from(document.querySelectorAll('input,select,button,a'))
              .map(el => {
                const r = el.getBoundingClientRect();
                return {tag: el.tagName, type: el.type||'', id: el.id||'',
                        name: el.name||'', cls: (el.className||'').toString().slice(0,40),
                        val: (el.value||'').slice(0,20), ro: el.readOnly||false,
                        x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width),
                        vis: r.width>0 && r.height>0};
              }).filter(o => o.vis && o.y < 300)
            """)
            for i in info:
                print('  ', i)
            browser.close()
            return 0

        if step == "paste":
            if not open_program(page, "휴가신청(일괄)"):
                browser.close()
                return 1
            time.sleep(2.5)
            fr = _grid_frame(page)
            if not fr:
                print("❌ 그리드 프레임 못 찾음")
                browser.close()
                return 1

            rows = load_targets(limit=int(sys.argv[sys.argv.index("--limit") + 1])
                                if "--limit" in sys.argv else None)
            if not rows:
                print("대상자가 없습니다.")
                browser.close()
                return 0
            print(f"\n=== 붙여넣기 대상 {len(rows)}건 ===")
            for r in rows:
                print(f"   {r['name']} | {r['wkitem']} | {r['fr']} ~ {r['to']}")

            # 사원 / 휴가항목 / 휴가시작일 / 휴가종료일 순서로 TSV 구성은
            # 컬럼 배치 확인 후 결정 → 우선 사원 열부터 붙여넣기 시험
            tsv = "\n".join(r["name"] for r in rows)
            ok = paste_into_grid(page, fr, tsv, col="사원")
            shot(page, "40_after_paste")
            print("\n🟢 붙여넣기만 수행 — 저장하지 않았습니다.")
            browser.close()
            return 0 if ok else 1

        if step == "grid":
            if not open_program(page, "휴가신청(일괄)"):
                browser.close()
                return 1
            time.sleep(2)
            print("\n=== '휴가시작일' 텍스트가 있는 프레임 찾기 ===")
            for fi, f in enumerate([page] + list(page.frames)):
                try:
                    r = f.evaluate("""
                    () => {
                      const hits = [];
                      document.querySelectorAll('*').forEach(el => {
                        if (el.children.length) return;
                        const t = (el.textContent||'').trim();
                        if (t === '휴가시작일' || t === '휴가종료일' || t === '사원') {
                          const b = el.getBoundingClientRect();
                          hits.push({t: t, tag: el.tagName, cls:(el.className||'').toString().slice(0,35),
                                     x: Math.round(b.x), y: Math.round(b.y)});
                        }
                      });
                      return {n: document.querySelectorAll('*').length, hits: hits.slice(0,6),
                              canvas: document.querySelectorAll('canvas').length};
                    }
                    """)
                except Exception:
                    continue
                if r["hits"] or r["canvas"]:
                    label = "MAIN" if fi == 0 else f"frame{fi-1}"
                    print(f"  [{label}] elems={r['n']} canvas={r['canvas']} url={f.url[-38:] if fi else 'main'}")
                    for h in r["hits"]:
                        print("      ", h)

            # 실제로 렌더링된 프레임 선택 (body 높이 > 0)
            best = None; best_h = -1
            for f in page.frames:
                try:
                    if f.locator('#SS1_btnSheetSetting').count() == 0:
                        continue
                    h = f.evaluate("() => document.documentElement.scrollHeight || 0")
                except Exception:
                    continue
                if h >= best_h:          # 마지막(가장 최근) 프레임 우선
                    best, best_h = f, h
            fr = best
            if not fr:
                print("❌ 그리드 프레임 못 찾음")
                browser.close()
                return 1
            print(f"  그리드 프레임: {fr.url[-40:]} (body h={best_h:.0f})")
            # 그리드 구조 파악: 헤더 컬럼명 + 첫 행 셀 좌표
            info = fr.evaluate("""
            () => {
              const heads = [];
              document.querySelectorAll('th,.GMHeaderCell,[class*="header"]').forEach(el => {
                const t = (el.textContent||'').trim();
                const r = el.getBoundingClientRect();
                if (t && r.width > 10 && r.height > 5 && r.top < 200)
                  heads.push({t: t.slice(0,12), x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width)});
              });
              // 캔버스/시트 컨테이너 후보
              const sheets = [];
              document.querySelectorAll('div,canvas,table').forEach(el => {
                const id = el.id||'';
                if (/SS1|sheet|Sheet|grid|Grid/.test(id)) {
                  const r = el.getBoundingClientRect();
                  if (r.width > 300 && r.height > 100)
                    sheets.push({tag: el.tagName, id: id, x: Math.round(r.x), y: Math.round(r.y),
                                 w: Math.round(r.width), h: Math.round(r.height)});
                }
              });
              return {heads: heads.slice(0,30), sheets: sheets.slice(0,10)};
            }
            """)
            print("\n  === 그리드 헤더 ===")
            for h in info["heads"]:
                print("    ", h)
            print("\n  === 시트 컨테이너 ===")
            for s in info["sheets"]:
                print("    ", s)
            print("\n  === 시트 JS API 탐색 ===")
            print("   ", fr.evaluate("""
            () => {
              const out = [];
              for (const k of Object.keys(window)) {
                let v;
                try { v = window[k]; } catch(e) { continue; }
                if (!v || typeof v !== 'object') continue;
                const names = [];
                for (const m of ['SetCellValue','setValue','SetValue','LoadSearchData',
                                 'DataInsert','SetRowData','GetTotalRows','SetSheetData',
                                 'RemoveAll','SetEditable','DoPaste','Paste']) {
                  if (typeof v[m] === 'function') names.push(m);
                }
                if (names.length) out.push({obj: k, api: names});
              }
              return out.slice(0, 12);
            }
            """))
            print("\n  === y>140 영역 요소 (그리드 실체 파악) ===")
            for e in fr.evaluate("""
            () => Array.from(document.querySelectorAll('*')).map(el => {
              const r = el.getBoundingClientRect();
              return {tag: el.tagName, id: el.id||'', cls:(el.className||'').toString().slice(0,40),
                      t:(el.textContent||'').trim().slice(0,14), kids: el.children.length,
                      x: Math.round(r.x), y: Math.round(r.y),
                      w: Math.round(r.width), h: Math.round(r.height)};
            }).filter(o => o.y > 140 && o.w > 60 && o.h > 15).slice(0, 35)
            """):
                print("    ", e)
            print("\n  === 시트 관련 JS 객체 ===")
            print("   ", fr.evaluate("""
            () => Object.keys(window).filter(k =>
              /sheet|Sheet|grid|Grid|IBS|SS1/.test(k)).slice(0, 25)
            """))
            print("\n  === sheetApp 구조 ===")
            print("   ", fr.evaluate("""
            () => {
              const s = window.sheetApp;
              if (!s) return 'sheetApp 없음';
              const keys = Object.keys(s).slice(0, 30);
              const proto = Object.getOwnPropertyNames(Object.getPrototypeOf(s)||{}).slice(0,30);
              return {type: typeof s, keys: keys, proto: proto};
            }
            """))
            print("\n  === 중첩 iframe / 시트 DOM ===")
            print("   ", fr.evaluate("""
            () => {
              const ifr = Array.from(document.querySelectorAll('iframe')).map(f => ({
                id: f.id||'', src: (f.src||'').slice(-40)}));
              const all = document.querySelectorAll('*').length;
              const body = document.body ? document.body.getBoundingClientRect() : null;
              return {iframes: ifr, elemCount: all,
                      bodyH: body ? Math.round(body.height) : 0};
            }
            """))
            shot(page, "31_grid_structure")
            browser.close()
            return 0

        if step == "bulk":
            if not open_program(page, "휴가신청(일괄)"):
                browser.close()
                return 1
            time.sleep(2)
            shot(page, "30_bulk_screen")
            print("\n=== 휴가신청(일괄) 화면 구성 ===")
            for fi, f in enumerate(page.frames):
                try:
                    els = f.evaluate("""
                    () => Array.from(document.querySelectorAll('input,button,select,a,th'))
                      .map(el => { const r = el.getBoundingClientRect();
                        return {tag: el.tagName, type: el.type||'', id: el.id||'',
                                t: (el.textContent||el.value||'').trim().slice(0,16),
                                x: Math.round(r.x), y: Math.round(r.y),
                                vis: r.width>0 && r.height>0};
                      }).filter(o => o.vis && (o.id || o.t))
                    """)
                except Exception:
                    continue
                if len(els) > 5:
                    print(f"  [frame {fi}] {f.url[-40:]}  요소 {len(els)}개")
                    for e in els[:45]:
                        print("    ", e)
            browser.close()
            return 0

        if step == "search":
            kw = sys.argv[sys.argv.index("--kw") + 1] if "--kw" in sys.argv else "휴가"
            page.click('#btnProgramQuery')
            time.sleep(1.5)
            box = page.locator('input:visible').last
            box.click()
            box.press_sequentially(kw, delay=60)
            page.keyboard.press("Enter")
            time.sleep(3)
            shot(page, "20_search_" + kw)
            print(f"\n=== '{kw}' 검색 결과 ===")
            for r in page.evaluate("""
            () => Array.from(document.querySelectorAll('a.txtProgramName')).map(el => {
              let path = '';
              let p = el.parentElement;
              for (let i = 0; i < 4 && p; i++, p = p.parentElement) {
                const s = p.querySelector('span.txt');
                if (s) { path = (s.textContent||'').trim(); break; }
              }
              return {name: (el.textContent||'').trim(), path: path};
            })
            """):
                print("  •", r["name"], " —", r["path"])
            browser.close()
            return 0

        if step == "savebtn":
            if not goto_vacation(page):
                browser.close()
                return 1
            print("\n=== '저장' 후보 (전 프레임) ===")
            for fi, f in enumerate(page.frames):
                try:
                    found = f.evaluate("""
                    () => Array.from(document.querySelectorAll('a,button,span,div,li'))
                      .filter(el => (el.textContent||'').trim().startsWith('저장'))
                      .map(el => { const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
                        return {tag: el.tagName, id: el.id||'', cls:(el.className||'').toString().slice(0,40),
                                t:(el.textContent||'').trim().slice(0,12), kids: el.children.length,
                                x: Math.round(r.x), y: Math.round(r.y),
                                vis: r.width>0 && r.height>0 && st.display!=='none'};
                      }).filter(o => o.vis)
                    """)
                except Exception:
                    continue
                if found:
                    print(f"  [frame {fi}] {f.url[-40:]}")
                    for b in found:
                        print('    ', b)
            print("\n=== (참고) 메인 페이지 전체 ===")
            for b in page.evaluate("""
            () => Array.from(document.querySelectorAll('a,button,span,div,li'))
              .filter(el => (el.textContent||'').trim().startsWith('저장'))
              .map(el => { const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
                return {tag: el.tagName, id: el.id||'', cls:(el.className||'').toString().slice(0,45),
                        t:(el.textContent||'').trim().slice(0,15), kids: el.children.length,
                        x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width),
                        vis: r.width>0 && r.height>0 && st.display!=='none' && st.visibility!=='hidden'};
              })
            """):
                print('  ', b)
            browser.close()
            return 0

        if step == "dump":
            dump_header(page)
            browser.close()
            return 0

        if step in ("menu", "fill"):
            ok2 = goto_vacation(page)
            if not ok2:
                print("❌ 휴가신청 화면 진입 실패")
                browser.close()
                return 1
            print("✅ 휴가신청 화면 진입 완료")
            if step == "menu":
                browser.close()
                return 0

        print(f"(step={step} 은 아직 미구현)")
        browser.close()
        return 0


if __name__ == "__main__":
    sys.exit(main())

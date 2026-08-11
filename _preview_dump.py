#!/usr/bin/env python3
"""_preview_dump.py — 대시보드가 템플릿에 넘기는 실제 컨텍스트 구조를 찍는다.

새 디자인 템플릿을 실데이터로 만들려면 변수 이름만이 아니라 자료 모양을 알아야 한다.
쿼리를 다시 짜지 않고 운영 라우트를 그대로 호출한 뒤 render_template 을 가로챈다.

⚠️ app_maria 를 그냥 import 하면 운영에 영향을 준다. 반드시 먼저 막는다.
   · MQTT 구독자 — client ID 가 'mes_subscriber' 로 운영과 같아서, 같은 브로커에
     붙으면 서로 강퇴하며 QoS0 메시지가 유실된다 (mes_bp.py 주석의 2026-07-22 실사고).
   · HAZMAT 모니터 — 30초 뒤부터 5분 주기로 돌며 텔레그램 발송 + hazmat_items UPDATE.
   · 그 밖의 텔레그램 발송 — requests.post 를 통째로 막아 이중으로 차단한다.

DB 는 SELECT 만 한다.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# ── ① 부작용 차단 — app_maria 보다 먼저 ─────────────────────
_blocked = []

import requests  # noqa: E402


def _blocked_post(url, *a, **kw):
    _blocked.append(str(url)[:60])
    raise RuntimeError("미리보기 모드: 외부 발송 차단됨")


requests.post = _blocked_post

import mes_bp  # noqa: E402
mes_bp._start_subscriber_if_leader = lambda *a, **kw: print("[차단] MQTT 구독 안 함")

import hazmat_bp  # noqa: E402
hazmat_bp.start_hazmat_monitor = lambda *a, **kw: print("[차단] HAZMAT 모니터 안 뜸")

import app_maria as A  # noqa: E402


def shape(v, depth=0, key=""):
    pad = "  " * depth
    t = type(v).__name__
    if isinstance(v, dict):
        print(f"{pad}{key}: dict({len(v)})")
        for k in list(v)[:6]:
            shape(v[k], depth + 1, repr(k))
        if len(v) > 6:
            print(f"{pad}  … 외 {len(v)-6}개 키")
    elif isinstance(v, (list, tuple)):
        print(f"{pad}{key}: {t}({len(v)})")
        if v:
            shape(v[0], depth + 1, "[0]")
    else:
        s = str(v)
        if len(s) > 55:
            s = s[:55] + "…"
        print(f"{pad}{key}: {t} = {s}")


captured = {}
_orig = A.render_template


def _spy(template_name, **ctx):
    captured["name"] = template_name
    captured["ctx"] = ctx
    return "captured"


def capture():
    """운영 dashboard() 를 그대로 호출해 컨텍스트만 받아온다."""
    conn = A._conn()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM tuser ORDER BY id LIMIT 1")
    uid, uname = cur.fetchone()
    conn.close()

    A.render_template = _spy
    try:
        with A.app.test_request_context("/"):
            from flask import session
            session["user_id"] = uid
            session["role"] = "admin"      # 관리자 패널까지 채워서 본다
            session["e_id"] = uid
            session["user_name"] = uname
            A.dashboard()
    finally:
        A.render_template = _orig
    return captured.get("ctx", {})


def main():
    ctx = capture()
    print(f"\n템플릿: {captured.get('name')}  ·  컨텍스트 {len(ctx)}개")
    print("=" * 70)
    for k in sorted(ctx):
        shape(ctx[k], 0, k)
        print("-" * 70)
    if _blocked:
        print(f"\n⚠️ 차단된 외부 발송 {len(_blocked)}건: {_blocked[:3]}")
    else:
        print("\n외부 발송 시도 없음")


if __name__ == "__main__":
    main()

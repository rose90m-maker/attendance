#!/usr/bin/env python3
"""솔라피 통합 발송 모듈 — 문자(SMS/LMS)와 카카오 알림톡/친구톡을 한 곳에서 보낸다.

app_maria.py 의 `_send_sms_solapi()` 는 위험물 알림·설문에서 이미 돌고 있어 건드리지 않았다.
이쪽은 단체 발송 화면(/message_send)용으로 새로 만든 것이고, 다음이 다르다.
  · 90바이트가 넘으면 자동으로 LMS 로 올린다 (기존 것은 90자에서 잘림)
  · 수신자마다 본문이 달라질 수 있다 ({이름}·{부서} 치환) — send-many/detail 사용
  · 알림톡을 같은 경로로 보낸다. 채널 등록 전이면 문자로만 나간다.
  · 건별 성공/실패를 돌려줘 발송 이력에 남길 수 있다

카카오 알림톡은 발신프로필(pfId)과 승인된 템플릿(templateId)이 있어야 한다.
2026-08-09 기준 계정에 채널이 없어 문자만 실사용 중이다.
"""
import hashlib
import hmac
import json
import os
import re
import ssl
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen

API = "https://api.solapi.com"
SMS_MAX_BYTES = 90          # EUC-KR 기준. 넘으면 LMS
LMS_MAX_BYTES = 2000
CHUNK = 100                 # 한 번에 보낼 건수 (솔라피 한도는 10,000이지만 실패 격리 목적)


def _cfg():
    return (os.environ.get("SOLAPI_KEY", ""),
            os.environ.get("SOLAPI_SECRET", ""),
            os.environ.get("SOLAPI_SENDER", ""))


def _auth_header():
    key, sec, _ = _cfg()
    dt = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    salt = os.urandom(16).hex()
    sig = hmac.new(sec.encode(), (dt + salt).encode(), hashlib.md5).hexdigest()
    return f"HMAC-MD5 apiKey={key}, date={dt}, salt={salt}, signature={sig}"


def _ctx():
    # NAS 컨테이너 인증서 체인이 얕아 기존 코드도 검증을 끄고 쓴다. 동작을 맞춘다.
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    return c


def _call(method, path, payload=None, timeout=30):
    body = json.dumps(payload).encode() if payload is not None else None
    req = Request(API + path, data=body, method=method,
                  headers={"Content-Type": "application/json",
                           "Authorization": _auth_header()})
    try:
        with urlopen(req, context=_ctx(), timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except HTTPError as e:
        raw = e.read().decode()
        try:
            j = json.loads(raw)
            raise RuntimeError(j.get("errorMessage") or j.get("message") or raw)
        except (ValueError, TypeError):
            raise RuntimeError(f"HTTP {e.code}: {raw[:200]}")


# ── 길이 계산 ────────────────────────────────────────────────
def text_bytes(s):
    """EUC-KR 기준 바이트 수. 통신사 과금이 이 기준이라 len() 이 아니라 이걸 쓴다."""
    try:
        return len((s or "").encode("euc-kr"))
    except UnicodeEncodeError:
        # 이모지처럼 EUC-KR 에 없는 글자는 2바이트로 친다 (LMS 로 넘어가므로 안전측)
        return sum(1 if ord(c) < 128 else 2 for c in (s or ""))


def guess_type(text, has_title=False):
    """SMS / LMS 판정. 제목이 있으면 무조건 LMS."""
    if has_title or text_bytes(text) > SMS_MAX_BYTES:
        return "LMS"
    return "SMS"


# ── 수신자 정리 ──────────────────────────────────────────────
def norm_phone(p):
    """숫자만 남기고 휴대폰 형식이면 반환, 아니면 None"""
    d = re.sub(r"[^0-9]", "", str(p or ""))
    if d.startswith("8210") and len(d) == 12:      # +82 10 ... 형태
        d = "0" + d[2:]
    if len(d) == 11 and d.startswith("010"):
        return d
    if len(d) == 10 and d.startswith("01"):        # 011/016 등 구번호
        return d
    return None


# 담당자는 {이름} 이라고 쓰지 {name} 이라고 쓰지 않는다. 한글 표시자를 컬럼에 이어 준다.
ALIAS = {
    "이름": "name", "성명": "name",
    "부서": "dept", "소속": "dept", "팀": "dept",
    "직급": "position", "직위": "position",
    "직책": "job_title", "사번": "emp_no",
    "휴대폰": "phone", "연락처": "phone",
}


def personalize(text, row):
    """본문의 {이름}·{부서} 등을 수신자 값으로 바꾼다. 값이 없는 표시자는 지운다."""
    if not text:
        return ""
    row = row or {}
    out = text
    for key, val in row.items():                       # 컬럼명 그대로 쓴 경우
        out = out.replace("{" + str(key) + "}", "" if val is None else str(val))
    for ko, col in ALIAS.items():                      # 한글 표시자
        if "{" + ko + "}" in out:
            val = row.get(col)
            out = out.replace("{" + ko + "}", "" if val is None else str(val))
    # 끝까지 안 채워진 표시자는 지운다 (그대로 두면 문자에 {이름} 이 찍혀 나간다)
    return re.sub(r"\{[가-힣A-Za-z_][가-힣A-Za-z0-9_]*\}", "", out)


def build_recipients(rows, body, title=None):
    """[{name,dept,phone,...}] → 발송 대상 목록 + 제외 목록.

    반환: (valid, dropped)
      valid   = [{"phone","text","title","name","dept","raw"}]
      dropped = [{"name","phone","reason"}]
    """
    valid, dropped, seen = [], [], set()
    for r in rows or []:
        row = dict(r) if isinstance(r, dict) else {"phone": r}
        ph = norm_phone(row.get("phone"))
        if not ph:
            dropped.append({"name": row.get("name", ""), "phone": row.get("phone", ""),
                            "reason": "번호 없음/형식 오류"})
            continue
        if ph in seen:
            dropped.append({"name": row.get("name", ""), "phone": ph, "reason": "중복"})
            continue
        seen.add(ph)
        txt = personalize(body, row)
        if not txt.strip():
            dropped.append({"name": row.get("name", ""), "phone": ph, "reason": "본문 없음"})
            continue
        if text_bytes(txt) > LMS_MAX_BYTES:
            dropped.append({"name": row.get("name", ""), "phone": ph,
                            "reason": f"본문 초과({text_bytes(txt)}/{LMS_MAX_BYTES}바이트)"})
            continue
        valid.append({"phone": ph, "text": txt, "title": personalize(title, row) if title else None,
                      "name": row.get("name", ""), "dept": row.get("dept", ""), "raw": row})
    return valid, dropped


# ── 발송 ────────────────────────────────────────────────────
def send(rows, body, title=None, channel="auto", kakao=None, dry_run=False):
    """단체 발송.

    rows    : [{"name","dept","phone",...}] — phone 외 키는 본문 치환에 쓰인다
    body    : 본문. {이름} 처럼 중괄호로 치환자 사용 가능
    title   : LMS 제목 (선택). 넣으면 SMS 가 아니라 LMS 로 나간다
    channel : "auto"(길이로 SMS/LMS 판단) | "sms" | "lms" | "ata"(알림톡) | "cta"(친구톡)
    kakao   : 알림톡/친구톡용 {"pf_id","template_id","disable_sms"}
              disable_sms=False 면 카톡 미수신자에게 문자로 대체발송된다(추가 과금)
    dry_run : True 면 실제로 보내지 않고 집계만 돌려준다

    반환: {"ok","total","sent","failed","dropped","group_id","results":[...],"error"}
    """
    key, sec, sender = _cfg()
    valid, dropped = build_recipients(rows, body, title)
    base = {"total": len(valid), "sent": 0, "failed": 0, "dropped": dropped,
            "group_id": "", "results": [], "error": ""}

    if not (key and sec and sender):
        return {**base, "ok": False, "error": "솔라피 설정(SOLAPI_KEY/SECRET/SENDER)이 없습니다."}
    if not valid:
        return {**base, "ok": False, "error": "보낼 수 있는 수신자가 없습니다."}

    is_kakao = channel in ("ata", "cta")
    if is_kakao:
        if not (kakao or {}).get("pf_id"):
            return {**base, "ok": False,
                    "error": "카카오 발신프로필(pfId)이 없습니다. 채널 등록 후 사용하세요."}
        if channel == "ata" and not (kakao or {}).get("template_id"):
            return {**base, "ok": False, "error": "알림톡은 승인된 템플릿ID가 필요합니다."}

    if dry_run:
        kind = "알림톡" if channel == "ata" else "친구톡" if channel == "cta" else \
               guess_type(valid[0]["text"], bool(title)) if channel == "auto" else channel.upper()
        return {**base, "ok": True, "dry_run": True, "kind": kind,
                "sample": valid[0]["text"], "bytes": text_bytes(valid[0]["text"])}

    results, sent, failed, gid = [], 0, 0, ""
    for i in range(0, len(valid), CHUNK):
        batch = valid[i:i + CHUNK]
        messages = []
        for v in batch:
            m = {"to": v["phone"], "from": sender, "text": v["text"]}
            if channel in ("sms", "lms"):
                m["type"] = channel.upper()
            elif channel == "auto":
                m["type"] = guess_type(v["text"], bool(v["title"]))
            if v["title"] and m.get("type") != "SMS":
                m["subject"] = v["title"]
            if is_kakao:
                ko = {"pfId": kakao["pf_id"],
                      "disableSms": bool((kakao or {}).get("disable_sms", False))}
                if channel == "ata":
                    ko["templateId"] = kakao["template_id"]
                    if kakao.get("variables"):
                        ko["variables"] = {k: personalize(val, v["raw"])
                                           for k, val in kakao["variables"].items()}
                m["kakaoOptions"] = ko
                m.pop("type", None)
            messages.append(m)

        try:
            res = _call("POST", "/messages/v4/send-many/detail",
                        {"messages": messages, "allowDuplicates": True})
        except Exception as e:                       # 배치 전체 실패 — 건별로 실패 기록
            for v in batch:
                results.append({"name": v["name"], "dept": v["dept"], "phone": v["phone"],
                                "ok": False, "error": str(e)[:200]})
            failed += len(batch)
            continue

        gid = gid or (res.get("groupInfo", {}) or {}).get("groupId", "") or res.get("groupId", "")
        # 실패 건은 failedMessageList 로 따로 온다
        fail_map = {}
        for f in (res.get("failedMessageList") or []):
            fail_map[re.sub(r"[^0-9]", "", str(f.get("to", "")))] = \
                f.get("statusMessage") or f.get("statusCode") or "실패"
        for v in batch:
            err = fail_map.get(v["phone"])
            results.append({"name": v["name"], "dept": v["dept"], "phone": v["phone"],
                            "ok": not err, "error": err or ""})
            if err:
                failed += 1
            else:
                sent += 1

    return {**base, "ok": failed == 0, "sent": sent, "failed": failed,
            "group_id": gid, "results": results}


# ── 잔액·채널 조회 (화면 표시용) ───────────────────────────────
def balance():
    try:
        b = _call("GET", "/cash/v1/balance", timeout=15)
        return {"ok": True, "balance": float(b.get("balance") or 0),
                "point": float(b.get("point") or 0)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def kakao_channels():
    """연결된 카카오 발신프로필 목록. 없으면 빈 리스트 (= 알림톡 아직 못 씀)."""
    try:
        r = _call("GET", "/kakao/v2/channels", timeout=15)
        lst = r.get("channelList") if isinstance(r, dict) else r
        return [{"pf_id": c.get("channelId") or c.get("pfId"),
                 "search_id": c.get("searchId"),
                 "status": c.get("status")} for c in (lst or [])]
    except Exception:
        return []


def kakao_templates(pf_id=None):
    """승인된 알림톡 템플릿 목록"""
    try:
        q = f"?pfId={pf_id}&limit=100" if pf_id else "?limit=100"
        r = _call("GET", "/kakao/v2/templates" + q, timeout=15)
        out = []
        for t in (r.get("templateList") or []):
            out.append({"template_id": t.get("templateId"), "name": t.get("name"),
                        "status": t.get("status"), "content": t.get("content", ""),
                        "pf_id": t.get("channelId") or t.get("pfId")})
        return out
    except Exception:
        return []


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    print("잔액:", balance())
    print("카카오 채널:", kakao_channels() or "없음 (알림톡 사용 불가)")
    demo = [{"name": "홍길동", "dept": "총무팀", "phone": "010-0000-0000"}]
    print("미리보기:", send(demo, "{이름}님, 안녕하세요. {부서} 공지입니다.", dry_run=True))

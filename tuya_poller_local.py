"""
로컬(맥)에서 Tuya 폴링 → NAS Flask API로 전송하는 독립 프로세스
NAS Python 환경 의존성 없이 맥에서 직접 실행
"""
import hmac, hashlib, time, requests, logging, os, signal, sys
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

ACCESS_ID     = os.environ["TUYA_ACCESS_ID"]
ACCESS_SECRET = os.environ["TUYA_ACCESS_SECRET"]
REGION        = "us"
POLL_INTERVAL = 60  # 초 (Tuya API 쿼터 절약 — 감지기 자체 경보음은 즉시 울림)
NAS_URL       = "http://192.168.100.11:5050"

DEVICES = [
    {"key": "smoke-001", "name": "연기감지기", "location": "박스창고", "device_id": "ebfcbfd26f4de8e50ecjbj"},
]

HOST = {"cn": "openapi.tuyacn.com", "eu": "openapi.tuyaeu.com",
        "us": "openapi.tuyaus.com", "in": "openapi.tuyain.com"}[REGION]

# 연기 상태와 변조 상태를 따로 추적
_prev_smoke  = {}   # smoke_sensor_status 변화 추적
_prev_temper = {}   # temper_alarm 변화 추적


def _sign(access_id, secret, t, token, method, path, body=""):
    content_hash = hashlib.sha256(body.encode()).hexdigest()
    str_to_sign = f"{method}\n{content_hash}\n\n{path}"
    message = access_id + token + str(t) + str_to_sign
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest().upper()


def get_token():
    t = int(time.time() * 1000)
    path = "/v1.0/token?grant_type=1"
    s = _sign(ACCESS_ID, ACCESS_SECRET, t, "", "GET", path)
    headers = {"client_id": ACCESS_ID, "sign": s, "t": str(t), "sign_method": "HMAC-SHA256"}
    data = requests.get(f"https://{HOST}{path}", headers=headers, timeout=10).json()
    if not data.get("success"):
        raise RuntimeError(f"토큰 발급 실패: {data}")
    return data["result"]["access_token"]


def get_device_status(token, device_id):
    t = int(time.time() * 1000)
    path = f"/v1.0/devices/{device_id}/status"
    s = _sign(ACCESS_ID, ACCESS_SECRET, t, token, "GET", path)
    headers = {"client_id": ACCESS_ID, "access_token": token, "sign": s, "t": str(t), "sign_method": "HMAC-SHA256"}
    return requests.get(f"https://{HOST}{path}", headers=headers, timeout=10).json()


def parse_dps(dps):
    """각 DP를 파싱해서 smoke_status, temper, battery, fault 반환"""
    smoke_status = "normal"
    temper = False
    battery = None
    fault = None
    for dp in dps:
        code, value = dp.get("code", ""), dp.get("value")
        if code in ("smoke_sensor_state", "smoke_sensor_status"):
            if value in ("alarm", "serious_alarm"):
                smoke_status = "alarm"
            else:
                smoke_status = "normal"
        elif code == "temper_alarm":
            temper = bool(value)
        elif code == "battery_percentage":
            try: battery = int(value)
            except: pass
        elif code == "fault" and value and value != 0:
            fault = value
    return smoke_status, temper, battery, fault


def send_event(dev, status, battery, event_type, message):
    try:
        r = requests.post(f"{NAS_URL}/api/fire/event", json={
            "device_key": dev["key"], "device_name": dev["name"],
            "location": dev["location"], "status": status,
            "battery": battery, "event_type": event_type, "message": message,
        }, timeout=5)
        logger.info(f"[이벤트] {dev['key']} {event_type}:{status} → {r.status_code}")
    except Exception as e:
        logger.warning(f"[이벤트 전송 실패] {e}")


def send_heartbeat(dev, status, battery):
    try:
        requests.post(f"{NAS_URL}/api/fire/heartbeat", json={
            "device_key": dev["key"], "device_name": dev["name"],
            "location": dev["location"], "status": status, "battery": battery,
        }, timeout=5)
        logger.info(f"[하트비트] {dev['key']} {status} 배터리:{battery}%")
    except Exception as e:
        logger.warning(f"[하트비트 전송 실패] {e}")


def poll_once(token):
    for dev in DEVICES:
        key = dev["key"]
        try:
            result = get_device_status(token, dev["device_id"])
            if not result.get("success"):
                if _prev_smoke.get(key) != "offline":
                    send_event(dev, "offline", None, "offline", "장치 응답 없음")
                    _prev_smoke[key] = "offline"
                continue

            smoke_status, temper, battery, fault = parse_dps(result.get("result", []))

            # ① 연기 상태 변화 처리 (null → normal은 최초 연결이므로 이벤트 없이 저장)
            prev_smoke = _prev_smoke.get(key)
            if smoke_status != prev_smoke:
                if prev_smoke is None:
                    # 최초 폴링: 이벤트 없이 현재 상태만 heartbeat로 등록
                    send_heartbeat(dev, smoke_status, battery)
                    logger.info(f"[초기화] {key}: 연기상태={smoke_status}")
                else:
                    event_type = "smoke_detected" if smoke_status == "alarm" else "smoke_cleared"
                    msg = "연기 감지됨!" if smoke_status == "alarm" else "연기 경보 해제됨"
                    logger.info(f"[연기상태변경] {key}: {prev_smoke} → {smoke_status}")
                    send_event(dev, smoke_status, battery, event_type, msg)
                _prev_smoke[key] = smoke_status
            else:
                # 상태 변화 없음: heartbeat
                if _prev_temper.get(key) is None or temper == _prev_temper.get(key):
                    send_heartbeat(dev, smoke_status, battery)

            # ② 변조 알람 변화 처리 (연기와 독립)
            prev_temper = _prev_temper.get(key)
            if temper and prev_temper is None:
                # 폴러 시작 시 이미 커버가 열려있으면 즉시 경보
                logger.info(f"[변조감지-초기] {key}: 폴러 시작 시 temper_alarm ON")
                send_event(dev, "alarm", battery, "temper_alarm", "장치 변조/개봉 감지됨 (폴러 재시작 시 감지)")
            elif temper != prev_temper and prev_temper is not None:
                if temper:
                    logger.info(f"[변조감지] {key}: temper_alarm ON")
                    send_event(dev, "alarm", battery, "temper_alarm", "장치 변조/개봉 감지됨")
                else:
                    logger.info(f"[변조해제] {key}: temper_alarm OFF")
                    send_event(dev, smoke_status, battery, "temper_cleared", "장치 변조 경보 해제")
            _prev_temper[key] = temper

            # ③ fault 처리
            if fault:
                send_event(dev, "fault", battery, "fault", f"장치 오류: {fault}")

        except Exception as e:
            logger.error(f"[폴링 오류] {key}: {e}")


if __name__ == "__main__":
    import sys as _sys
    once_mode = "--once" in _sys.argv

    signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))

    if once_mode:
        logger.info(f"Tuya 1회 폴링 — {len(DEVICES)}개 장치 → {NAS_URL}")
        try:
            token = get_token()
            poll_once(token)
            logger.info("1회 폴링 완료")
        except Exception as e:
            logger.error(f"폴링 오류: {e}")
            sys.exit(1)
    else:
        logger.info(f"Tuya 폴러 시작 — {len(DEVICES)}개 장치, {POLL_INTERVAL}초 간격 → {NAS_URL}")
        token, token_expire = None, 0
        while True:
            try:
                if time.time() >= token_expire:
                    token = get_token()
                    token_expire = time.time() + 7000
                    logger.info("토큰 갱신됨")
                poll_once(token)
            except Exception as e:
                logger.error(f"폴러 오류: {e}")
            time.sleep(POLL_INTERVAL)

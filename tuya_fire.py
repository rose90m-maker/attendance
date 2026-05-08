"""
Tuya 연기감지기 → 소방관리 연동 폴러
환경변수 또는 직접 설정으로 여러 장치 등록 가능

환경변수 예시 (.env 또는 NAS 실행 환경에서 설정):
  TUYA_ACCESS_ID=your_access_id
  TUYA_ACCESS_SECRET=your_access_secret
  TUYA_REGION=cn   (cn / eu / us / in)
  TUYA_DEVICES=smoke-001:장치이름:위치:DEVICEID1,smoke-002:장치이름:위치:DEVICEID2
"""

import os, time, hmac, hashlib, json, threading, logging
import requests

logger = logging.getLogger(__name__)

TUYA_ACCESS_ID     = os.environ.get("TUYA_ACCESS_ID", "")
TUYA_ACCESS_SECRET = os.environ.get("TUYA_ACCESS_SECRET", "")
TUYA_REGION        = os.environ.get("TUYA_REGION", "cn")  # cn / eu / us / in
TUYA_POLL_INTERVAL = int(os.environ.get("TUYA_POLL_INTERVAL", "30"))  # 초
# 형식: "key:이름:위치:deviceid,key2:이름2:위치2:deviceid2"
TUYA_DEVICES_ENV   = os.environ.get("TUYA_DEVICES", "")

REGION_HOSTS = {
    "cn": "openapi.tuyacn.com",
    "eu": "openapi.tuyaeu.com",
    "us": "openapi.tuyaus.com",
    "in": "openapi.tuyain.com",
}

_FLASK_URL = "http://127.0.0.1:5050"
_prev_status = {}   # device_key → 이전 status (변경 시에만 이벤트 전송)
_prev_temper = {}   # device_key → 이전 temper_alarm 상태


def _sign(access_id, secret, t, token, method, path, body=""):
    """Tuya API 서명 생성"""
    content_hash = hashlib.sha256(body.encode()).hexdigest()
    str_to_sign = f"{method}\n{content_hash}\n\n{path}"
    message = access_id + token + str(t) + str_to_sign
    sign = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest().upper()
    return sign


def _get_token():
    """Tuya access token 발급"""
    host = REGION_HOSTS.get(TUYA_REGION, REGION_HOSTS["cn"])
    t = int(time.time() * 1000)
    path = "/v1.0/token?grant_type=1"
    sign = _sign(TUYA_ACCESS_ID, TUYA_ACCESS_SECRET, t, "", "GET", path)
    headers = {
        "client_id": TUYA_ACCESS_ID,
        "sign": sign,
        "t": str(t),
        "sign_method": "HMAC-SHA256",
    }
    resp = requests.get(f"https://{host}{path}", headers=headers, timeout=10)
    data = resp.json()
    if data.get("success"):
        return data["result"]["access_token"]
    raise RuntimeError(f"Tuya token 발급 실패: {data}")


def _get_device_status(token, device_id):
    """장치 상태 조회"""
    host = REGION_HOSTS.get(TUYA_REGION, REGION_HOSTS["cn"])
    t = int(time.time() * 1000)
    path = f"/v1.0/devices/{device_id}/status"
    sign = _sign(TUYA_ACCESS_ID, TUYA_ACCESS_SECRET, t, token, "GET", path)
    headers = {
        "client_id": TUYA_ACCESS_ID,
        "access_token": token,
        "sign": sign,
        "t": str(t),
        "sign_method": "HMAC-SHA256",
    }
    resp = requests.get(f"https://{host}{path}", headers=headers, timeout=10)
    return resp.json()


def _tuya_status_to_fire(data_points):
    """
    Tuya dps 값 → fire status 변환
    연기감지기 주요 코드:
      smoke_sensor_state: alarm / normal
      battery_percentage: 0~100
      fault: 배터리부족/탬퍼 등
    """
    status = "normal"
    battery = None
    message = ""
    temper = False

    for dp in data_points:
        code = dp.get("code", "")
        value = dp.get("value")
        if code in ("smoke_sensor_state", "smoke_sensor_status"):
            if value == "alarm":
                status = "alarm"
                message = "연기 감지됨"
            else:
                status = "normal"
                message = "정상"
        elif code == "battery_percentage":
            try:
                battery = int(value)
            except Exception:
                pass
        elif code == "temper_alarm":
            temper = bool(value)
        elif code == "fault" and value and value != 0:
            status = "fault"
            message = f"장치 오류: {value}"

    return status, battery, message, temper


def _post_fire_event(device_key, device_name, location, status, battery, event_type, message):
    """Flask 소방 이벤트 API 호출"""
    try:
        requests.post(
            f"{_FLASK_URL}/api/fire/event",
            json={
                "device_key": device_key,
                "device_name": device_name,
                "location": location,
                "status": status,
                "battery": battery,
                "event_type": event_type,
                "message": message,
            },
            timeout=5,
        )
    except Exception as e:
        logger.warning(f"[Tuya] Flask 이벤트 전송 실패: {e}")


def _post_fire_heartbeat(device_key, device_name, location, status, battery):
    """Flask heartbeat API 호출 (상태 동일 시 조용한 업데이트)"""
    try:
        requests.post(
            f"{_FLASK_URL}/api/fire/heartbeat",
            json={
                "device_key": device_key,
                "device_name": device_name,
                "location": location,
                "status": status,
                "battery": battery,
            },
            timeout=5,
        )
    except Exception as e:
        logger.warning(f"[Tuya] Flask heartbeat 전송 실패: {e}")


def _parse_devices():
    """환경변수에서 장치 목록 파싱"""
    devices = []
    if not TUYA_DEVICES_ENV:
        return devices
    for entry in TUYA_DEVICES_ENV.split(","):
        parts = entry.strip().split(":")
        if len(parts) == 4:
            devices.append({
                "key": parts[0],
                "name": parts[1],
                "location": parts[2],
                "device_id": parts[3],
            })
    return devices


class _TuyaTokenExpired(Exception):
    pass


def _poll_once(token, devices):
    """모든 장치 1회 폴링 → 상태 변화 시 이벤트 전송"""
    for dev in devices:
        try:
            result = _get_device_status(token, dev["device_id"])
            if not result.get("success"):
                # 토큰 만료(1010) → 즉시 재발급 신호
                if result.get("code") == 1010:
                    logger.warning("[Tuya] 토큰 만료(1010) 감지 → 즉시 재발급")
                    raise _TuyaTokenExpired()
                logger.warning(f"[Tuya] {dev['key']} 상태 조회 실패: {result}")
                # 오프라인으로 처리
                if _prev_status.get(dev["key"]) != "offline":
                    _post_fire_event(dev["key"], dev["name"], dev["location"],
                                     "offline", None, "offline", "장치 응답 없음")
                    _prev_status[dev["key"]] = "offline"
                continue

            dps = result.get("result", [])
            status, battery, message, temper = _tuya_status_to_fire(dps)

            # ① 연기 상태 변화
            prev = _prev_status.get(dev["key"])
            if status != prev:
                logger.info(f"[Tuya] {dev['key']} 상태변경: {prev} → {status}")
                event_type = "smoke_detected" if status == "alarm" else "status_update"
                _post_fire_event(dev["key"], dev["name"], dev["location"],
                                 status, battery, event_type, message)
                _prev_status[dev["key"]] = status
            else:
                _post_fire_heartbeat(dev["key"], dev["name"], dev["location"],
                                     status, battery)

            # ② 커버 변조/개봉 감지 (temper_alarm)
            prev_temper = _prev_temper.get(dev["key"])
            if temper and prev_temper is None:
                # 폴러 시작 시 이미 열려있으면 즉시 경보
                logger.warning(f"[Tuya] {dev['key']} 폴러 시작 시 커버 열림 감지")
                _post_fire_event(dev["key"], dev["name"], dev["location"],
                                 "alarm", battery, "temper_alarm", "장치 변조/개봉 감지됨")
            elif prev_temper is not None and temper != prev_temper:
                if temper:
                    logger.warning(f"[Tuya] {dev['key']} 커버 열림")
                    _post_fire_event(dev["key"], dev["name"], dev["location"],
                                     "alarm", battery, "temper_alarm", "장치 변조/개봉 감지됨")
                else:
                    logger.info(f"[Tuya] {dev['key']} 커버 닫힘")
                    _post_fire_event(dev["key"], dev["name"], dev["location"],
                                     status, battery, "temper_cleared", "장치 변조 경보 해제")
            _prev_temper[dev["key"]] = temper

        except Exception as e:
            logger.error(f"[Tuya] {dev['key']} 폴링 오류: {e}")


def start_tuya_poller():
    """백그라운드 스레드로 Tuya 폴링 시작"""
    if not TUYA_ACCESS_ID or not TUYA_ACCESS_SECRET:
        logger.info("[Tuya] TUYA_ACCESS_ID/SECRET 미설정 → 폴러 비활성화")
        return

    devices = _parse_devices()
    if not devices:
        logger.info("[Tuya] TUYA_DEVICES 미설정 → 폴러 비활성화")
        return

    def _run():
        logger.info(f"[Tuya] 폴러 시작 — {len(devices)}개 장치, {TUYA_POLL_INTERVAL}초 간격")
        token = None
        token_expire = 0

        while True:
            try:
                # 토큰 만료 시 재발급 (7200초=2시간, 여유 200초)
                if time.time() >= token_expire:
                    token = _get_token()
                    token_expire = time.time() + 7000
                    logger.info("[Tuya] 액세스 토큰 갱신")

                _poll_once(token, devices)

            except _TuyaTokenExpired:
                # 1010 에러 → 즉시 토큰 재발급
                token_expire = 0
            except Exception as e:
                logger.error(f"[Tuya] 폴러 오류: {e}")

            time.sleep(TUYA_POLL_INTERVAL)

    t = threading.Thread(target=_run, daemon=True)
    t.start()

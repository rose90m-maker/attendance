#!/usr/bin/env python3
"""wht_release.py — 원천징수영수증 검증 후 배포 (통과할 때만 배포)

검증을 사람이 눈으로 보고 배포를 따로 치면, 실패를 못 보고 배포하는 일이 생긴다.
여기서는 검증이 하나라도 실패하면 배포 단계로 넘어가지 않는다.

  1. _archive/_check_prework.py   종(전)근무지 열 · 74/75/77 검산 · 회귀
  2. _archive/_check_nontax.py    Ⅱ 비과세·감면 명세 · 52/54 세액감면
  3. deploy_and_restart.py        변경파일만 전송 + gunicorn graceful reload
  4. wht_watch.py --force         전 직원 재렌더 + 텔레그램 통보

ERP·NAS 자격증명은 .env 에서 각 스크립트가 알아서 읽는다.

사용:
  python3 wht_release.py              검증 → 통과 시 배포 → 사후 검증
  python3 wht_release.py --check      검증만 (배포 안 함)
  python3 wht_release.py --yes        확인 없이 진행
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

CHECKS = [
    ("종(전)근무지 · 세액 검산", ["_archive/_check_prework.py"]),
    ("비과세·감면 명세 · 52/54", ["_archive/_check_nontax.py"]),
    # 서식에 인쇄된 검산식 10종을 전 직원에게 돌린다. 2026-08-10 에 이 검사
    # 하나가 발급본 없이 결함 2건(64 기부금 잔값, 72 출처)과 미배선 2건
    # (고향사랑, 장애인전용)을 잡았다.
    ("산술 항등식 전수검사 (189명)", ["_archive/_check_identities.py"]),
]
DEPLOY = ("배포", ["deploy_and_restart.py"])
AFTER = ("전 직원 재검증", ["wht_watch.py", "--force"])


def run(title, argv):
    print(f"\n{'=' * 70}\n  {title}\n{'=' * 70}")
    r = subprocess.run([PY] + argv, cwd=HERE)
    ok = r.returncode == 0
    print(f"\n  → {title}: {'통과' if ok else f'실패 (종료코드 {r.returncode})'}")
    return ok


def main():
    check_only = "--check" in sys.argv
    auto = "--yes" in sys.argv

    failed = [t for t, a in CHECKS if not run(t, a)]
    if failed:
        print(f"\n❌ 검증 실패 — 배포하지 않습니다: {', '.join(failed)}")
        return 1
    print("\n✅ 검증 전부 통과")

    if check_only:
        print("   --check 라서 여기서 멈춥니다.")
        return 0

    if not auto:
        try:
            a = input("\n배포할까요? (y/N) ").strip().lower()
        except EOFError:
            a = ""
        if a != "y":
            print("배포하지 않았습니다.")
            return 0

    if not run(*DEPLOY):
        print("\n❌ 배포 실패 — 위 로그를 확인하세요.")
        return 1
    if not run(*AFTER):
        print("\n⚠️  배포는 됐으나 사후 검증이 실패했습니다. 로그를 확인하세요.")
        return 1
    print("\n✅ 배포와 사후 검증까지 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())

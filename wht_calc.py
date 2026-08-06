# -*- coding: utf-8 -*-
"""연말정산 정산명세 계산 엔진 (원천징수영수증 2쪽, 별지 제24호서식)

ERP 는 공제 '대상금액'만 저장하고 한도·산식 적용은 출력 시점에 수식엔진으로
계산한다. 여기서는 소득세법 산식을 그대로 구현한다.

검증 기준: 지창구 2025 귀속 ERP 실발급본 (2026-08-07 대조)
  근로소득공제 12,781,038 / 과세표준 31,657,791 / 산출세액 3,488,668
  신용카드 3,476,833 / 의료비공제 417,797 / 결정세액 2,115,426
  차감징수 -1,017,740 — 전 항목 일치.

절사 규칙 (발급본에서 확인):
  · 금액 계산은 원 미만 절사(floor)
  · 차감징수세액은 10원 미만 절사(0 방향)
"""
import math


def _floor(x):
    return math.floor(x + 1e-9)


def _floor10(x):
    """10원 미만 절사 — 음수도 0 방향으로 (-101,658 → -101,650)"""
    return int(x / 10) * 10 if x >= 0 else -(int(-x / 10) * 10)


def earned_income_deduction(gross):
    """22. 근로소득공제 (소득세법 §47)"""
    if gross <= 5_000_000:
        d = gross * 0.7
    elif gross <= 15_000_000:
        d = 3_500_000 + (gross - 5_000_000) * 0.4
    elif gross <= 45_000_000:
        d = 7_500_000 + (gross - 15_000_000) * 0.15
    elif gross <= 100_000_000:
        d = 12_000_000 + (gross - 45_000_000) * 0.05
    else:
        d = 14_750_000 + (gross - 100_000_000) * 0.02
    return min(_floor(d), 20_000_000)


def progressive_tax(base):
    """49. 산출세액 — 기본세율 (2023~ 세율표)"""
    brackets = [
        (14_000_000, 0.06, 0),
        (50_000_000, 0.15, 840_000),
        (88_000_000, 0.24, 6_240_000),
        (150_000_000, 0.35, 15_360_000),
        (300_000_000, 0.38, 37_060_000),
        (500_000_000, 0.40, 94_060_000),
        (1_000_000_000, 0.42, 174_060_000),
        (float("inf"), 0.45, 384_060_000),
    ]
    prev = 0
    for limit, rate, acc in brackets:
        if base <= limit:
            return _floor(acc + (base - prev) * rate)
        prev = limit
    return 0


def earned_income_tax_credit(calc_tax, gross):
    """55. 근로소득 세액공제 (§59) — 한도식 포함"""
    if calc_tax <= 1_300_000:
        credit = calc_tax * 0.55
    else:
        credit = 715_000 + (calc_tax - 1_300_000) * 0.30
    if gross <= 33_000_000:
        limit = 740_000
    elif gross <= 70_000_000:
        limit = max(660_000, 740_000 - (gross - 33_000_000) * 0.008)
    elif gross <= 120_000_000:
        limit = max(500_000, 660_000 - (gross - 70_000_000) * 0.5)
    else:
        limit = max(200_000, 500_000 - (gross - 120_000_000) * 0.5)
    return _floor(min(credit, limit))


def card_deduction(gross, plastic, debit_cash, culture, tradition, transit,
                   spend_cur=0, spend_prev=0):
    """41. 신용카드등 사용액 소득공제 (조특법 §126의2)

    지창구 2025 검증: 기본한도 300만 + 추가한도(전통·대중·문화 공제분, 한도 300만)
    """
    total = plastic + debit_cash + culture + tradition + transit
    threshold = gross * 0.25
    if total <= threshold:
        return 0
    raw = (plastic * 0.15 + debit_cash * 0.30 + culture * 0.30
           + tradition * 0.40 + transit * 0.40)
    # 문턱 보정 — 신용카드분부터 문턱에 충당
    if plastic >= threshold:
        adj = threshold * 0.15
    elif plastic + debit_cash + culture >= threshold:
        adj = plastic * 0.15 + (threshold - plastic) * 0.30
    else:
        adj = (plastic * 0.15 + (debit_cash + culture) * 0.30
               + (threshold - plastic - debit_cash - culture) * 0.40)
    deduc = raw - adj
    base_limit = 3_000_000 if gross <= 70_000_000 else 2_500_000
    if deduc <= base_limit:
        return _floor(deduc)
    extra = tradition * 0.40 + transit * 0.40 + (culture * 0.30 if gross <= 70_000_000 else 0)
    # 소비증가분 특례 — 전년 105% 초과분의 10% (한도 100만)
    # 2024 귀속에서 시행. 박광원 2024 검증: 초과 3,447,817 → 344,782
    if spend_cur and spend_prev:
        # 이 항목만 절사가 아니라 반올림이다.
        # 박광원 2024: 초과 3,447,816.75 × 10% = 344,781.675 → 344,782
        over = max(0, spend_cur - spend_prev * 1.05)
        extra += min(round(over * 0.10), 1_000_000)
    extra_limit = 3_000_000 if gross <= 70_000_000 else 2_000_000
    return _floor(base_limit + min(deduc - base_limit, extra, extra_limit))


def compute(d):
    """정산명세 전체 계산.

    d: {
      gross,                       # 총급여 (급여+상여+…)
      persons_basic,               # 기본공제 인원 (본인 포함)
      persons_old, persons_disabled, is_woman, is_single_parent,
      np_pension,                  # 국민연금
      health, employ,              # 건강·고용보험료 (공제금액)
      house_rent_principal,        # 주택임차차입금 원리금상환액 (대출기관)
      card_plastic, card_debit_cash, card_culture, card_tradition, card_transit,
      ins_guarantee,               # 보장성보험료 대상금액
      med_full,                    # 의료비 전액공제대상 (본인·65세·6세이하·장애인)
      med_etc,                     # 그 밖의 의료비
      med_refund,                  # 실손보험금 (차감)
      edu_amount,                  # 교육비 대상금액
      donate_political,            # 정치자금 기부금
      donate_special,              # 특례기부금
      donate_general,              # 일반기부금(종교단체외)
      donate_religion,             # 일반기부금(종교단체)
      prepaid_tax, prepaid_local,  # 기납부 (주현근무지)
    }
    반환: 서식 항목번호 → 값
    """
    r = {}
    g = d["gross"]
    r[21] = g
    r[22] = earned_income_deduction(g)
    r[23] = g - r[22]

    # 인적공제
    r[24] = 1_500_000                                   # 본인
    r[25] = 1_500_000 if d.get("has_spouse") else 0     # 배우자
    r["26cnt"] = d.get("persons_dependent", 0)
    r[26] = r["26cnt"] * 1_500_000
    r["27cnt"] = d.get("persons_old", 0)
    r[27] = r["27cnt"] * 1_000_000
    r["28cnt"] = d.get("persons_disabled", 0)
    r[28] = r["28cnt"] * 2_000_000
    r[29] = 500_000 if d.get("is_woman") else 0
    r[30] = 1_000_000 if d.get("is_single_parent") else 0

    # 연금·특별소득공제
    r[31] = d.get("np_pension", 0)
    housing = _floor(min(d.get("house_rent_principal", 0) * 0.40, 4_000_000))
    r["34rent"] = housing

    # 34. 장기주택저당차입금 이자상환액 — 유형별 한도 (2024~ 상향 한도)
    mort_limits = {"fix_nonpay": 20_000_000, "fix_or_nonpay": 18_000_000,
                   "etc": 8_000_000, "y10_fix_or_nonpay": 6_000_000}
    mort = 0
    for key, limit in mort_limits.items():
        mort += min(d.get(f"mort_{key}", 0), limit)
    r["34mort"] = _floor(mort)
    r[35] = d.get("health", 0) + d.get("employ", 0) + housing + r["34mort"]
    r[36] = (r[23] - r[24] - r[25] - r[26] - r[27] - r[28] - r[29] - r[30]
             - r[31] - r[35])

    # 그 밖의 소득공제
    # 39㉯ 주택청약종합저축 — 납입액(한도 300만)의 40%
    r["39sub"] = _floor(min(d.get("housing_saving", 0), 3_000_000) * 0.40)
    r[41] = card_deduction(g, d.get("card_plastic", 0), d.get("card_debit_cash", 0),
                           d.get("card_culture", 0), d.get("card_tradition", 0),
                           d.get("card_transit", 0),
                           d.get("card_spend_cur", 0), d.get("card_spend_prev", 0))
    r[46] = r[41] + r["39sub"]
    r[47] = 0

    r[48] = max(0, r[36] - r[46])                       # 과세표준
    r[49] = progressive_tax(r[48])                      # 산출세액

    # 세액감면 — 중소기업 취업자 소득세 감면 (조특법 §30, 서식 52번 칸)
    # 감면세액 = 산출세액 × (감면대상급여/총급여) × 감면율, 한도 연 200만
    reduc_pay = d.get("smb_reduc_pay", 0)
    reduc_rate = d.get("smb_reduc_rate", 0.9)
    if reduc_pay and g:
        r[52] = _floor(min(r[49] * min(reduc_pay / g, 1.0) * reduc_rate,
                           2_000_000))
    else:
        r[52] = 0
    r[54] = r[52]

    # 세액공제
    # 중소기업 감면자는 근로소득세액공제를 (산출세액-감면세액)/산출세액 로 안분
    # (노지수 2025 발급본 검증: 665,660 × 569,847/2,569,847 = 147,605)
    r[55] = earned_income_tax_credit(r[49], g)
    if r[54] and r[49]:
        r[55] = _floor(r[55] * (r[49] - r[54]) / r[49])

    # 59~60. 연금계좌 (퇴직연금·연금저축) — 총급여 5,500만 기준 15%/12%
    pension_rate = 0.15 if g <= 55_000_000 else 0.12
    saving = min(d.get("pension_saving", 0), 6_000_000)         # 연금저축 한도
    retire = min(d.get("pension_retire", 0), 9_000_000 - saving)  # 합산 900만
    r["59obj"], r["60obj"] = retire, saving
    r[59] = _floor(retire * pension_rate)
    r[60] = _floor(saving * pension_rate)

    # 70. 월세액 — 총급여 5,500만 이하 17%, 8,000만 이하 15% (대상 한도 1,000만)
    rent = min(d.get("monthly_rent", 0), 10_000_000)
    rent_rate = 0.17 if g <= 55_000_000 else (0.15 if g <= 80_000_000 else 0)
    r["70obj"] = rent if rent_rate else 0
    r[70] = _floor(rent * rent_rate)
    r["61obj"] = min(d.get("ins_guarantee", 0), 1_000_000)
    # 장애인전용 보장성보험료 — 별도 한도 100만, 15%
    r["61dis_obj"] = min(d.get("ins_disabled", 0), 1_000_000)
    r["61dis"] = _floor(r["61dis_obj"] * 0.15)
    r[61] = _floor(r["61obj"] * 0.12) + r["61dis"]
    med_obj = (d.get("med_full", 0) + d.get("med_etc", 0)
               - d.get("med_refund", 0) - g * 0.03)
    r["62obj"] = max(0, round(med_obj))
    r[62] = _floor(r["62obj"] * 0.15)
    r["63obj"] = d.get("edu_amount", 0)
    r[63] = _floor(r["63obj"] * 0.15)

    pol = d.get("donate_political", 0)
    r["64pol_lo_obj"] = min(pol, 100_000)
    r["64pol_lo"] = _floor(min(pol, 100_000) * 100 / 110)
    r["64pol_hi_obj"] = max(0, pol - 100_000)
    r["64pol_hi"] = _floor(max(0, pol - 100_000) * 0.15)
    # 고향사랑기부금 — 10만 이하 100/110, 초과분 15%
    home = d.get("donate_hometown", 0)
    r["64home_lo_obj"] = min(home, 100_000)
    r["64home_lo"] = _floor(min(home, 100_000) * 100 / 110)
    r["64home_hi_obj"] = max(0, home - 100_000)
    r["64home_hi"] = _floor(max(0, home - 100_000) * 0.15)
    # 이월기부금은 당해분에 합산해 15% (1천만 초과 30% 구간은 미도달 가정)
    r["64spec_obj"] = d.get("donate_special", 0)
    r["64spec"] = _floor(r["64spec_obj"] * 0.15)
    r["64gen_obj"] = d.get("donate_general", 0)
    r["64gen"] = _floor(r["64gen_obj"] * 0.15)
    r["64rel_obj"] = d.get("donate_religion", 0)
    r["64rel"] = _floor(r["64rel_obj"] * 0.15)
    r[64] = (r["64pol_lo"] + r["64pol_hi"] + r["64home_lo"] + r["64home_hi"]
             + r["64spec"] + r["64gen"] + r["64rel"])
    # 57. 자녀세액공제 — 2024~ 기준 (1명 15만, 2명 35만, 3명부터 +30만)
    n_child = d.get("children_credit", 0)
    if n_child <= 0:
        r[57] = 0
    elif n_child == 1:
        r[57] = 150_000
    elif n_child == 2:
        r[57] = 350_000
    else:
        r[57] = 350_000 + (n_child - 2) * 300_000
    r["57cnt"] = n_child
    r["57birth_cnt"] = d.get("children_birth", 0)
    r[57] += r["57birth_cnt"] * 300_000

    r[66] = 0                                           # 표준세액공제 (특별공제 적용 시 0)

    # 세액공제는 (산출세액 - 세액감면) 한도에서 순서대로 소진된다.
    # 한도를 넘는 항목은 서식에 0(빈칸)으로 찍힌다.
    # (박광원 2024 검증: 교육비 90,538 → 0, 보험료 120,000 → 33,939)
    room = max(0, r[49] - r[54])
    def _take(amt):
        nonlocal room
        used = min(amt, room)
        room -= used
        return used

    r[55] = _take(r[55])
    r[57] = _take(r[57])
    r[59] = _take(r[59])
    r[60] = _take(r[60])
    r[61] = _take(r[61])
    r[62] = _take(r[62])
    r[63] = _take(r[63])
    r[64] = _take(r[64])
    r[66] = _take(r[66])
    r[70] = _take(r[70])

    r[65] = r[61] + r[62] + r[63] + r[64]
    r[71] = r[55] + r[57] + r[59] + r[60] + r[65] + r[66] + r[70]
    r[72] = max(0, r[49] - r[54] - r[71])               # 결정세액

    # 1쪽 세액명세
    r["73tax"] = r[72]
    r["73local"] = _floor(r[72] * 0.1)
    r["75tax"] = d.get("prepaid_tax", 0)
    r["75local"] = d.get("prepaid_local", 0)
    r["77tax"] = _floor10(r["73tax"] - r["75tax"])
    r["77local"] = _floor10(r["73local"] - r["75local"])
    r[82] = round(r[72] / g * 100, 1) if g else 0       # 실효세율
    return r

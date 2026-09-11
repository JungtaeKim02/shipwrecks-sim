"""SSS 현실화 공용 모듈: 거리 1D 물리 후처리(TVG/흡수/dB)만.

실제 SSS sonar equation:  EL = SL - 2*TL(r) + Sb(theta) + B(theta) + 10log10(A)
  - Sb(theta) 후방산란, B(theta) 빔지향성 : 각/기하에 의존 -> 시뮬 C++ 안에서 레이별로
    계산해야 물리적. (Sb = R^2*cos(inc), B = 양방향 빔패턴 |D|^4 : RaycastSidescanSonar.cpp)
  - 2*TL(r) = 2*(20log10(r) + alpha*r) : 오직 거리 r 만의 함수 -> 최종 1D 출력만으로
    정확히 계산됨. 여기(후처리)에 두는 것이 맞다.
근거 없이 임의로 더하는 근사(부피산란 배경, multipath 고스트)는 두지 않는다.
"""
import math
import pathlib
import numpy as np


# ---------------------------------------------------------------------------
# 1) Francois-Garrison(1982) 해수 흡수계수 alpha [dB/m]
# ---------------------------------------------------------------------------
def francois_garrison_alpha(f_khz, T=15.0, S=32.0, D=20.0, pH=8.0):
    """주파수 f[kHz], 수온 T[C], 염분 S[ppt], 수심 D[m], pH -> alpha [dB/m]."""
    f = float(f_khz)
    c = 1412.0 + 3.21 * T + 1.19 * S + 0.0167 * D          # 음속 근사 [m/s]

    # 붕산(H3BO3)
    A1 = (8.86 / c) * 10 ** (0.78 * pH - 5)                 # dB/km/kHz
    P1 = 1.0
    f1 = 2.8 * np.sqrt(S / 35.0) * 10 ** (4 - 1245.0 / (T + 273.0))   # kHz

    # 황산마그네슘(MgSO4)
    A2 = 21.44 * (S / c) * (1 + 0.025 * T)                  # dB/km/kHz
    P2 = 1 - 1.37e-4 * D + 6.2e-9 * D ** 2
    f2 = (8.17 * 10 ** (8 - 1990.0 / (T + 273.0))) / (1 + 0.0018 * (S - 35.0))

    # 순수물
    if T <= 20:
        A3 = 4.937e-4 - 2.59e-5 * T + 9.11e-7 * T ** 2 - 1.50e-8 * T ** 3
    else:
        A3 = 3.964e-4 - 1.146e-5 * T + 1.45e-7 * T ** 2 - 6.5e-10 * T ** 3
    P3 = 1 - 3.83e-5 * D + 4.9e-10 * D ** 2

    alpha_db_km = (A1 * P1 * f1 * f ** 2 / (f1 ** 2 + f ** 2)
                   + A2 * P2 * f2 * f ** 2 / (f2 ** 2 + f ** 2)
                   + A3 * P3 * f ** 2)
    return alpha_db_km / 1000.0                              # dB/m


# ---------------------------------------------------------------------------
# 2) 기준 SSS 센서 설정 (예: EdgeTech 계열 고주파 채널, 서해 물성)
# ---------------------------------------------------------------------------
# 실제 SSS 스펙 근사
SSS_FREQ_KHZ   = 540.0     # EdgeTech 2205 고해상 채널 (AI4Shipwrecks)
RANGE_MIN      = 1.0       # m
RANGE_MAX      = 40.0      # m (편측 swath)
RANGE_RES      = 0.05      # m/bin  -> RangeBins = 780
ELEVATION_DEG  = 0.5       # along-track 빔폭 (실제 0.3~1)
AZIMUTH_DEG    = 60.0      # across-track 팬 (편측)
WATER_DENSITY  = 1024.0    # 서해 해수
WATER_SOUND    = 1500.0    # 서해 연평균 음속
# 표시 창 = 이미지별 분위수가 아니라 고정 시그마 폭.
#
# normalize_intensity() 는 각 이미지를 평균 0 / 표준편차 1 로 표준화한다(정의상 std=1
# 이 되므로 "표준화 이후" 시그마는 항상 같은 물리적 의미를 갖는다 — 실제 dB 스케일이
# 아니라 "그 이미지 자기 평균 대비 몇 표준편차인가"). 분위수 매핑(percentile)은 여기에
# *추가로* 한 번 더 이미지마다 다른 클리핑 비율을 적용하는 셈이라, 같은 물성이라도
# 씬 구성(밝은 표적 개수 등)에 따라 검정/흰색 기준이 흔들린다(실측: 씬 간 최대 0.56σ
# 편차). 대신 고정 시그마 폭을 쓰면 모든 이미지가 항상 "평균 ± N 표준편차"라는 같은
# 규칙으로 매핑되어, 실제 장비가 서베이 내내 게인/TVG 설정을 일정하게 유지하는 것과
# 대응된다(반대로 서베이마다 게인을 다시 잡는 것은 실제로도 흔하지만, 그건 여기서
# normalize_intensity 가 이미 하는 일이다 — 이중으로 적응시키지 않는다).
#
# ±2.0σ 채택 근거(실측, 2026-07-30 검증 세션 기준): 클리핑 비율이 하단 0.96%/상단
# 0.81%(합 1.77%)로 좌우 균형이 맞고, 실제 8비트 표시에서 흔히 쓰는 "평균±2표준편차"
# 관례와 일치한다. ±1.5 는 하단이 13%나 잘려 배경 텍스처 손실이 크고, ±2.5 이상은
# 클리핑이 0%대로 떨어져 대비가 부족해진다(밋밋해짐).
DISPLAY_SIGMA = 2.0
ADD_SIGMA      = 0.0       # 레거시 엔진 잡음: complex-power-v2에서는 항상 비활성
MULT_SIGMA     = 0.5       # 레거시 호환 키: 새 모델의 contrast를 정하지 않음


def make_sss_config(azimuth=AZIMUTH_DEG, elevation=ELEVATION_DEG,
                    range_min=RANGE_MIN, range_max=RANGE_MAX, range_res=RANGE_RES,
                    add_sigma=ADD_SIGMA, mult_sigma=MULT_SIGMA,
                    speckle_strength=1.0, speckle_seed=0,
                    signal_model="coherent", frequency_khz=SSS_FREQ_KHZ,
                    scatter_correlation_length_m=0.05,
                    texture_shape=0.0, texture_correlation_length_m=1.0):
    """RaycastSidescanSonar 엔진 설정을 만든다.

    ``add_sigma``와 ``mult_sigma``는 기존 호출부/시나리오 호환을 위해 남아 있다.
    complex-power-v2에서 엔진 가산 잡음은 항상 0이고, ``MultSigma``는 무시된다.
    수신기 잡음 power는 이 설정이 아니라 :func:`process_side`에 전달한다.
    """
    return {
        "RangeMin": range_min, "RangeMax": range_max, "RangeRes": range_res,
        "Azimuth": azimuth, "Elevation": elevation,
        "AddSigma": 0.0, "MultSigma": mult_sigma,
        "SpeckleDistribution": "complex_gaussian",
        "SpeckleStrength": speckle_strength, "SpeckleSeed": speckle_seed,
        "SignalModel": signal_model, "FrequencyKhz": frequency_khz,
        "ScatterCorrelationLengthM": scatter_correlation_length_m,
        "TextureShape": texture_shape,
        "TextureCorrelationLengthM": texture_correlation_length_m,
        "WaterDensity": WATER_DENSITY, "WaterSpeedSound": WATER_SOUND,
    }


# ---------------------------------------------------------------------------
# 3) 물리 후처리: TVG(전송손실 + 흡수) 적용으로 실제 거리별 SNR 구배 생성
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 물리 앵커 (2026-07-31). 표시 매핑과 노이즈 레벨을 이미지 통계가 아니라 여기에 묶는다.
# ---------------------------------------------------------------------------
# 센서가 내보내는 값의 정체: RaycastSidescanSonar.cpp 는 val = R^2 * cos(입사각) 을
# 쓴다. R^2 은 **강도** 반사계수이므로 val 은 강도(power)다. 따라서 dB 변환은
# 10*log10 이고, dB 손실을 선형값에 걸 때도 10 으로 나눈다(진폭이면 20).
# 이전 코드는 강도에 20*log10 을 써서 dB 스케일이 2배로 부풀어 있었다. 표준화 뒤에는
# 상쇄되어 눈에 안 띄지만, dB 로 역산한 noise_floor 값이 그만큼 틀어졌다.
INTENSITY_DB = 10.0

# 표시 상한: 완전반사체(R^2=1)를 수직입사(cos=1)로 때렸을 때 val=1.0 -> 0 dB.
# 물리적으로 이보다 밝은 값은 나올 수 없으므로 흰색의 하드 앵커로 쓴다.
SPECULAR_CEILING_DB = 0.0

# 노이즈 바닥 보정의 기준 거리 [m]. EdgeTech 2205 는 540 kHz 에서 정격 최대거리
# 150 m 로 규정된다(2205 사양서). 소나의 정격 최대거리는 "해저 반사가 더는 노이즈
# 위로 나오지 않는 거리"이므로, 그 지점에서 SNR=0 dB 가 되도록 노이즈 바닥을 역산한다.
# 이러면 노이즈 레벨이 임의값이 아니라 센서 제원에서 나온다.
RATED_MAX_RANGE_M = 150.0

# 완전발달 스페클의 1% 레벨 [dB, 평균 대비].
# 진폭이 Rayleigh 면 강도는 지수분포이고 CDF = 1 - exp(-I/mu) 이므로
#   1% 지점 I = -mu*ln(0.99) = 0.01005*mu  ->  10*log10(0.01005) = -19.98 dB
# 표시 창이 이보다 좁으면 **해저 텍스처 자체가 검게 잘린다**. 임의 상수가 아니라
# 우리가 이미 모사하고 있는 스페클 통계에서 나온다.
SPECKLE_P1_DB = -19.98

# TVG-off 8-bit rendering black point. Empty receiver-bin power is Exponential(Pn),
# so this is an analytically defined threshold rather than an image percentile.
# Mapping its 99th percentile to black suppresses 99% of water-column electronics
# noise in the display while preserving the unmodified dB/complex arrays on disk.
DISPLAY_NOISE_BLACK_QUANTILE = 0.99


def reference_backscatter(cos_inc=0.7071):
    """기준 해저(모래) 후방산란 선형값. materials.csv 의 모래 임피던스로 계산.

    R = (z_sed - z_water)/(z_sed + z_water), val = R^2 * cos(입사각).
    cos_inc 기본값은 45도(사이드스캔의 대표 입사각).
    """
    z_sed = 1298.0 * 1564.0            # materials.csv 모래 (APL-UW TR9407 Ch.IV Table 2)
    z_w = WATER_DENSITY * WATER_SOUND
    R = (z_sed - z_w) / (z_sed + z_w)
    return R * R * cos_inc


def noise_floor_from_rated_range(range_min, alpha_db_m,
                                 rated_range_m=RATED_MAX_RANGE_M, ref_bs=None):
    """수신기/앰비언트 노이즈 바닥 [선형 강도]. 센서 정격 최대거리에서 역산한다.

    TVG 적용 후 해저 반사는 거리무관 상수 ref_bs 가 되고, 노이즈는 TVG 로 증폭되어
    noise * 10^(2TL(r)/10) 로 상승한다. 정격 최대거리에서 이 둘이 같아지는 값:

        noise = ref_bs * 10^(-2*TL(rated)/10)

    이전 값 3e-6 은 "빔 가장자리에서 SNR 15 dB" 로 역산했다는데, TVG 증폭과 상한을
    빼먹은 계산이라 실제로는 근거리 +61 dB / 47 m 에서 -19 dB 였다(2026-07-31 실측).
    """
    if ref_bs is None:
        ref_bs = reference_backscatter()
    TL = 20 * np.log10(rated_range_m / range_min) + alpha_db_m * rated_range_m
    return float(ref_bs * 10 ** (-2 * TL / INTENSITY_DB))


def add_complex_receiver_noise(signal_power, noise_power, rng):
    """Add circular complex Gaussian receiver noise and return detected power.

    ``signal_power`` is already a non-negative detected echo-power realization.
    Circular noise is rotationally invariant, so the unknown echo phase can be set
    to zero without changing the output distribution:

        Z = sqrt(Ps) + sqrt(Pn/2) * (N_I + i N_Q)
        P = |Z|^2

    For Ps=0 this is Exponential(mean=Pn), as required for an empty receiver bin.
    ``noise_power`` is therefore a mean power, not a Rayleigh amplitude sigma.
    """
    signal_power = np.asarray(signal_power, dtype=float)
    pn = float(noise_power)
    if pn <= 0:
        return signal_power.copy()
    sigma = math.sqrt(pn / 2.0)
    noise_i = rng.normal(0.0, sigma, size=signal_power.shape)
    noise_q = rng.normal(0.0, sigma, size=signal_power.shape)
    return (np.sqrt(np.maximum(signal_power, 0.0)) + noise_i) ** 2 + noise_q ** 2


def lfm_matched_filter_kernel(range_res_m, bandwidth_hz, sound_speed_ms=1500.0,
                              pulse_duration_s=1e-3, max_taps=129):
    """Return the peak-normalized baseband autocorrelation of an ideal LFM pulse.

    A scatterer displaced by ``dr`` produces delay ``tau=2*dr/c``.  For a
    rectangular linear chirp, the matched-filter response is approximated by

        h(tau) = (1-|tau|/T) sinc(B*tau*(1-|tau|/T)), |tau| < T.

    This is the signal-domain point-spread function: finite bandwidth, pulse
    compression and range sidelobes are therefore applied before square-law
    detection.  ``max_taps`` is an explicit computation guard, not a change to
    range resolution; a warning-worthy long tail is truncated symmetrically.
    """
    dr = float(range_res_m)
    bw = float(bandwidth_hz)
    c = float(sound_speed_ms)
    T = float(pulse_duration_s)
    if dr <= 0 or bw <= 0 or c <= 0 or T <= 0:
        return np.array([1.0], dtype=float)
    support_m = 0.5 * c * T
    half = max(1, int(np.ceil(support_m / dr)))
    half = min(half, max(1, (int(max_taps) - 1) // 2))
    offsets = np.arange(-half, half + 1, dtype=float) * dr
    tau = 2.0 * offsets / c
    overlap = np.maximum(0.0, 1.0 - np.abs(tau) / T)
    kernel = overlap * np.sinc(bw * tau * overlap)
    kernel[half] = 1.0
    return kernel.astype(float)


def _convolve_range_complex(field, kernel):
    """Convolve every ping along slant range without mixing adjacent pings."""
    field = np.asarray(field, dtype=np.complex128)
    kernel = np.asarray(kernel, dtype=np.complex128)
    max_len = max(1, 2 * field.shape[1] - 1)
    if kernel.size > max_len:
        center = kernel.size // 2
        half = (max_len - 1) // 2
        kernel = kernel[center - half:center + half + 1]
    if kernel.size == 1:
        return field.copy()
    out = np.empty_like(field)
    for row in range(field.shape[0]):
        out[row] = np.convolve(field[row], kernel, mode="same")
    return out


def add_complex_receiver_noise_field(signal_field, noise_power, rng,
                                     matched_filter_kernel=None):
    """Add circular receiver noise to a complex field before matched filtering.

    ``noise_power`` is defined at the matched-filter output.  When a peak-normalized
    filter is supplied, the white input noise is divided by its energy so that the
    marginal empty-bin output remains ``Exponential(mean=noise_power)``.  The filter
    nevertheless gives neighboring range bins the physically expected correlation.
    """
    signal_field = np.asarray(signal_field, dtype=np.complex128)
    kernel = (np.array([1.0]) if matched_filter_kernel is None
              else np.asarray(matched_filter_kernel, dtype=float))
    filtered_signal = _convolve_range_complex(signal_field, kernel)
    pn = max(0.0, float(noise_power))
    if pn == 0.0:
        return filtered_signal
    filter_energy = float(np.sum(np.abs(kernel) ** 2))
    input_power = pn / max(filter_energy, 1e-30)
    sigma = math.sqrt(input_power / 2.0)
    white = (rng.normal(0.0, sigma, size=signal_field.shape)
             + 1j * rng.normal(0.0, sigma, size=signal_field.shape))
    return filtered_signal + _convolve_range_complex(white, kernel)


def process_complex_side(raw_field, range_min=RANGE_MIN, range_max=RANGE_MAX,
                         alpha_db_m=None, noise_power=None, tvg_cap_db=None, seed=0,
                         apply_tvg=True, bandwidth_hz=63e3,
                         sound_speed_ms=WATER_SOUND, pulse_duration_s=1e-3,
                         matched_filter=True, matched_filter_max_taps=129,
                         geometric_spreading=True):
    """Coherent-field-v3 side trace -> detected, TVG-corrected power in dB.

    The engine output is a complex, pre-propagation pressure-like field whose
    squared magnitude has the ray-derived mean backscatter power.  Processing order:

      complex field -> two-way amplitude loss -> chirp matched filter plus complex
      receiver noise -> |.|^2 detection -> TVG -> 10log10.

    This preserves phase until detection and gives the signal and receiver noise the
    same range PSF.  It is the practical signal-level counterpart to ``process_side``,
    which remains available for legacy detected-power data.
    """
    raw_field = np.asarray(raw_field, dtype=np.complex128)
    if raw_field.ndim != 2:
        raise ValueError(f"raw_field must be [pings,bins], got {raw_field.shape}")
    nbins = raw_field.shape[1]
    if alpha_db_m is None:
        alpha_db_m = francois_garrison_alpha(SSS_FREQ_KHZ)
    bin_width = (range_max - range_min) / max(nbins, 1)
    r = range_min + (np.arange(nbins, dtype=float) + 0.5) * bin_width
    r = np.maximum(r, range_min)
    spreading_db = (20 * np.log10(r / range_min)
                    if geometric_spreading else np.zeros_like(r))
    TL = spreading_db + alpha_db_m * r
    # TL is an amplitude dB convention. A two-way path therefore multiplies the
    # complex field by 10^(-2TL/20); its detected power gets 10^(-2TL/10).
    recv_field = raw_field * 10 ** (-(2.0 * TL) / 20.0)
    if noise_power is None:
        noise_power = noise_floor_from_rated_range(range_min, alpha_db_m)
    kernel = (lfm_matched_filter_kernel(
        bin_width, bandwidth_hz,
        sound_speed_ms=sound_speed_ms, pulse_duration_s=pulse_duration_s,
        max_taps=matched_filter_max_taps) if matched_filter else np.array([1.0]))
    rng = np.random.default_rng(None if seed is None else seed)
    received = add_complex_receiver_noise_field(
        recv_field, noise_power, rng, matched_filter_kernel=kernel)
    power = np.abs(received) ** 2
    if apply_tvg:
        r_tvg = np.minimum(r, RATED_MAX_RANGE_M)
        spreading_tvg_db = (20 * np.log10(r_tvg / range_min)
                            if geometric_spreading else np.zeros_like(r_tvg))
        TL_tvg = spreading_tvg_db + alpha_db_m * r_tvg
        tvg = 10 ** (2 * TL_tvg / INTENSITY_DB)
        if tvg_cap_db is not None:
            tvg = np.minimum(tvg, 10 ** (tvg_cap_db / INTENSITY_DB))
        power = power * tvg
    return INTENSITY_DB * np.log10(power + 1e-30)


def process_side(raw, range_min=RANGE_MIN, range_max=RANGE_MAX,
                 alpha_db_m=None, noise_floor=None, tvg_cap_db=None, seed=0,
                 apply_tvg=True):
    """단측 waterfall(raw: [pings, bins], nadir=bin0) -> 물리보정 dB 이미지.

    raw는 엔진이 만든 신호 power다. 결정론적 R^2*cos에 coherent/diffuse complex
    speckle을 적용해 검파한 값이며, 아직 수신기 잡음은 들어 있지 않다.

    noise_floor 는 **수신단 앰비언트/수신기 평균 noise power**다. 전송손실 뒤 복소
    Gaussian 으로 신호에 합쳐지고
    TVG 로 함께 증폭되므로, TVG 후에는 거리에 따라 상승한다 — 실제 사이드스캔에서
    원거리가 지저분해지는 거동이 이것이다. (센서 C++ 의 AddSigma 는 후방산란 단계에
    더해져 TL 과 TVG 가 정확히 상쇄되므로 거리 무관 평탄한 바닥이 된다. 수신기 노이즈는
    이쪽에 두는 것이 맞다.)
    noise_floor=None이면 기준 해저가 센서 정격 최대거리에서 SNR=0 dB가 되도록
    평균 noise power를 역산한다. 인자 이름은 하위 호환 때문에 noise_floor로
    유지하지만 값의 단위/의미는 Rayleigh sigma가 아니라 선형 평균 power다.
    apply_tvg=True 면 TVG(=거리보정 게인)로 거리의존성 제거(논문 §3.6: "returned intensity
      is not range-dependent"). False 면 게인 없이 원거리 어둡게(거리의존 유지).
    흡수는 alpha_db_m=0 을 주면 끔(확산손실만).
    """
    raw = np.asarray(raw, dtype=float)
    raw = np.clip(raw, 0, None)
    nbins = raw.shape[1]
    if alpha_db_m is None:
        alpha_db_m = francois_garrison_alpha(SSS_FREQ_KHZ)

    r = np.linspace(range_min, range_max, nbins)
    r = np.maximum(r, range_min)
    TL = 20 * np.log10(r / range_min) + alpha_db_m * r        # 편도 전송손실 [dB]
    two_way = 2 * TL
    if noise_floor is None:
        noise_floor = noise_floor_from_rated_range(range_min, alpha_db_m)

    rng = np.random.default_rng(None if seed is None else seed)
    # raw 는 강도이므로 dB 손실은 10 으로 나눠 건다(진폭이면 20).
    recv_signal = raw * 10 ** (-two_way / INTENSITY_DB)        # 수신 에코 power
    recv = add_complex_receiver_noise(recv_signal, noise_floor, rng)
    if apply_tvg:
        # TVG 는 **정격 최대거리에서 멈춘다**.
        #
        # 이력: 원래 60 dB 고정 상한이 있었는데 근거 없는 임의 상수라 2026-07-31 에
        # 제거했다. 그러자 이번엔 게인이 무한정 커져, range_max 343 m 에서 197 dB 까지
        # 올라가 **노이즈 바닥을 +38 dB 로 증폭**했다(정반사 상한 0 dB 의 6900배).
        # 원거리가 통째로 흰색 포화되고, 정작 진짜 해저(-24.8 dB)는 표시 창 아래로
        # 밀려 검게 나왔다 — 명암이 완전히 뒤집혔다(2026-08-04 실측).
        #
        # 물리적으로 맞는 경계: 정격 최대거리는 "해저 반사가 더는 노이즈 위로 나오지
        # 않는 거리"다(noise_floor_from_rated_range 가 그렇게 정의한다). 그 너머로
        # 게인을 더 올려도 되살릴 신호가 없고 노이즈만 커진다. 그래서
        # 2*TL(min(r, rated)) 로 묶는다. 새 상수가 아니라 노이즈 바닥과 **같은 앵커**
        # (센서 정격 거리)를 쓴다.
        # 결과: 정격 밖에서 노이즈는 기준 해저 레벨에서 평탄해지고, 진짜 원거리
        # 반사는 그만큼 어두워진다(실제로도 약하다).
        r_tvg = np.minimum(r, RATED_MAX_RANGE_M)
        TL_tvg = 20 * np.log10(r_tvg / range_min) + alpha_db_m * r_tvg
        tvg = 10 ** (2 * TL_tvg / INTENSITY_DB)
        if tvg_cap_db is not None:                 # 명시적으로 주면 추가로 제한
            tvg = np.minimum(tvg, 10 ** (tvg_cap_db / INTENSITY_DB))
        recv = recv * tvg                                      # TVG(거리보정)
    disp_db = INTENSITY_DB * np.log10(recv + 1e-30)            # dB (강도 규약)
    return disp_db


# ---------------------------------------------------------------------------
# 4) SSS 해상도 (ai4shipwrecks §3.4) 및 이미지 강도 정규화 (§3.6)
# ---------------------------------------------------------------------------
def resolution_xy(range_max_m, horiz_beam_deg, sound_speed_ms, bandwidth_hz):
    """논문 §3.4 픽셀 해상도.
      R_x (along-track/세로) = R * sin(theta_h)     (Eq.1, 최대 range에서)
      R_y (across-track/가로) = c / (2 * BW)          (Eq.2, BW=CHIRP 대역폭)
    반환 (R_x, R_y) [m]."""
    Rx = range_max_m * np.sin(np.deg2rad(horiz_beam_deg))
    Ry = sound_speed_ms / (2.0 * bandwidth_hz)
    return Rx, Ry


def normalize_intensity(img, mu=None, sd=None):
    """논문 §3.6 의 표준화. **학습 전처리**이지 표시 매핑이 아니다.

    논문 원문: "a normalization around 0 based on the mean and standard deviation of
    the pixel values of **the entire dataset**". 즉 데이터셋 하나의 통계를 모든
    이미지에 똑같이 적용한다. 이미지마다 자기 통계로 표준화하면 장면이 무엇이든
    평균이 같은 회색으로 강제되어, 밝은 해저와 어두운 해저의 구분 자체가 사라진다
    (2026-07-31: 우리 이미지가 항상 평균 130 회색이던 직접 원인).

    mu/sd 를 주면 그 데이터셋 통계를 쓰고, 안 주면 이미지 자기 통계로 폴백한다.
    표시용 8비트 매핑은 display_window_db() 의 고정 물리 창을 쓴다.
    """
    img = np.asarray(img, dtype=float)
    if mu is None:
        mu = img.mean()
    if sd is None:
        sd = img.std()
    return (img - mu) / (sd + 1e-9)


def display_window_db(range_min, range_max, alpha_db_m=None, rated_range_m=None,
                      apply_tvg=True, reference_range_m=None,
                      noise_quantile=DISPLAY_NOISE_BLACK_QUANTILE):
    """8비트 표시 창 [vmin, vmax] (dB). 이미지 통계가 아니라 물리 레벨에 앵커링한다.

    TVG on:
      기존 물리 창을 유지한다. 최대거리에서 TVG로 증폭된 noise floor를 하한으로,
      완전반사 0 dB를 상한으로 쓴다.

    TVG off:
      vmin = 빈 수신 bin Exponential(Pn)의 지정 분위수. 이 이하를 검정으로 잘라
             실제 8-bit SSS처럼 water column/nadir 잡음을 검게 표시한다.
      vmax = 기준 모래 해저가 reference_range_m에서 만드는 수신 power. 거리손실을
             받는 TVG-off 데이터에 0 dB 상한을 쓰면 영상 전체가 검어지므로, 실제로
             관측 가능한 기준 해저를 흰색 앵커로 삼는다.

    이 함수는 PNG 표시만 정한다. 복소 raw와 dB 배열에는 clipping을 적용하지 않는다.

    둘 다 씬이 아니라 (센서 제원 + 취득 기하)로만 정해지므로, 같은 설정으로 찍은
    이미지끼리 밝기를 그대로 비교할 수 있다. 이전 방식(이미지 평균 ± 2σ)은 장면이
    무엇이든 평균을 중간 회색으로 밀어붙여 이 비교가 불가능했다.
    """
    if alpha_db_m is None:
        alpha_db_m = francois_garrison_alpha(SSS_FREQ_KHZ)
    rated = rated_range_m if rated_range_m else RATED_MAX_RANGE_M
    nf = noise_floor_from_rated_range(range_min, alpha_db_m, rated)
    if not apply_tvg:
        # Empty detected-power samples are Exponential(mean=nf). Reuse the exact
        # inverse-CDF threshold used by empty-bin detection; no scene statistics.
        noise_black = empty_bin_threshold(nf, quantile=noise_quantile)
        vmin = INTENSITY_DB * np.log10(max(noise_black, 1e-30))

        r_ref = (float(reference_range_m) if reference_range_m is not None
                 else min(float(range_max), rated))
        r_ref = max(float(range_min), r_ref)
        TL_ref = 20 * np.log10(r_ref / range_min) + alpha_db_m * r_ref
        ref_db = INTENSITY_DB * np.log10(reference_backscatter())
        vmax = ref_db - 2.0 * TL_ref
        # At an operating point beyond the rated SNR there may be no useful display
        # span. Keep conversion numerically valid without changing stored data.
        vmax = max(float(vmax), float(vmin) + 1.0)
        return float(vmin), float(vmax)

    # 노이즈를 재는 거리는 **정격 최대거리까지만** 본다.
    # range_max 가 정격을 넘으면 그 지점의 노이즈가 정반사 상한(0 dB)보다 위로
    # 올라가 vmin > vmax 가 된다(실측 2026-08-03: range_max 343 m -> 창 +46.4~0 dB,
    # 폭이 음수라 이미지가 통째로 클리핑됐다). 정격 밖은 센서가 동작을 보장하지
    # 않는 구간이라 거기서 표시 창을 정하는 것 자체가 의미가 없다.
    r_eval = min(float(range_max), rated)
    TL = 20 * np.log10(r_eval / range_min) + alpha_db_m * r_eval
    vmin = INTENSITY_DB * np.log10(nf) + 2 * TL       # TVG 증폭 후 노이즈 [dB]
    vmax = float(SPECULAR_CEILING_DB)

    # 창이 기준 해저의 스페클 1% 레벨까지는 내려가야 한다. 그렇지 않으면 해저
    # 텍스처가 통째로 검게 잘린다. 정격 근처에서는 노이즈 == 기준 해저 이므로
    # (노이즈 바닥을 그렇게 정의했다) 위 vmin 이 곧 해저 레벨이 되어, 해저가
    # 창의 맨 바닥에 놓여 새까맣게 나왔다(2026-08-04 실측: range_max 343 m ->
    # 창 -18.7~0 dB, 진짜 해저 -24.8 dB 가 창 밖).
    ref_db = INTENSITY_DB * np.log10(reference_backscatter())
    vmin = min(vmin, ref_db + SPECKLE_P1_DB)
    return float(vmin), vmax


# ---------------------------------------------------------------------------
# 미모델 효과에 대한 방침 (사용자 지시)
#   - 빔 지향성  : 실제 소나 방정식의 양방향 빔패턴 항. 레이의 '실제' 각도로 계산해야
#                  물리적이다 -> 시뮬 C++(RaycastSidescanSonar) 안에서 레이별로 구현함.
#                  (거리에서 각도를 역추정하는 후처리는 근거가 약해 제거)
#   - 부피 산란  : 퇴적물 침투/지중 산란은 sediment volume 모델이 있어야 충실히 모사 가능.
#                  임의로 배경을 더하는 것은 근거 없는 짓이라 구현하지 않음.
#   - Multipath  : 제대로 하려면 수면반사 레이를 실제로 쏴야 함. 우리 마운트(수면 0.5m 아래)
#                  에선 경로차가 ~1m로 기하적으로 무의미 -> 구현하지 않음.
# 여기(후처리)엔 거리 1D 물리(흡수/TVG)만 둔다. 그건 최종 1D 출력만으로 정확히 계산됨.
# ---------------------------------------------------------------------------


def beam_reach_m(altitude_m, depression_deg, vertical_beam_deg):
    """이 기하에서 빔이 해저에 닿는 최원거리 slant range [m]. 닿는 한계가 없으면 inf.

    수직 부채꼴의 위쪽 가장자리(수평에 가장 가까운 레이)의 복각이
        lo = depression - vertical_beam/2
    이고, 그 레이가 해저를 만나는 slant range 가 altitude / sin(lo) 이다.
    lo <= 0 이면 그 레이는 수평 이상이라 해저에 닿지 않는다(사실상 무한).

    이 거리 밖의 range bin 에는 어떤 ping 에서도 반사가 없다. 실제 취득에서도
    유효거리 밖은 비어 있으므로, 기하를 제약하는 대신 워터폴을 여기까지만 남긴다.
    (센서에 가산 노이즈가 있어 '값이 0인지'로는 판정할 수 없다. 시뮬레이터에서는
     고도·복각·빔폭을 정확히 알고 있으므로 기하로 계산하는 편이 확실하다.)
    """
    lo = float(depression_deg) - float(vertical_beam_deg) / 2.0
    if lo <= 0.05:
        return float("inf")
    return float(altitude_m) / math.sin(math.radians(lo))


def empty_bin_threshold(noise_power, quantile=0.99, **_legacy):
    """반사가 없는 bin 값의 상위 분위수. 이 값을 넘으면 '신호가 있다'고 본다.

    complex-power-v2 에서 빈 수신 bin 은 |sqrt(Pn) W|^2 이므로 평균 Pn 의
    Exponential 분포다. 따라서 분위수는 역 CDF 로 정확히 계산할 수 있다.
    """
    pn = max(0.0, float(noise_power))
    q = float(np.clip(quantile, 0.0, 1.0 - 1e-12))
    return float(-pn * math.log1p(-q))


def effective_range_bins(port, stbd, noise_power, min_frac=0.10,
                         range_res_m=0.15, range_min=RANGE_MIN,
                         range_max=None, alpha_db_m=0.0, **_legacy):
    """신호가 노이즈와 구분되는 마지막 range bin (+1). 판정 불가면 전체 폭.

    각 bin 에서 '빈 bin 상위 1% 임계'를 넘는 ping 의 비율을 보고, 그 비율이
    min_frac 이상인 가장 먼 bin 을 유효거리로 본다.

    기하 계산(beam_reach_m)과 재는 대상이 다르다:
      기하   = 레이가 물리적으로 닿을 수 있는 한계 (하드 상한)
      데이터 = 신호가 노이즈에 묻히는 지점 (실질 상한, 보통 더 가깝다)
    원거리는 입사각이 스치듯 얕아 cos(입사각)이 작아지므로 신호가 먼저 사그라든다.
    희미하지만 실재하는 표적을 지우지 않으려면 기하 쪽이 안전하다.

    2026-07-31 수정: 예전엔 `frac >= min_frac` 인 **마지막** bin 을 그대로 썼다.
    임계가 노이즈 상위 1% 라 bin 하나하나는 1% 확률로 우연히 넘고, bin 이 수백
    개면 그런 우연이 끝자락에도 반드시 생긴다. 그래서 판정이 늘 전체 폭으로
    붙어 크롭이 사실상 동작하지 않았다. 이동평균으로 평활한 뒤 판정해 고립된
    우연을 없앤다(창 폭 = 1 m 상당 bin 수, 최소 5).
    """
    port = np.asarray(port, dtype=float)
    stbd = np.asarray(stbd, dtype=float)
    if range_max is None:
        range_max = range_min + range_res_m * port.shape[1]
    r = np.linspace(range_min, range_max, port.shape[1])
    tl = 20 * np.log10(np.maximum(r, range_min) / range_min) + alpha_db_m * r
    attenuation = 10 ** (-2 * tl / INTENSITY_DB)
    thr = empty_bin_threshold(noise_power)
    # Engine output contains signal power only. Compare its propagated receiver
    # power with the empty-bin receiver-noise threshold.
    frac = (((port * attenuation) > thr).mean(axis=0)
            + ((stbd * attenuation) > thr).mean(axis=0)) / 2.0
    w = max(5, int(round(1.0 / max(range_res_m, 1e-6))))
    if w > 1 and frac.size > w:
        k = np.ones(w) / w
        frac = np.convolve(frac, k, mode="same")
    hit = np.nonzero(frac >= min_frac)[0]
    return int(hit[-1]) + 1 if hit.size else port.shape[1]


def build_dual_waterfall(port_raw, stbd_raw, **kw):
    """좌/우 단측 waterfall -> [port(뒤집힘) | nadir | starboard] 이중측 이미지(dB)."""
    p = process_side(port_raw, **kw)
    s = process_side(stbd_raw, **kw)
    # 실제 SSS: 중앙=nadir(근거리), 바깥=원거리. 포트는 좌우반전해서 왼쪽에 붙임.
    return np.concatenate([p[:, ::-1], s], axis=1)


# ---------------------------------------------------------------------------
# 5) 워터폴 저장: 실제 SSS처럼 ping 1개 = 픽셀 1줄 raster (강제 리사이즈 없음).
#    - 취득순으로 아래에서 위로 쌓음(먼저 취득한 ping = 최하단).
#    - 순수 워터폴 이미지만 저장(제목/헤더 없음). 설정정보는 파일명에 담는다.
#    - raw(1:1)와 trueaspect(정사각 미터 픽셀) 두 장 저장.
# ---------------------------------------------------------------------------
def _to_uint8(img, vmin, vmax):
    x = np.clip((np.asarray(img, float) - vmin) / (vmax - vmin + 1e-9), 0, 1)
    return (x * 255 + 0.5).astype(np.uint8)


def _resample_rows(u8, factor):
    """행(along-track)을 factor배로 선형 리샘플."""
    n = u8.shape[0]
    m = max(1, int(round(n * factor)))
    src = np.linspace(0, n - 1, m)
    i0 = np.floor(src).astype(int)
    i1 = np.minimum(i0 + 1, n - 1)
    w = (src - i0)[:, None]
    return (u8[i0] * (1 - w) + u8[i1] * w + 0.5).astype(np.uint8)


def fill_label_holes(mask, close_radius=5):
    """GT 라벨의 '난파선 내부'를 정답으로 채운다. 입출력 0/1 uint8.

    소나 레이는 갑판 격자·구조물 틈으로 빠져나가 해저를 맞기도 한다. 그 픽셀은 난파선에
    닿지 않았으므로 strict 라벨에선 배경이지만, 사람이 보기엔 선체 윤곽 안쪽이다.
      1) 모폴로지 닫힘(반경 close_radius)으로 끊긴 윤곽을 잇고
      2) 바깥에서 배경을 flood fill 해 닿지 못한 0 픽셀 = 둘러싸인 내부로 채운다.
    기본 반경 5 px 는 베이스라인 평가(1728 폭 리샘플 기준 8 px)에서 IoU 가 정점을 찍은
    설정과 동일하다(1728/원본 배율 환산).
    """
    import cv2
    mask = (np.asarray(mask) > 0).astype(np.uint8)
    if close_radius > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                      (2 * close_radius + 1, 2 * close_radius + 1))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
    h, w = mask.shape
    pad = np.zeros((h + 2, w + 2), np.uint8)
    pad[1:-1, 1:-1] = mask
    ff = pad.copy()
    cv2.floodFill(ff, np.zeros((h + 4, w + 4), np.uint8), (0, 0), 1)
    holes = ((ff == 0) & (pad == 0)).astype(np.uint8)
    return (pad | holes)[1:-1, 1:-1]


def mask_to_boxes(mask, min_area_px=1, padding_px=0, merge_gap_px=0,
                  fragment_max_area_px=0):
    """Binary segmentation mask -> object-level detection boxes.

    Returns pixel boxes in half-open image coordinates: ``x1,y1,x2,y2,area_px``.
    The mask is expected to have the same orientation as the saved image.  Keeping
    this conversion client-side guarantees that segmentation and detection GT use
    exactly the same sonar geometry and crop rather than separately projecting the
    3-D object bounds (which would not match acoustic shadow/visibility).

    A wreck can be hit in several disconnected pieces: its open hull/railings let
    some rays through, and a separate small structure can sit a few pixels away.
    ``merge_gap_px`` therefore joins a *small* component to a nearby component
    before one YOLO box is emitted.  ``fragment_max_area_px`` makes that rule
    deliberately asymmetric, so two independently visible, substantial wrecks
    are never merged merely because they happen to be close in the waterfall.
    Area filtering occurs after this grouping, preserving a small genuine part
    when it belongs to a larger hull while suppressing isolated 1--15 px debris.
    """
    import cv2
    m = (np.asarray(mask) > 0).astype(np.uint8)
    h, w = m.shape
    n, _labels, stats, _centroids = cv2.connectedComponentsWithStats(m, 8)
    components = []
    for i in range(1, n):
        x, y, bw, bh, area = [int(v) for v in stats[i]]
        components.append({"x1": x, "y1": y, "x2": x + bw, "y2": y + bh,
                           "area_px": area})

    # Union/find keeps the grouping deterministic even if one small fragment
    # bridges two pieces of the same hull.
    parent = list(range(len(components)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        i, j = find(i), find(j)
        if i != j:
            parent[j] = i

    gap_limit = max(0, int(merge_gap_px))
    fragment_limit = max(0, int(fragment_max_area_px))
    if gap_limit > 0 and fragment_limit > 0:
        for i, a in enumerate(components):
            for j in range(i):
                b = components[j]
                # Only a fragment may initiate the merge.  This prevents two
                # normal-sized neighbouring targets from becoming one label.
                if min(a["area_px"], b["area_px"]) > fragment_limit:
                    continue
                dx = max(0, a["x1"] - b["x2"], b["x1"] - a["x2"])
                dy = max(0, a["y1"] - b["y2"], b["y1"] - a["y2"])
                if max(dx, dy) <= gap_limit:
                    union(i, j)

    groups = {}
    for i, component in enumerate(components):
        groups.setdefault(find(i), []).append(component)

    out = []
    pad = max(0, int(padding_px))
    for group in groups.values():
        area = sum(c["area_px"] for c in group)
        if area < int(min_area_px):
            continue
        x1 = max(0, min(c["x1"] for c in group) - pad)
        y1 = max(0, min(c["y1"] for c in group) - pad)
        x2 = min(w, max(c["x2"] for c in group) + pad)
        y2 = min(h, max(c["y2"] for c in group) + pad)
        record = {"x1": x1, "y1": y1, "x2": x2, "y2": y2,
                  "area_px": area}
        if len(group) > 1:
            record["components"] = len(group)
        out.append(record)
    out.sort(key=lambda b: (b["y1"], b["x1"], b["y2"], b["x2"]))
    return out


def boxes_to_yolo(boxes, image_width, image_height, class_id=0):
    """Pixel boxes -> YOLO ``class x_center y_center width height`` lines."""
    w = float(image_width); h = float(image_height)
    if w <= 0 or h <= 0:
        raise ValueError("image dimensions must be positive")
    lines = []
    for b in boxes:
        bw = float(b["x2"] - b["x1"]); bh = float(b["y2"] - b["y1"])
        cx = (float(b["x1"]) + float(b["x2"])) / 2.0
        cy = (float(b["y1"]) + float(b["y2"])) / 2.0
        lines.append(f"{int(class_id)} {cx/w:.8f} {cy/h:.8f} {bw/w:.8f} {bh/h:.8f}")
    return lines


def save_waterfall(img, raw_path, ta_path, across_px_m, dx_m, vmin=None, vmax=None):
    """선택한 워터폴 raster를 저장한다. ``None`` 경로는 생성하지 않는다.

    ``raw_path``는 ping 1개가 픽셀 1줄인 기본 PNG이고, ``ta_path``는
    along-track 축을 거리 비율에 맞춰 리샘플한 PNG다. 반환값은 전달받은 두 경로다.
    """
    from PIL import Image
    # 표시 창: 이미지 자신의 평균 ± DISPLAY_SIGMA 표준편차 (고정 규칙).
    # normalize_intensity() 를 거친 입력이면 이미 mean=0/std=1 이므로 그대로 ±SIGMA.
    # 혹시 정규화를 끄고 원시 dB 를 바로 넣는 호출이면(현재 파이프라인엔 없음) 그때만
    # 이미지 자체 통계로 폴백한다 — 정규화 없이 고정 시그마를 쓰면 씬마다 절대 dB 가
    # 달라 무의미해지기 때문이다.
    is_normalized = abs(np.mean(img)) < 1e-3 and abs(np.std(img) - 1.0) < 1e-3
    if vmin is None:
        vmin = -DISPLAY_SIGMA if is_normalized else np.percentile(img, 1.0)
    if vmax is None:
        vmax = DISPLAY_SIGMA if is_normalized else np.percentile(img, 99.5)
    u8 = _to_uint8(img, vmin, vmax)
    u8 = np.flipud(u8)                          # 먼저 취득한 ping(row0) -> 최하단

    if raw_path is not None:
        raw_path = pathlib.Path(raw_path)
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(u8).save(str(raw_path))

    factor = dx_m / across_px_m                 # 세로를 이만큼 늘려 정사각 픽셀
    if ta_path is not None:
        ta_path = pathlib.Path(ta_path)
        ta_path.parent.mkdir(parents=True, exist_ok=True)
        u8_ta = _resample_rows(u8, factor)
        Image.fromarray(u8_ta).save(str(ta_path))
    return raw_path, ta_path


if __name__ == "__main__":
    a = francois_garrison_alpha(SSS_FREQ_KHZ)
    print(f"alpha(400kHz, 15C, 32ppt, 20m) = {a:.4f} dB/m ({a*1000:.1f} dB/km)")
    for fk in [100, 200, 400, 600, 900]:
        print(f"  {fk:4d} kHz -> {francois_garrison_alpha(fk):.4f} dB/m")

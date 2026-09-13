import math
import pathlib
import numpy as np





def francois_garrison_alpha(f_khz, T=15.0, S=32.0, D=20.0, pH=8.0):
    f = float(f_khz)
    c = 1412.0 + 3.21 * T + 1.19 * S + 0.0167 * D


    A1 = (8.86 / c) * 10 ** (0.78 * pH - 5)
    P1 = 1.0
    f1 = 2.8 * np.sqrt(S / 35.0) * 10 ** (4 - 1245.0 / (T + 273.0))


    A2 = 21.44 * (S / c) * (1 + 0.025 * T)
    P2 = 1 - 1.37e-4 * D + 6.2e-9 * D ** 2
    f2 = (8.17 * 10 ** (8 - 1990.0 / (T + 273.0))) / (1 + 0.0018 * (S - 35.0))


    if T <= 20:
        A3 = 4.937e-4 - 2.59e-5 * T + 9.11e-7 * T ** 2 - 1.50e-8 * T ** 3
    else:
        A3 = 3.964e-4 - 1.146e-5 * T + 1.45e-7 * T ** 2 - 6.5e-10 * T ** 3
    P3 = 1 - 3.83e-5 * D + 4.9e-10 * D ** 2

    alpha_db_km = (A1 * P1 * f1 * f ** 2 / (f1 ** 2 + f ** 2)
                   + A2 * P2 * f2 * f ** 2 / (f2 ** 2 + f ** 2)
                   + A3 * P3 * f ** 2)
    return alpha_db_km / 1000.0






SSS_FREQ_KHZ   = 540.0
RANGE_MIN      = 1.0
RANGE_MAX      = 40.0
RANGE_RES      = 0.05
ELEVATION_DEG  = 0.5
AZIMUTH_DEG    = 60.0
WATER_DENSITY  = 1024.0
WATER_SOUND    = 1500.0
















DISPLAY_SIGMA = 2.0
ADD_SIGMA      = 0.0
MULT_SIGMA     = 0.5


def make_sss_config(azimuth=AZIMUTH_DEG, elevation=ELEVATION_DEG,
                    range_min=RANGE_MIN, range_max=RANGE_MAX, range_res=RANGE_RES,
                    add_sigma=ADD_SIGMA, mult_sigma=MULT_SIGMA,
                    speckle_strength=1.0, speckle_seed=0,
                    signal_model="coherent", frequency_khz=SSS_FREQ_KHZ,
                    scatter_correlation_length_m=0.05,
                    texture_shape=0.0, texture_correlation_length_m=1.0):
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













INTENSITY_DB = 10.0



SPECULAR_CEILING_DB = 0.0





RATED_MAX_RANGE_M = 150.0






SPECKLE_P1_DB = -19.98





DISPLAY_NOISE_BLACK_QUANTILE = 0.99


def reference_backscatter(cos_inc=0.7071):
    z_sed = 1298.0 * 1564.0
    z_w = WATER_DENSITY * WATER_SOUND
    R = (z_sed - z_w) / (z_sed + z_w)
    return R * R * cos_inc


def noise_floor_from_rated_range(range_min, alpha_db_m,
                                 rated_range_m=RATED_MAX_RANGE_M, ref_bs=None):
    if ref_bs is None:
        ref_bs = reference_backscatter()
    TL = 20 * np.log10(rated_range_m / range_min) + alpha_db_m * rated_range_m
    return float(ref_bs * 10 ** (-2 * TL / INTENSITY_DB))


def add_complex_receiver_noise(signal_power, noise_power, rng):
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
    raw = np.asarray(raw, dtype=float)
    raw = np.clip(raw, 0, None)
    nbins = raw.shape[1]
    if alpha_db_m is None:
        alpha_db_m = francois_garrison_alpha(SSS_FREQ_KHZ)

    r = np.linspace(range_min, range_max, nbins)
    r = np.maximum(r, range_min)
    TL = 20 * np.log10(r / range_min) + alpha_db_m * r
    two_way = 2 * TL
    if noise_floor is None:
        noise_floor = noise_floor_from_rated_range(range_min, alpha_db_m)

    rng = np.random.default_rng(None if seed is None else seed)

    recv_signal = raw * 10 ** (-two_way / INTENSITY_DB)
    recv = add_complex_receiver_noise(recv_signal, noise_floor, rng)
    if apply_tvg:















        r_tvg = np.minimum(r, RATED_MAX_RANGE_M)
        TL_tvg = 20 * np.log10(r_tvg / range_min) + alpha_db_m * r_tvg
        tvg = 10 ** (2 * TL_tvg / INTENSITY_DB)
        if tvg_cap_db is not None:
            tvg = np.minimum(tvg, 10 ** (tvg_cap_db / INTENSITY_DB))
        recv = recv * tvg
    disp_db = INTENSITY_DB * np.log10(recv + 1e-30)
    return disp_db





def resolution_xy(range_max_m, horiz_beam_deg, sound_speed_ms, bandwidth_hz):
    Rx = range_max_m * np.sin(np.deg2rad(horiz_beam_deg))
    Ry = sound_speed_ms / (2.0 * bandwidth_hz)
    return Rx, Ry


def normalize_intensity(img, mu=None, sd=None):
    img = np.asarray(img, dtype=float)
    if mu is None:
        mu = img.mean()
    if sd is None:
        sd = img.std()
    return (img - mu) / (sd + 1e-9)


def display_window_db(range_min, range_max, alpha_db_m=None, rated_range_m=None,
                      apply_tvg=True, reference_range_m=None,
                      noise_quantile=DISPLAY_NOISE_BLACK_QUANTILE):
    if alpha_db_m is None:
        alpha_db_m = francois_garrison_alpha(SSS_FREQ_KHZ)
    rated = rated_range_m if rated_range_m else RATED_MAX_RANGE_M
    nf = noise_floor_from_rated_range(range_min, alpha_db_m, rated)
    if not apply_tvg:


        noise_black = empty_bin_threshold(nf, quantile=noise_quantile)
        vmin = INTENSITY_DB * np.log10(max(noise_black, 1e-30))

        r_ref = (float(reference_range_m) if reference_range_m is not None
                 else min(float(range_max), rated))
        r_ref = max(float(range_min), r_ref)
        TL_ref = 20 * np.log10(r_ref / range_min) + alpha_db_m * r_ref
        ref_db = INTENSITY_DB * np.log10(reference_backscatter())
        vmax = ref_db - 2.0 * TL_ref


        vmax = max(float(vmax), float(vmin) + 1.0)
        return float(vmin), float(vmax)






    r_eval = min(float(range_max), rated)
    TL = 20 * np.log10(r_eval / range_min) + alpha_db_m * r_eval
    vmin = INTENSITY_DB * np.log10(nf) + 2 * TL
    vmax = float(SPECULAR_CEILING_DB)






    ref_db = INTENSITY_DB * np.log10(reference_backscatter())
    vmin = min(vmin, ref_db + SPECKLE_P1_DB)
    return float(vmin), vmax















def beam_reach_m(altitude_m, depression_deg, vertical_beam_deg):
    lo = float(depression_deg) - float(vertical_beam_deg) / 2.0
    if lo <= 0.05:
        return float("inf")
    return float(altitude_m) / math.sin(math.radians(lo))


def empty_bin_threshold(noise_power, quantile=0.99, **_legacy):
    pn = max(0.0, float(noise_power))
    q = float(np.clip(quantile, 0.0, 1.0 - 1e-12))
    return float(-pn * math.log1p(-q))


def effective_range_bins(port, stbd, noise_power, min_frac=0.10,
                         range_res_m=0.15, range_min=RANGE_MIN,
                         range_max=None, alpha_db_m=0.0, **_legacy):
    port = np.asarray(port, dtype=float)
    stbd = np.asarray(stbd, dtype=float)
    if range_max is None:
        range_max = range_min + range_res_m * port.shape[1]
    r = np.linspace(range_min, range_max, port.shape[1])
    tl = 20 * np.log10(np.maximum(r, range_min) / range_min) + alpha_db_m * r
    attenuation = 10 ** (-2 * tl / INTENSITY_DB)
    thr = empty_bin_threshold(noise_power)


    frac = (((port * attenuation) > thr).mean(axis=0)
            + ((stbd * attenuation) > thr).mean(axis=0)) / 2.0
    w = max(5, int(round(1.0 / max(range_res_m, 1e-6))))
    if w > 1 and frac.size > w:
        k = np.ones(w) / w
        frac = np.convolve(frac, k, mode="same")
    hit = np.nonzero(frac >= min_frac)[0]
    return int(hit[-1]) + 1 if hit.size else port.shape[1]


def build_dual_waterfall(port_raw, stbd_raw, **kw):
    p = process_side(port_raw, **kw)
    s = process_side(stbd_raw, **kw)

    return np.concatenate([p[:, ::-1], s], axis=1)








def _to_uint8(img, vmin, vmax):
    x = np.clip((np.asarray(img, float) - vmin) / (vmax - vmin + 1e-9), 0, 1)
    return (x * 255 + 0.5).astype(np.uint8)


def _resample_rows(u8, factor):
    n = u8.shape[0]
    m = max(1, int(round(n * factor)))
    src = np.linspace(0, n - 1, m)
    i0 = np.floor(src).astype(int)
    i1 = np.minimum(i0 + 1, n - 1)
    w = (src - i0)[:, None]
    return (u8[i0] * (1 - w) + u8[i1] * w + 0.5).astype(np.uint8)


def fill_label_holes(mask, close_radius=5):
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
    import cv2
    m = (np.asarray(mask) > 0).astype(np.uint8)
    h, w = m.shape
    n, _labels, stats, _centroids = cv2.connectedComponentsWithStats(m, 8)
    components = []
    for i in range(1, n):
        x, y, bw, bh, area = [int(v) for v in stats[i]]
        components.append({"x1": x, "y1": y, "x2": x + bw, "y2": y + bh,
                           "area_px": area})



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
    from PIL import Image





    is_normalized = abs(np.mean(img)) < 1e-3 and abs(np.std(img) - 1.0) < 1e-3
    if vmin is None:
        vmin = -DISPLAY_SIGMA if is_normalized else np.percentile(img, 1.0)
    if vmax is None:
        vmax = DISPLAY_SIGMA if is_normalized else np.percentile(img, 99.5)
    u8 = _to_uint8(img, vmin, vmax)
    u8 = np.flipud(u8)

    if raw_path is not None:
        raw_path = pathlib.Path(raw_path)
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(u8).save(str(raw_path))

    factor = dx_m / across_px_m
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

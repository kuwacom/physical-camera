"""Load lens prescriptions from TOML files."""

import math
import tomllib
from pathlib import Path

MAX_SURFACES = 36
VALID_SURFACE_TYPES = ("spherical", "flat", "stop", "aspheric", "cylindrical_x", "cylindrical_y")
VALID_COATINGS = ("none", "single", "multi")


def _number(table: dict, key: str, where: str, minimum: float | None = None, strict: bool = False) -> float:
    """Return table[key] as a number, raising ValueError when missing, non-numeric or out of range."""
    value = table.get(key)
    # bool は int のサブクラスなので明示的に弾かないと true が 1 として通ってしまう
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{where}: {key} must be a number, got {value!r}")
    if minimum is not None and (value <= minimum if strict else value < minimum):
        relation = ">" if strict else ">="
        raise ValueError(f"{where}: {key} {value!r} must be {relation} {minimum}")
    return value


def _resolve_surface_types(surfaces: list[dict], filename: str) -> list[str]:
    """Resolve the type for each surface from explicit or inferred values."""
    types = []
    for i, s in enumerate(surfaces):
        explicit = s.get("type")
        if explicit is not None:
            if explicit not in VALID_SURFACE_TYPES:
                raise ValueError(f"{filename}: surface {i} type {explicit!r} must be one of {VALID_SURFACE_TYPES}")
            types.append(explicit)
        elif s["radius"] == 0:
            types.append("flat")
        else:
            types.append("spherical")
    return types


def _parse_focus(data: dict, surfaces: list[dict], filename: str) -> dict | None:
    """Parse optional [focus] section from lens TOML data."""
    focus_raw = data.get("focus")
    if focus_raw is None:
        return None

    if not isinstance(focus_raw, dict):
        raise ValueError(f"{filename}: [focus] must be a table")
    close_distance = _number(focus_raw, "close_distance", f"{filename}: focus", minimum=0, strict=True)
    variables_raw = focus_raw.get("variable", [])
    if not isinstance(variables_raw, list) or not variables_raw:
        raise ValueError(f"{filename}: [focus] section has no [[focus.variable]] entries")

    variables = []
    seen_surfaces = set()
    for v in variables_raw:
        idx = v.get("surface") if isinstance(v, dict) else None
        if isinstance(idx, bool) or not isinstance(idx, int):
            raise ValueError(f"{filename}: focus.variable surface must be an integer index, got {idx!r}")
        if idx in seen_surfaces:
            raise ValueError(f"{filename}: focus.variable surface {idx} listed more than once")
        if idx < 0 or idx >= len(surfaces):
            raise ValueError(f"{filename}: focus.variable surface {idx} out of range (0..{len(surfaces) - 1})")
        if surfaces[idx]["ior"] != 1.0:
            raise ValueError(
                f"{filename}: focus.variable surface {idx} has ior {surfaces[idx]['ior']} (expected 1.0 for air gap)"
            )
        thickness_close = _number(v, "thickness_close", f"{filename}: focus.variable surface {idx}", minimum=0)
        seen_surfaces.add(idx)
        variables.append({"surface": idx, "thickness_close": thickness_close})

    return {"close_distance": close_distance, "variables": variables}


def _paraxial_marginal_ray(
    surfaces: list[dict], surface_types: list[str], axis: str = "y"
) -> tuple[float, float, float, float]:
    """Trace a ray parallel to the axis (height 1, nu=0) front to back at infinity spacing.

    Returns (y, nu) after the last surface plus the ray height and aperture at the stop.
    """
    # 合焦軸 (Y) に合わせ、Y にパワーを持たない cylindrical_x は平面として扱う
    # X 軸で追跡するときは逆に cylindrical_y が平面になる
    flat_cylinder = "cylindrical_x" if axis == "y" else "cylindrical_y"
    y, nu, n = 1.0, 0.0, 1.0
    stop_height = 0.0
    stop_aperture = 0.0
    last = len(surfaces) - 1
    for i, (s, st) in enumerate(zip(surfaces, surface_types, strict=True)):
        if st == "stop":
            stop_height = y
            stop_aperture = s["aperture"]
        n_after = s["ior"]
        if st not in ("flat", "stop", flat_cylinder):
            nu -= y * (n_after - n) / s["radius"]
        n = n_after
        if i < last:
            y += s["thickness"] * nu / n
    return y, nu, stop_height, stop_aperture


def paraxial_fstop(surfaces: list[dict], surface_types: list[str]) -> float:
    """Open-aperture f-number from a Y-axis paraxial trace (infinity focus).

    f-number = EFL / entrance pupil diameter, where the entrance pupil is
    the stop as imaged by the front group.
    """
    # 入射瞳は絞りを前群越しに見た像で、絞り径そのものとは一致しないため近軸追跡で求める
    _, nu, stop_height, stop_aperture = _paraxial_marginal_ray(surfaces, surface_types)
    if nu >= 0.0 or stop_height == 0.0:
        raise ValueError("lens has no positive focal length or the marginal ray misses the stop")
    efl = -1.0 / nu
    entrance_pupil = stop_aperture / abs(stop_height)
    return efl / entrance_pupil


def paraxial_back_focal_distance(surfaces: list[dict], surface_types: list[str]) -> float:
    """Distance from the rear vertex to the infinity-focus image plane on the Y axis (mm).

    Matches the shader's compute_sensor_distance() at infinity spacing (-A/C).
    """
    y, nu, _, _ = _paraxial_marginal_ray(surfaces, surface_types)
    if nu == 0.0:
        raise ValueError("lens has no optical power")
    return -y / nu


def paraxial_entrance_pupil_magnification(surfaces: list[dict], surface_types: list[str], axis: str = "y") -> float:
    """Entrance pupil diameter / stop diameter along one axis (infinity focus)."""
    # 被写体側の回折角は入射瞳径で決まるため、絞り径に掛ける倍率を求める
    # アナモルフィックは前群の円柱面で X と Y の入射瞳径が異なるので軸ごとに追跡する
    _, _, stop_height, _ = _paraxial_marginal_ray(surfaces, surface_types, axis)
    if stop_height == 0.0:
        raise ValueError("the marginal ray misses the stop")
    return 1.0 / abs(stop_height)


def min_fstop(lens: dict) -> float:
    """Smallest f-number the lens data can actually reach (UI lower bound)."""
    # データ上の絞りが表記より小さいレンズは表記の f 値まで開けないため、実際に開ける値を下限にする
    return max(lens["max_fstop"], lens["open_fstop"])


def aperture_scale(lens: dict, fstop: float) -> float:
    """Stop diameter multiplier that makes the effective f-number equal fstop."""
    # 絞り径は入射瞳径と比例するため、データ上の開放 f 値 (近軸) を基準にすれば UI の f 値と実効 f 値が一致する
    # max_fstop 基準だと絞り径と表記 f 値がずれたレンズで明るさ・ボケ量が狂う
    return min(lens["open_fstop"] / fstop, 1.0)


def cauchy_ior(n_d: float, v_d: float, wavelength_nm: float) -> float:
    """Wavelength-dependent IOR, identical to the shader's cauchy_ior()."""
    if v_d < 1.0 or n_d <= 1.0:
        return n_d
    inv_f2 = 1.0 / (486.1 * 486.1)
    inv_c2 = 1.0 / (656.3 * 656.3)
    inv_d2 = 1.0 / (587.6 * 587.6)
    b = (n_d - 1.0) / (v_d * (inv_f2 - inv_c2))
    return n_d - b * inv_d2 + b / (wavelength_nm * wavelength_nm)


def focus_thicknesses(lens: dict, alpha: float) -> list[float]:
    """Surface thicknesses at the shader's focus interpolation factor alpha (0 = infinity, 1 = close)."""
    close = {v["surface"]: v["thickness_close"] for v in lens["focus"]["variables"]} if lens["focus"] else {}
    return [
        s["thickness"] + alpha * (close.get(i, s["thickness"]) - s["thickness"]) for i, s in enumerate(lens["surfaces"])
    ]


def paraxial_sensor_distance(lens: dict, thicknesses: list[float], focus_mm: float | None) -> float:
    """Y-axis paraxial sensor distance from the rear vertex, same as the shader's compute_sensor_distance()."""
    surfaces, types = lens["surfaces"], lens["surface_types"]
    a, b, c, d = 1.0, 0.0, 0.0, 1.0
    n = 1.0
    last = len(surfaces) - 1
    for i, (s, st) in enumerate(zip(surfaces, types, strict=True)):
        n_after = s["ior"]
        if st not in ("flat", "stop", "cylindrical_x") and n_after != n:
            phi = (n_after - n) / s["radius"]
            c -= phi * a
            d -= phi * b
        n = n_after
        if i < last:
            t = thicknesses[i] / n
            a += t * c
            b += t * d
    f = -1.0 / c
    if focus_mm is None:
        return -a / c
    total_length = sum(thicknesses[:-1])
    d_o = max(focus_mm - total_length + (d - 1.0) / c, 2.0 * f)
    return (1.0 - a) / c + f * d_o / (d_o - f)


# ずれはピント距離に対して曲がって変化し (Navarro は途中で符号が変わる)、無限遠と最短の 2 点の直線補間では
# 中間距離で大きく外れるため、逆数距離の α を等分した点で求めて区分線形に補間する
FOCUS_OFFSET_STEPS = 4
# 色収差オンのシェーダーは 400-700nm を一様に使うため、その範囲を等分した代表波長で平均する
_FOCUS_WAVELENGTHS = (450.0, 550.0, 650.0)
# 等面積の輪帯数。周辺ほど球面収差が大きく最良ピントを左右するので、輪帯で開口全体を均等に代表させる
# 輪帯が粗いと各光線の寄与が z 方向に離散的な山になり最良位置が飛ぶため、山が重なる程度に細かくする
_FOCUS_RINGS = 16
# 円柱面のあるレンズで追跡する 1 象限あたりの角度数。軸上光束は X / Y 対称なので 1 象限で全周を代表できる
_FOCUS_ANGLES = 4
# 鮮鋭度の評価半径 (mm)。フルサイズ FHD の画素ピッチ (36 / 1920 ≈ 0.019) に Cycles の画素フィルター (1.5px) の広がりを足した程度
# 同じ画素ピッチのレンダリングでピント位置を振ったときの最良位置 (Neil 3m / Double Gauss 50 0.6m) に最も近かった値
# RMS スポット径は少数の周辺光線 (ハロ) に引っ張られ、芯の鋭さで決まる見た目のピントからずれる
# (トリプレットや軟焦点で近軸より悪化した) ため、この半径付近に集まる光の量で評価する
_FOCUS_BLUR_RADIUS = 0.025


def _intersect(st: str, s: dict, vz: float, p: tuple, d: tuple) -> tuple | None:
    """Hit point and unnormalized normal, picking the vertex-side root like the shader's intersect_curved()."""
    if st in ("flat", "stop"):
        t = (vz - p[2]) / d[2]
        return (p[0] + t * d[0], p[1] + t * d[1], vz), (0.0, 0.0, -1.0)
    r = s["radius"]
    mx = 0.0 if st == "cylindrical_y" else 1.0
    my = 0.0 if st == "cylindrical_x" else 1.0
    cz = vz + r
    ox, oy, oz = p[0] * mx, p[1] * my, p[2] - cz
    dx, dy, dz = d[0] * mx, d[1] * my, d[2]
    qa = dx * dx + dy * dy + dz * dz
    qb = 2.0 * (ox * dx + oy * dy + oz * dz)
    qc = ox * ox + oy * oy + oz * oz - r * r
    disc = qb * qb - 4.0 * qa * qc
    if disc < 0.0:
        return None
    sq = disc**0.5
    t1, t2 = (-qb - sq) / (2.0 * qa), (-qb + sq) / (2.0 * qa)
    t = t1 if abs(p[2] + t1 * d[2] - vz) < abs(p[2] + t2 * d[2] - vz) else t2
    if st == "aspheric":
        k = s.get("conic", 0.0)
        coeffs = [*s["aspheric_coeffs"], 0.0][:4]
        cv = 1.0 / r
        for _ in range(11):
            hx, hy, hz = p[0] + t * d[0], p[1] + t * d[1], p[2] + t * d[2]
            h2 = hx * hx + hy * hy
            q = max(1.0 - (1.0 + k) * cv * cv * h2, 1e-20) ** 0.5
            # sag'(h) / h で持つと h = 0 でも割らずに法線と Newton の微分が求まる
            sp_h = cv / q + sum(2.0 * (j + 2) * a * h2 ** (j + 1) for j, a in enumerate(coeffs))
            f = hz - vz - cv * h2 / (1.0 + q) - sum(a * h2 ** (j + 2) for j, a in enumerate(coeffs))
            if abs(f) < 1e-9:
                break
            t -= f / (d[2] - sp_h * (hx * d[0] + hy * d[1]))
        return (hx, hy, hz), (-sp_h * hx, -sp_h * hy, 1.0)
    hit = (p[0] + t * d[0], p[1] + t * d[1], p[2] + t * d[2])
    return hit, (hit[0] * mx, hit[1] * my, hit[2] - cz)


def _trace_real(
    lens: dict, vertex_z: list[float], iors: list[float], p: tuple, d: tuple, end: int
) -> tuple[tuple, tuple] | None:
    """Trace a ray front to back through surfaces [0, end) with clipping; None when clipped or TIR."""
    surfaces, types = lens["surfaces"], lens["surface_types"]
    n = 1.0
    for i in range(end):
        st, s = types[i], surfaces[i]
        hit = _intersect(st, s, vertex_z[i], p, d)
        if hit is None:
            return None
        p, nrm = hit
        semi = s["aperture"] * 0.5
        if p[0] * p[0] + p[1] * p[1] > semi * semi:
            return None
        n_after = iors[i]
        if st != "stop" and n_after != n:
            nl = (nrm[0] ** 2 + nrm[1] ** 2 + nrm[2] ** 2) ** 0.5
            nx, ny, nz = nrm[0] / nl, nrm[1] / nl, nrm[2] / nl
            cos_i = -(d[0] * nx + d[1] * ny + d[2] * nz)
            if cos_i < 0.0:
                nx, ny, nz, cos_i = -nx, -ny, -nz, -cos_i
            eta = n / n_after
            k = 1.0 - eta * eta * (1.0 - cos_i * cos_i)
            if k < 0.0:
                return None
            g = eta * cos_i - k**0.5
            d = (eta * d[0] + g * nx, eta * d[1] + g * ny, eta * d[2] + g * nz)
        n = n_after
    return p, d


def _stop_ray_scale(lens: dict, thicknesses: list[float], focus_mm: float | None, axis: str) -> float:
    """Paraxial launch parameter (height at infinity, slope otherwise) that reaches the stop rim on one axis."""
    surfaces, types = lens["surfaces"], lens["surface_types"]
    flat_cylinder = "cylindrical_x" if axis == "y" else "cylindrical_y"
    if focus_mm is None:
        y, nu = 1.0, 0.0
    else:
        y, nu = focus_mm - sum(thicknesses[:-1]), 1.0
    n = 1.0
    for i, (s, st) in enumerate(zip(surfaces, types, strict=True)):
        if st == "stop":
            return s["aperture"] * 0.5 / abs(y)
        n_after = s["ior"]
        if st not in ("flat", "stop", flat_cylinder):
            nu -= y * (n_after - n) / s["radius"]
        n = n_after
        y += thicknesses[i] * nu / n
    raise ValueError("lens has no stop")


def on_axis_rays(
    lens: dict, focus_mm: float | None, aperture_scale: float = 1.0
) -> tuple[float, list[tuple[float, float, float, float]]]:
    """Trace the on-axis beam through the real lens for the shader's focus state.

    focus_mm follows the shader: object distance from the rear vertex (None =
    infinity), with the same reciprocal-distance thickness interpolation.
    Returns the paraxial Y-axis sensor distance and, per ray and wavelength,
    (x0, sx, y0, sy) with x(z) = x0 + z*sx in lens space (rear vertex at
    z = 0). Rays sample the stop in equal-area rings: one meridian for
    rotationally symmetric lenses, one quadrant for anamorphic ones.
    """
    surfaces, types = lens["surfaces"], lens["surface_types"]
    total_inf = sum(s["thickness"] for s in surfaces[:-1])
    close = lens["focus"]["close_distance"] if lens["focus"] else 0.0
    alpha = 0.0
    if focus_mm is not None and close > 0.0:
        d_obj = focus_mm - total_inf
        alpha = min(max(close / d_obj, 0.0), 1.0) if d_obj > 0.0 else 1.0
    thicknesses = focus_thicknesses(lens, alpha)
    vertex_z = [-sum(thicknesses[:-1])]
    for t in thicknesses[:-1]:
        vertex_z.append(vertex_z[-1] + t)
    paraxial = paraxial_sensor_distance(lens, thicknesses, focus_mm)
    stop = types.index("stop")
    stop_semi = surfaces[stop]["aperture"] * 0.5
    scale_x = _stop_ray_scale(lens, thicknesses, focus_mm, "x") / stop_semi
    scale_y = _stop_ray_scale(lens, thicknesses, focus_mm, "y") / stop_semi
    iors_d = [s["ior"] for s in surfaces]
    iors_by_wavelength = [[cauchy_ior(s["ior"], s["abbe_v"], w) for s in surfaces] for w in _FOCUS_WAVELENGTHS]
    thetas = [(j + 0.5) * math.pi * 0.5 / _FOCUS_ANGLES for j in range(_FOCUS_ANGLES)]
    # 回転対称なら輪帯の像も輪帯で、鮮鋭度は光軸からの距離だけで決まるため子午線上の 1 本で輪帯全体を代表できる
    rotational = not any(st.startswith("cylindrical") for st in types)
    traced_thetas = [math.pi * 0.5] if rotational else thetas

    def launch(ax: float, ay: float) -> tuple[tuple, tuple]:
        if focus_mm is None:
            return (ax, ay, vertex_z[0] - 100.0), (0.0, 0.0, 1.0)
        dl = (ax * ax + ay * ay + 1.0) ** 0.5
        return (0.0, 0.0, -focus_mm), (ax / dl, ay / dl, 1.0 / dl)

    rays = []
    for ring in range(_FOCUS_RINGS):
        rho = ((ring + 0.5) / _FOCUS_RINGS) ** 0.5 * stop_semi * aperture_scale
        for theta in traced_thetas:
            tx, ty = rho * math.cos(theta), rho * math.sin(theta)
            ax, ay = tx * scale_x, ty * scale_y
            # 近軸の瞳は実光線の瞳とずれるため、絞り上の目標点に当たるよう打ち出しを数回補正する
            for _ in range(3):
                hit = _trace_real(lens, vertex_z, iors_d, *launch(ax, ay), stop + 1)
                if hit is None:
                    break
                hx, hy = hit[0][0], hit[0][1]
                if abs(hx) > 1e-12:
                    ax *= tx / hx
                if abs(hy) > 1e-12:
                    ay *= ty / hy
            for iors in iors_by_wavelength:
                out = _trace_real(lens, vertex_z, iors, *launch(ax, ay), len(surfaces))
                if out is None:
                    continue
                (px, py, pz), (dx, dy, dz) = out
                sx, sy = dx / dz, dy / dz
                rays.append((px - pz * sx, sx, py - pz * sy, sy))
    return paraxial, rays


def focus_sharpness(rays: list[tuple[float, float, float, float]], z: float) -> float:
    """Fraction of the on-axis beam concentrated within about _FOCUS_BLUR_RADIUS on the plane z (larger is sharper)."""
    # ガウス窓で数えると PSF の芯の集中度になり、MTF を低〜中周波で積分したものに相当する
    # 軸上光束は X / Y 対称で重心が 0 なので、光軸からの距離だけで評価できる
    inv_r2 = 1.0 / (_FOCUS_BLUR_RADIUS * _FOCUS_BLUR_RADIUS)
    total = 0.0
    for x0, sx, y0, sy in rays:
        x, y = x0 + z * sx, y0 + z * sy
        total += math.exp(-(x * x + y * y) * inv_r2)
    return total / len(rays)


def real_focus_offset(lens: dict, focus_mm: float | None, aperture_scale: float = 1.0) -> float:
    """Real-ray best focus minus the paraxial (Y-axis) sensor distance, in mm.

    Best focus maximizes focus_sharpness() of the polychromatic on-axis beam
    (X and Y combined). See on_axis_rays() for the arguments.
    """
    paraxial, rays = on_axis_rays(lens, focus_mm, aperture_scale)
    slope = max((max(abs(sx), abs(sy)) for _, sx, _, sy in rays), default=0.0)
    if slope == 0.0:
        return 0.0
    # 探索範囲は各光線が軸を横切る位置の全体 (縦収差の広がり) と近軸位置を含む区間
    crossings = [paraxial]
    for x0, sx, y0, sy in rays:
        if abs(sy) > 1e-12:
            crossings.append(-y0 / sy)
        if abs(sx) > 1e-12:
            crossings.append(-x0 / sx)
    lo, hi = min(crossings), max(crossings)
    # 各光線の寄与は z 方向に幅 ≈ 評価半径 / 傾き の山なので、最も鋭い山を飛ばさない間隔で全域を調べる
    step = 0.5 * _FOCUS_BLUR_RADIUS / slope
    count = min(int((hi - lo) / step) + 2, 2000)
    step = (hi - lo) / (count - 1) if hi > lo else 0.0
    best = max((lo + i * step for i in range(count)), key=lambda z: focus_sharpness(rays, z))
    # 格子の間隔より細かく合わせるため、最良点の前後を 2 段階で詰める
    for _ in range(2):
        candidates = [best + (i - 5) * step * 0.2 for i in range(11)]
        best = max(candidates, key=lambda z: focus_sharpness(rays, z))
        step *= 0.2
    return best - paraxial


def focus_offset_table(lens: dict) -> tuple[float, list[float]]:
    """Open-aperture real_focus_offset() sampled for the shader's focus-offset interpolation.

    Returns (reference distance, offsets) where offsets[k] is the offset at
    alpha = k / FOCUS_OFFSET_STEPS with alpha = clamp(reference / d_obj, 0, 1),
    the same reciprocal-distance parameter as the [focus] spacing.
    """
    # 内焦レンズは [focus] の最短距離、全群繰り出しは像倍率で収差が変わるので焦点距離の 10 倍を基準にする
    reference = lens["focus"]["close_distance"] if lens["focus"] else 10.0 * lens["focal_length"]
    total_inf = sum(s["thickness"] for s in lens["surfaces"][:-1])
    offsets = [real_focus_offset(lens, None)]
    for k in range(1, FOCUS_OFFSET_STEPS + 1):
        offsets.append(real_focus_offset(lens, reference * FOCUS_OFFSET_STEPS / k + total_inf))
    return reference, offsets


def _validate_surfaces(surfaces: list[dict], surface_types: list[str], filename: str) -> None:
    for i, (s, st) in enumerate(zip(surfaces, surface_types, strict=True)):
        where = f"{filename}: surface {i}"
        _number(s, "radius", where)
        _number(s, "thickness", where, minimum=0)
        _number(s, "ior", where, minimum=1.0)
        _number(s, "aperture", where, minimum=0, strict=True)
        _number(s, "abbe_v", where, minimum=0)
        if st == "stop":
            if s["radius"] != 0 or s["ior"] != 1.0:
                raise ValueError(f"{where}: stop must have radius = 0 and ior = 1.0")
        elif st == "aspheric":
            if s["radius"] == 0:
                raise ValueError(f"{filename}: aspheric surface {i} must have nonzero radius")
            coeffs = s.get("aspheric_coeffs")
            if (
                not isinstance(coeffs, list)
                or len(coeffs) not in (3, 4)
                or any(isinstance(c, bool) or not isinstance(c, (int, float)) for c in coeffs)
            ):
                raise ValueError(
                    f"{filename}: aspheric surface {i} must have aspheric_coeffs as a list of 3 or 4 floats"
                )
            if "conic" in s:
                _number(s, "conic", where)
        elif st in ("cylindrical_x", "cylindrical_y"):
            if s["radius"] == 0:
                raise ValueError(f"{filename}: cylindrical surface {i} must have nonzero radius")
            if "aspheric_coeffs" in s or "conic" in s:
                raise ValueError(f"{filename}: cylindrical surface {i} must not have aspheric_coeffs or conic")
        else:
            if st == "spherical" and s["radius"] == 0:
                raise ValueError(f"{filename}: spherical surface {i} must have nonzero radius")
            if "aspheric_coeffs" in s or "conic" in s:
                raise ValueError(f"{filename}: non-aspheric surface {i} must not have aspheric_coeffs or conic")


def load_lens(toml_path: Path) -> dict:
    """Read and validate one lens file. Raises ValueError (or a TOML decode error) when invalid."""
    filename = toml_path.name
    with open(toml_path, "rb") as f:
        data = tomllib.load(f)
    lens = data.get("lens")
    if not isinstance(lens, dict):
        raise ValueError(f"{filename}: missing [lens] table")
    name = lens.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"{filename}: lens.name must be a non-empty string")
    focal_length = _number(lens, "focal_length", f"{filename}: lens", minimum=0, strict=True)
    max_fstop = _number(lens, "max_fstop", f"{filename}: lens", minimum=0, strict=True)
    surfaces = data.get("surface")
    if not isinstance(surfaces, list) or not surfaces or not all(isinstance(s, dict) for s in surfaces):
        raise ValueError(f"{filename}: missing [[surface]] entries")
    if len(surfaces) > MAX_SURFACES:
        raise ValueError(f"{filename}: {len(surfaces)} surfaces exceeds MAX_SURFACES ({MAX_SURFACES})")
    coating = lens.get("coating", "none")
    if coating not in VALID_COATINGS:
        raise ValueError(f"{filename}: coating {coating!r} must be one of {VALID_COATINGS}")
    squeeze = lens.get("squeeze", 1.0)
    if isinstance(squeeze, bool) or not isinstance(squeeze, (int, float)) or squeeze <= 0:
        raise ValueError(f"{filename}: squeeze {squeeze!r} must be a positive number")
    # 型推論で radius を参照するため、面ごとの数値検証より先に radius の有無だけ確認する
    for i, s in enumerate(surfaces):
        _number(s, "radius", f"{filename}: surface {i}")
    surface_types = _resolve_surface_types(surfaces, filename)
    _validate_surfaces(surfaces, surface_types, filename)
    stop_count = surface_types.count("stop")
    if stop_count != 1:
        raise ValueError(f"{filename}: expected exactly 1 stop surface, found {stop_count}")
    stop_index = surface_types.index("stop")
    focus = _parse_focus(data, surfaces, filename)
    try:
        open_fstop = paraxial_fstop(surfaces, surface_types)
    except ValueError as e:
        raise ValueError(f"{filename}: {e}") from e
    return {
        "name": name,
        "filename_stem": toml_path.stem,
        "focal_length": focal_length,
        "max_fstop": max_fstop,
        "open_fstop": open_fstop,
        "stop_index": stop_index,
        "coating": coating,
        "squeeze": squeeze,
        "surfaces": surfaces,
        "surface_types": surface_types,
        "focus": focus,
    }


def load_lenses(lens_dir: Path) -> list[dict]:
    """Read all .toml lens files from lens_dir, sorted by filename.

    Invalid files are skipped with a message so one broken lens does not
    prevent the others from loading.
    """
    lenses = []
    for toml_path in sorted(lens_dir.glob("*.toml")):
        try:
            lenses.append(load_lens(toml_path))
        # 検証で拾いきれない想定外の構造 (KeyError/TypeError) でもアドオン全体を落とさないため広めに捕捉する
        except (OSError, tomllib.TOMLDecodeError, ValueError, KeyError, TypeError) as e:
            detail = str(e).removeprefix(f"{toml_path.name}: ")
            print(f"Physical Camera: skipped lens {toml_path.name}: {detail}")
    return lenses

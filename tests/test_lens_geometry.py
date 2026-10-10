"""レンズ形状の健全性とアナモルフィック整合のテスト。

サグ・縁厚・近軸追跡はすべてテスト内に独立実装する
(lenses.py の結果と同じ式を比較しても自己検証にならないため)。
"""

import math

import pytest
from conftest import lenses

# 曲面 (サグ計算の対象になる型)。cylindrical は曲率軸方向にのみ曲がるが、
# 縁の最も突出した点のサグは球面と同じ式で上から押さえられる (保守的な近似)
_CURVED = ("spherical", "aspheric", "cylindrical_x", "cylindrical_y")


def _sag(s: dict, h: float) -> float:
    """Signed surface sag at semi-aperture h, positive toward the sensor (+z).

    Matches the template's aspheric_sag(): conic base sphere plus even
    polynomial terms, so it covers spherical / cylindrical / aspheric alike.
    """

    radius = s["radius"]
    if radius == 0:
        return 0.0
    c = 1.0 / radius
    k = s.get("conic", 0.0)
    q = max(1.0 - (1.0 + k) * c * c * h * h, 1e-20)
    base = c * h * h / (1.0 + math.sqrt(q))
    poly = sum(a * h ** (2 * (n + 2)) for n, a in enumerate(s.get("aspheric_coeffs", [])))
    return base + poly


def _edge_clearance(surfaces: list[dict], i: int) -> float:
    """Air/glass distance between surface i and i+1 at the smaller rim (mm).

    Negative means the two rims physically cross — impossible to build.
    Vertex spacing comes from the thickness chain with the rear vertex at z=0.
    """

    zs = [0.0] * len(surfaces)
    z = 0.0
    for j in range(len(surfaces) - 2, -1, -1):
        z += surfaces[j]["thickness"]
        zs[j] = -z
    h = min(surfaces[i]["aperture"], surfaces[i + 1]["aperture"]) / 2
    return zs[i + 1] + _sag(surfaces[i + 1], h) - (zs[i] + _sag(surfaces[i], h))


# 特許・公表値の丸めにより縁でわずかに交差する表記になる隣接面
# (0-based surface index -> 許容する縁厚の下限 mm、実測に若干のマージンを足した値)。
# stop との負値は絞りが軸周りのブレード群でレンズ縁との物理衝突がないため許容する
_EDGE_OVERLAPS = {
    ("cooke_triplet_50mm_f4_5", 2): -0.74,
    ("tessar_50mm_f2_8", 3): -1.39,
    ("neil_front_anamorphic_2x", 5): -1.53,
    ("nikkor_fisheye_16mm_f2_8", 1): -0.19,
}

# squeeze 表記と EFL_y/EFL_x の許容差。現実測は Navarro -1.92% / Neil -1.13%
_ANAMORPHIC_RATIO_TOL = 0.03
# BFD の軸間差の上限 mm。実測は Navarro 0.176mm / Neil 0.111mm
_ANAMORPHIC_BFD_MAX = 0.5


def test_aperture_fits_within_curved_radius(bundled_lenses):
    # aperture/2 > |R| の曲面はサグ式と OSL の球面交差が破綻する
    for lens in bundled_lenses:
        for i, (s, st) in enumerate(zip(lens["surfaces"], lens["surface_types"], strict=True)):
            if st in _CURVED:
                assert s["aperture"] / 2 <= abs(s["radius"]) + 1e-9, (
                    f"{lens['filename_stem']}: surface {i} aperture/2 {s['aperture'] / 2:.4f} "
                    f"exceeds |radius| {abs(s['radius']):.4f}"
                )


def test_negative_edge_clearance_is_allowlisted(bundled_lenses):
    for lens in bundled_lenses:
        surfs = lens["surfaces"]
        for i in range(len(surfs) - 1):
            edge = _edge_clearance(surfs, i)
            key = (lens["filename_stem"], i)
            if edge < 0:
                assert key in _EDGE_OVERLAPS, (
                    f"{lens['filename_stem']}: S{i}-S{i + 1} edge clearance {edge:.4f} mm "
                    f"is negative and not in the allowlist"
                )
                assert edge >= _EDGE_OVERLAPS[key], (
                    f"{lens['filename_stem']}: S{i}-S{i + 1} edge clearance {edge:.4f} mm "
                    f"is worse than the allowlisted {_EDGE_OVERLAPS[key]}"
                )


def test_edge_overlap_allowlist_is_current(bundled_lenses):
    # 許容リストが実測と一致すること — データ修正で直った項目は外し、
    # 新たに負になった面は理由を書いて追加する
    actual = {
        (lens["filename_stem"], i)
        for lens in bundled_lenses
        for i in range(len(lens["surfaces"]) - 1)
        if _edge_clearance(lens["surfaces"], i) < 0
    }
    assert actual == set(_EDGE_OVERLAPS)


def _paraxial_axis(lens: dict, axis: str) -> tuple[float, float]:
    """Return (EFL, back focal distance) on one axis via a 2x2 ABCD trace.

    Written independently of lenses.paraxial_*; cylindrical elements carry
    power only on their curved axis and are flat in the other.
    """

    def mul(a, b):
        return (
            a[0] * b[0] + a[1] * b[2],
            a[0] * b[1] + a[1] * b[3],
            a[2] * b[0] + a[3] * b[2],
            a[2] * b[1] + a[3] * b[3],
        )

    m = (1.0, 0.0, 0.0, 1.0)
    n_before = 1.0
    surfaces = lens["surfaces"]
    for i, (s, st) in enumerate(zip(surfaces, lens["surface_types"], strict=True)):
        has_power = st in ("spherical", "aspheric")
        if axis == "y" and st == "cylindrical_y":
            has_power = True
        if axis == "x" and st == "cylindrical_x":
            has_power = True
        if has_power:
            m = mul((1.0, 0.0, -(s["ior"] - n_before) / s["radius"], 1.0), m)
        n_before = s["ior"]
        if i < len(surfaces) - 1:
            m = mul((1.0, s["thickness"] / s["ior"], 0.0, 1.0), m)
    return -1.0 / m[2], -m[0] / m[2]


def test_paraxial_y_axis_matches_lens_implementation(bundled_lenses):
    # 追跡実装の信頼性担保: Y 軸 (合焦軸) は lenses.paraxial_back_focal_distance と一致するはず
    # X 軸はシリンダー群の影響で BFD が異なるため比較対象にならない
    for lens in bundled_lenses:
        _, bfd = _paraxial_axis(lens, "y")
        assert bfd == pytest.approx(lenses.paraxial_back_focal_distance(lens["surfaces"], lens["surface_types"])), lens[
            "filename_stem"
        ]


def test_anamorphic_efl_ratio_matches_squeeze(bundled_lenses):
    for lens in bundled_lenses:
        if lens["squeeze"] == 1.0:
            continue
        efl_x, _ = _paraxial_axis(lens, "x")
        efl_y, _ = _paraxial_axis(lens, "y")
        ratio = efl_y / efl_x
        assert ratio == pytest.approx(lens["squeeze"], rel=_ANAMORPHIC_RATIO_TOL), (
            f"{lens['filename_stem']}: EFL_y/EFL_x = {ratio:.4f}, "
            f"squeeze = {lens['squeeze']} (tolerance {(_ANAMORPHIC_RATIO_TOL) * 100:.0f}%)"
        )


def test_anamorphic_bfd_axes_agree(bundled_lenses):
    # X/Y の BFD が大きくずれると desqueeze 後の焦点面が片軸でぼやける
    for lens in bundled_lenses:
        if lens["squeeze"] == 1.0:
            continue
        _, bfd_x = _paraxial_axis(lens, "x")
        _, bfd_y = _paraxial_axis(lens, "y")
        assert abs(bfd_x - bfd_y) <= _ANAMORPHIC_BFD_MAX, (
            f"{lens['filename_stem']}: |BFD_x - BFD_y| = {abs(bfd_x - bfd_y):.3f} mm exceeds {_ANAMORPHIC_BFD_MAX} mm"
        )

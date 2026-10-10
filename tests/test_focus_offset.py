import math
import re

import pytest
from conftest import LENS_DIR, TEMPLATE_PATH, codegen, lenses


def _by_stem(bundled_lenses: list[dict], stem: str) -> dict:
    return next(lens for lens in bundled_lenses if lens["filename_stem"] == stem)


def test_neil_offset_matches_previous_real_ray_check(bundled_lenses):
    # 以前の実光線スポット計算 (分光込み) では 3m で約 -0.06mm、1.2m で約 -0.085mm、無限遠でほぼ 0 だった
    neil = _by_stem(bundled_lenses, "neil_front_anamorphic_2x")
    assert lenses.real_focus_offset(neil, 3000.0) == pytest.approx(-0.06, abs=0.02)
    assert lenses.real_focus_offset(neil, 1200.0) == pytest.approx(-0.085, abs=0.02)
    assert abs(lenses.real_focus_offset(neil, None)) < 0.04


def test_offset_vanishes_for_paraxial_rays(bundled_lenses, monkeypatch):
    # 単色で開口をごく小さくすると実光線は近軸光線に一致するので、追跡と近軸計算の食い違いが無いことを確かめる
    monkeypatch.setattr(lenses, "_FOCUS_WAVELENGTHS", (587.6,))
    for lens in bundled_lenses:
        # アナモルフィックは近軸でも X と Y の像面が離れており、総合の最良位置は Y の像面に一致しない
        if any(t.startswith("cylindrical") for t in lens["surface_types"]):
            continue
        assert abs(lenses.real_focus_offset(lens, None, 0.03)) < 0.005, lens["filename_stem"]


def test_offset_improves_sharpness_for_every_lens(bundled_lenses):
    for lens in bundled_lenses:
        reference, offsets = lenses.focus_offset_table(lens)
        assert len(offsets) == lenses.FOCUS_OFFSET_STEPS + 1
        total_inf = sum(s["thickness"] for s in lens["surfaces"][:-1])
        for focus_mm, offset in ((None, offsets[0]), (reference + total_inf, offsets[-1])):
            # 軟焦点 (Mamiya 150 Soft) は球面収差が大きく約 1mm ずれるが、それ以外は 1mm 未満
            assert math.isfinite(offset) and abs(offset) < 1.5, lens["filename_stem"]
            paraxial, rays = lenses.on_axis_rays(lens, focus_mm)
            assert lenses.focus_sharpness(rays, paraxial + offset) >= lenses.focus_sharpness(rays, paraxial)


def test_offset_reference_distance(bundled_lenses):
    # 内焦レンズは間隔補間と同じ α になるよう [focus] の最短距離を基準にする
    canon = _by_stem(bundled_lenses, "canon_rf_85mm_f1_2")
    assert lenses.focus_offset_table(canon)[0] == canon["focus"]["close_distance"]
    tessar = _by_stem(bundled_lenses, "tessar_50mm_f2_8")
    assert lenses.focus_offset_table(tessar)[0] == 10.0 * tessar["focal_length"]


def test_focus_offset_is_emitted(bundled_lenses):
    template, _ = codegen.load_osl_template(TEMPLATE_PATH, LENS_DIR)
    # 開放基準のずれを絞りに比例させてセンサー位置に足す
    assert "sensor_distance += focus_offset(offset_alpha) * aperture_scale;" in template
    neil = _by_stem(bundled_lenses, "neil_front_anamorphic_2x")
    osl = codegen.build_camera_osl(template, neil)
    reference, offsets = lenses.focus_offset_table(neil)
    assert float(re.search(r"#define FOCUS_OFFSET_DISTANCE (\S+)", osl).group(1)) == reference
    table = re.search(r"float table\[(\d+)\] = \{ ([^}]+) \};", osl)
    assert int(table.group(1)) == lenses.FOCUS_OFFSET_STEPS + 1
    values = [float(v) for v in table.group(2).split(",")]
    assert values == pytest.approx(offsets, abs=1e-5)

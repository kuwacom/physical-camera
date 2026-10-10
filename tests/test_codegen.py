import re

import pytest
from conftest import LENS_DIR, TEMPLATE_PATH, codegen, lenses


@pytest.fixture(scope="module")
def template() -> str:
    text, _ = codegen.load_osl_template(TEMPLATE_PATH, LENS_DIR)
    return text


def test_template_has_placeholders(template):
    assert "// {{LENS_DATA}}" in template
    assert "// {{SCENE_LIGHTS}}" in template


def test_build_camera_osl_for_every_lens(template, bundled_lenses):
    for lens in bundled_lenses:
        osl = codegen.build_camera_osl(template, lens)
        n = len(lens["surfaces"])

        assert "{{LENS_DATA}}" not in osl
        assert "{{SCENE_LIGHTS}}" not in osl
        assert f"#define MAX_SURFACES {lenses.MAX_SURFACES}" in osl
        assert f"#define MAX_EXTRA {lenses.MAX_SURFACES * codegen.N_EXTRA}" in osl
        assert f"#define NUM_SURFACES {n}" in osl
        assert f"#define SQUEEZE {float(lens['squeeze'])}" in osl
        assert "void load_lens_data(" in osl
        assert "num_lights = 0;" in osl
        # 全面ぶんの面データと型が出力されていること (欠けると OSL 側で 0 のまま追跡される)
        assert len(re.findall(r"^\s+radii\[\d+\]", osl, re.MULTILINE)) == n
        types = [int(v) for v in re.findall(r"surface_types\[\d+\] = (\d+);", osl)]
        assert types.count(2) == 1
        assert len(types) == n


def test_sensor_distance_fallback_is_finite(template, bundled_lenses):
    # 光学パワーが無いときの退避先が 0 除算 (-A / C) ではなく事前計算した無限遠のセンサー位置であること
    assert "return SENSOR_DISTANCE_INF;" in template
    for lens in bundled_lenses:
        osl = codegen.build_camera_osl(template, lens)
        value = float(re.search(r"#define SENSOR_DISTANCE_INF (\S+)", osl).group(1))
        assert value == lenses.paraxial_back_focal_distance(lens["surfaces"], lens["surface_types"])
        assert 0.0 < value < 1000.0, lens["filename_stem"]


def test_focus_and_aspheric_data_are_emitted(template, bundled_lenses):
    canon = next(lens for lens in bundled_lenses if lens["filename_stem"] == "canon_rf_85mm_f1_2")
    osl = codegen.build_camera_osl(template, canon)
    assert "#define FOCUS_CLOSE_DISTANCE 714.98" in osl
    assert "thicknesses_close[2] = 1.64;" in osl
    # 非球面の係数は extra[i * N_EXTRA + 1..] に入る
    assert f"extra[{7 * codegen.N_EXTRA + 1}] = -2.2875e-06;" in osl


def test_scene_lights_are_emitted(template, bundled_lenses):
    light = {"type": 1, "pos": (1.0, 2.0, 3.0), "dir": (0.0, 0.0, 1.0), "intensity": 10.0, "radius": 0.1}
    osl = codegen.build_camera_osl(template, bundled_lenses[0], [light])
    assert "num_lights = 1;" in osl
    assert "light_pos[2] = 3.0;" in osl

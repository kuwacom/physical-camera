import re

import pytest
from conftest import LENS_DIR, TEMPLATE_PATH, codegen, lenses, scene_lights


@pytest.fixture(scope="module")
def template() -> str:
    text, _ = codegen.load_osl_template(TEMPLATE_PATH, LENS_DIR)
    return text


def test_template_has_placeholders(template):
    assert "// {{LENS_DATA}}" in template
    assert "// {{SCENE_LIGHTS}}" in template
    assert "// {{SCENE_LIGHT_PARAMS}}" in template
    assert "// {{LOAD_SCENE_LIGHTS}}" in template


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
        # レンズだけで決まる値はシェーダーで光線ごとに求め直さず codegen が埋め込む
        assert f"#define STOP_INDEX {lens['surface_types'].index('stop')}" in osl
        total_length = float(re.search(r"#define TOTAL_LENGTH_INF (\S+)", osl).group(1))
        assert total_length == pytest.approx(sum(s["thickness"] for s in lens["surfaces"][:-1]))
        assert "void load_lens_data(" in osl
        assert "{{" not in osl
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


def test_entrance_pupil_magnification_is_emitted(template, bundled_lenses):
    # 回折角は入射瞳径で決まるため、絞り径に掛ける倍率を軸ごとに焼き込む
    assert "px / ENTRANCE_PUPIL_MAG_X" in template
    assert "py / ENTRANCE_PUPIL_MAG_Y" in template
    for lens in bundled_lenses:
        osl = codegen.build_camera_osl(template, lens)
        mag_x = float(re.search(r"#define ENTRANCE_PUPIL_MAG_X (\S+)", osl).group(1))
        mag_y = float(re.search(r"#define ENTRANCE_PUPIL_MAG_Y (\S+)", osl).group(1))
        assert 0.1 < mag_x < 10.0 and 0.1 < mag_y < 10.0, lens["filename_stem"]
        # Y 軸の入射瞳径は開放 f 値 (EFL / 入射瞳径) の計算と一致する
        _, nu, _, _ = lenses._paraxial_marginal_ray(lens["surfaces"], lens["surface_types"])
        stop_d = lens["surfaces"][lens["stop_index"]]["aperture"]
        assert (-1.0 / nu) / (stop_d * mag_y) == pytest.approx(lens["open_fstop"])
        if not any(t.startswith("cylindrical") for t in lens["surface_types"]):
            assert mag_x == mag_y


def test_entrance_pupil_magnification_values(bundled_lenses):
    by_stem = {lens["filename_stem"]: lens for lens in bundled_lenses}

    def mag(stem, axis):
        lens = by_stem[stem]
        return lenses.paraxial_entrance_pupil_magnification(lens["surfaces"], lens["surface_types"], axis)

    # 前群が負の魚眼は入射瞳が絞りより小さく、前置アナモルフィックは X の入射瞳が Y の約半分になる
    assert mag("nikkor_fisheye_16mm_f2_8", "y") == pytest.approx(0.405, abs=0.005)
    assert mag("neil_front_anamorphic_2x", "y") == pytest.approx(1.180, abs=0.005)
    assert mag("neil_front_anamorphic_2x", "x") == pytest.approx(0.596, abs=0.005)


def test_shader_parameters_use_help_metadata(template, bundled_lenses):
    # Cycles の osl.py は help だけを Parameters パネルの説明に反映し、description は捨てる
    osl = codegen.build_camera_osl(template, bundled_lenses[0])
    start = osl.index("shader lens_camera(")
    header = osl[start : osl.index("output point position", start)]
    assert "string description" not in header
    params = re.findall(r"^\s+(?:int|float|vector) (\w+) = [^\n]*\n\s+\[\[ string help = \"([^\"]+)\"", header, re.M)
    names = [name for name, _ in params]
    assert len(names) == 8 + 1 + 2 * scene_lights.MAX_LIGHTS
    assert "aperture_scale" in names and "scene_light_count" in names
    # ライト用は自動設定なので手で編集しないよう UI に出す
    for name, text in params:
        if name.startswith("scene_light"):
            assert "do not edit" in text, name
    assert "int min = 0, int max = 20" in header

import re
from types import SimpleNamespace

import pytest
from conftest import LENS_DIR, TEMPLATE_PATH, codegen, scene_lights

POINT = {"type": 0, "pos": (1.0, 2.0, 3.0), "dir": (0.0, 0.0, 0.0), "intensity": 10.0, "radius": 50.0}
SUN = {"type": 1, "pos": (0.0, 0.0, 0.0), "dir": (0.0, 0.6, 0.8), "intensity": 3.0, "radius": 0.0}


@pytest.fixture(scope="module")
def osl() -> str:
    template, lenses = codegen.load_osl_template(TEMPLATE_PATH, LENS_DIR)
    return codegen.build_camera_osl(template, lenses[0])


def test_light_values_are_not_baked_into_source(osl):
    # ライトをソースに埋め込むとライトの変更で再コンパイルが必要になるため、ソースは常に同一であること
    template, lenses = codegen.load_osl_template(TEMPLATE_PATH, LENS_DIR)
    assert codegen.build_camera_osl(template, lenses[0]) == osl
    assert "load_scene_lights(" not in osl


def test_every_light_slot_has_scalar_shader_params(osl):
    # 配列パラメーターは Cycles のカメラに渡らないため、スロットごとに vector パラメーターで受け取る
    assert re.search(rf"^\s+int {scene_lights.LIGHT_COUNT_PARAM} = 0$", osl, re.MULTILINE)
    for i in range(scene_lights.MAX_LIGHTS):
        geom, info = scene_lights.light_param_names(i)
        assert re.search(rf"^\s+vector {geom} = 0$", osl, re.MULTILINE)
        assert re.search(rf"^\s+vector {info} = 0$", osl, re.MULTILINE)
        assert f"unpack_scene_light({i}, {geom}, {info}," in osl
    assert f"#define MAX_LIGHTS {scene_lights.MAX_LIGHTS}" in osl
    # 光線ごとのライト読み込みはゴースト/回折の分岐内に入ったまま (直接光線は読まない)
    load = osl.index(f"num_lights = clamp({scene_lights.LIGHT_COUNT_PARAM}, 0, MAX_LIGHTS);")
    branch = osl.rindex("if (trace_ghost || (diffraction && aperture_blades >= 3)) {", 0, load)
    assert osl.index("transform_lights_to_camera(num_lights", load) > load > branch


def test_light_param_values_pack_position_or_direction():
    values = scene_lights.light_param_values([POINT, SUN])
    assert values[scene_lights.LIGHT_COUNT_PARAM] == 2
    g0, i0 = scene_lights.light_param_names(0)
    g1, i1 = scene_lights.light_param_names(1)
    assert values[g0] == (1.0, 2.0, 3.0)
    assert values[i0] == (0.0, 10.0, 50.0)
    # サンは位置を持たないため方向を同じスロットに入れる
    assert values[g1] == (0.0, 0.6, 0.8)
    assert values[i1] == (1.0, 3.0, 0.0)
    last_geom, last_info = scene_lights.light_param_names(scene_lights.MAX_LIGHTS - 1)
    assert values[last_geom] == (0.0, 0.0, 0.0)
    assert values[last_info] == (0.0, 0.0, 0.0)
    assert len(values) == 1 + 2 * scene_lights.MAX_LIGHTS


def test_light_param_values_are_capped():
    lights = [POINT] * (scene_lights.MAX_LIGHTS + 4)
    assert scene_lights.light_param_values(lights)[scene_lights.LIGHT_COUNT_PARAM] == scene_lights.MAX_LIGHTS


def test_light_params_differ():
    values = scene_lights.light_param_values([POINT])
    assert scene_lights.light_params_differ({}, values)
    stored = dict(values)
    assert not scene_lights.light_params_differ(stored, values)
    # 単精度に丸めて保存された値は変化とみなさない (毎回の書き込みと再同期を避ける)
    g0, _ = scene_lights.light_param_names(0)
    stored[g0] = (1.0000000298, 2.0, 3.0)
    assert not scene_lights.light_params_differ(stored, values)
    stored[g0] = (1.01, 2.0, 3.0)
    assert scene_lights.light_params_differ(stored, values)
    stored = dict(values)
    stored[scene_lights.LIGHT_COUNT_PARAM] = 0
    assert scene_lights.light_params_differ(stored, values)


def _layer(name, objects, children=(), exclude=False, hide_render=False):
    collection = SimpleNamespace(name=name, objects=objects, hide_render=hide_render)
    return SimpleNamespace(collection=collection, children=list(children), exclude=exclude)


def _obj(name, hide_render=False):
    return SimpleNamespace(name_full=name, hide_render=hide_render)


def test_render_objects_follow_view_layer_visibility():
    shown, excluded, render_off = _obj("shown"), _obj("excluded"), _obj("render_off")
    hidden_obj, unused_only = _obj("hidden_obj", hide_render=True), _obj("unused_only")
    nested = _obj("nested")
    used = SimpleNamespace(
        use=True,
        layer_collection=_layer(
            "Scene",
            [shown, hidden_obj],
            [
                _layer("excl", [excluded], exclude=True),
                _layer("off", [render_off], hide_render=True),
                _layer("parent", [], [_layer("child", [nested, shown])]),
            ],
        ),
    )
    unused = SimpleNamespace(use=False, layer_collection=_layer("Scene", [unused_only]))
    scene = SimpleNamespace(view_layers=[used, unused])

    names = sorted(obj.name_full for obj in scene_lights._render_objects(scene))
    # 除外・レンダリング無効のコレクションやオブジェクト、レンダリングに使わないビューレイヤーのライトには照準しない
    assert names == ["nested", "shown"]

    # レンダリングに使うビューレイヤーが無いときは全ビューレイヤーを対象にする
    used.use = False
    names = sorted(obj.name_full for obj in scene_lights._render_objects(scene))
    assert names == ["nested", "shown", "unused_only"]

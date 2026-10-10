"""Physical Camera — realistic lens simulation for Blender's OSL camera."""

from pathlib import Path

import bpy
from bpy.app.handlers import persistent
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
)

from . import codegen, diagram, scene_lights
from .lenses import aperture_scale, min_fstop

_TEXT_BLOCK_PREFIX = "Physical Lens - "

# oslc のコンパイルエラーをカメラ名で保持しパネルに表示する
_compile_errors: dict[str, str] = {}

_lens_registry: list[dict] = []
_lens_items: list[tuple] = []
_lens_map: dict[str, dict] = {}
_osl_template: str = ""
_updating_lights: bool = False


def _get_lens(props) -> dict | None:
    lens = _lens_map.get(props.lens)
    if lens is not None:
        return lens
    if _lens_registry:
        return _lens_registry[0]
    return None


def _is_text_used_by_other_camera(text, cam):
    return any(c != cam and c.custom_shader == text for c in bpy.data.cameras)


def _remove_orphan_text_blocks():
    """Remove generated text datablocks that no camera references anymore."""
    used = {cam.custom_shader for cam in bpy.data.cameras}
    for text in list(bpy.data.texts):
        # 生成テキストの接頭辞に一致する未参照のものだけを消し、ユーザーが手で作ったテキストを守る
        if text.library is not None or not text.name.startswith(_TEXT_BLOCK_PREFIX) or text in used:
            continue
        bpy.data.texts.remove(text)


def _get_or_create_text_block(cam):
    """Get or create a per-camera text datablock for the generated OSL shader."""
    # Reuse existing text block if camera already has one with our prefix
    # 複製したカメラデータは元と同じテキストを参照しており、上書きすると元カメラのレンズまで変わるため共有中は再利用しない
    if (
        cam.type == "CUSTOM"
        and cam.custom_shader is not None
        and cam.custom_shader.name.startswith(_TEXT_BLOCK_PREFIX)
        and not _is_text_used_by_other_camera(cam.custom_shader, cam)
    ):
        text = cam.custom_shader
        name = f"{_TEXT_BLOCK_PREFIX}{cam.name}"
        if text.name != name:
            # カメラ名変更後にテキスト名が古いままだとテキストエディターで対応が分からなくなるため揃える
            text.name = name
        return text

    name = f"{_TEXT_BLOCK_PREFIX}{cam.name}"
    text = bpy.data.texts.get(name)
    if text is not None and not _is_text_used_by_other_camera(text, cam):
        return text
    return bpy.data.texts.new(name)


def _build_camera_shader(cam, osl_source=None):
    """Generate and assign a single-lens shader for a camera."""
    lens = _get_lens(cam.physical_camera)
    if lens is None:
        return

    if osl_source is None:
        osl_source = codegen.build_camera_osl(_osl_template, lens)
    text = _get_or_create_text_block(cam)
    text.clear()
    text.write(osl_source)
    cam.custom_shader = text
    _compile_camera_shader(cam)
    sync_to_cycles(cam)


def _report_shader_message(cam):
    def report(otype, message):
        # コンパイルが成功するたびにコンソールへ出力されると他のログが埋まるため、問題が起きたときだけ表示する
        if otype not in {"ERROR", "WARNING"}:
            return
        if otype == "ERROR":
            # パネルに表示するため最新のエラーをカメラ名で保持する
            _compile_errors[cam.name] = message
        print(f"Physical Camera: {message}")

    return report


def _compile_camera_shader(cam):
    """Compile the camera shader to bytecode even when the scene engine is not Cycles."""
    # custom_shader 代入時の自動コンパイルはシーンのエンジンが Cycles のときしか走らず、
    # EEVEE 等で有効化やレンズ変更をすると bytecode が空/古いまま残り Cycles で真っ黒になるため
    scene = bpy.context.scene
    if scene is not None and scene.render.engine == "CYCLES":
        return
    try:
        from cycles import engine, osl
    except ImportError:
        return
    if engine.with_osl():
        # 前回のエラーが残ると今回の結果を反映できないためコンパイル前に消す
        _compile_errors.pop(cam.name, None)
        osl.update_custom_camera_shader(cam, _report_shader_message(cam))


def sync_to_cycles(cam):
    """Push all PhysicalCameraProperties to cam.cycles_custom."""
    try:
        custom = cam.cycles_custom
    except AttributeError:
        return

    props = cam.physical_camera
    lens = _get_lens(props)

    custom["aperture_blades"] = props.aperture_blades
    from math import degrees

    custom["blade_rotation"] = degrees(props.blade_rotation)
    custom["chromatic_aberration"] = 1 if props.chromatic_aberration else 0
    custom["lens_ghosts"] = 1 if props.lens_ghosts else 0
    custom["ghost_intensity"] = props.ghost_intensity
    custom["diffraction"] = 1 if props.diffraction else 0

    debug_map = {
        "NORMAL": 0.0,
        "PINHOLE": 1.0,
        "DIAGNOSTIC": 2.0,
        "EXIT_DIR": 3.0,
        "GHOSTS_ONLY": 4.0,
        "GHOST_AIM": 5.0,
    }
    custom["debug_mode"] = debug_map[props.debug_mode]

    if lens is not None:
        custom["aperture_scale"] = aperture_scale(lens, props.fstop)
    else:
        custom["aperture_scale"] = 1.0


def _sync_focal_length(cam, lens):
    if lens is not None:
        cam.lens = lens["focal_length"]


def _on_lens_change(self, context):
    lens = _get_lens(self)
    if lens is not None:
        if self.fstop < min_fstop(lens):
            self.fstop = min_fstop(lens)
    # 選択中オブジェクトではなくプロパティの持ち主に反映しないと、スクリプト操作や別オブジェクト選択中の変更が別カメラに入る
    cam = self.id_data
    _sync_focal_length(cam, lens)
    _build_camera_shader(cam)


def _on_property_change(self, context):
    sync_to_cycles(self.id_data)


def _on_fstop_change(self, context):
    lens = _get_lens(self)
    if lens is not None:
        limit = min_fstop(lens)
        if self.fstop < limit:
            # RNA に代入すると update が再帰するため IDProperty に直接書いて開放 F 値へ戻す
            self["fstop"] = limit
    sync_to_cycles(self.id_data)


def _on_light_feature_toggle(self, context):
    sync_to_cycles(self.id_data)
    _update_camera_lights(self.id_data, context.scene)


class PhysicalCameraProperties(bpy.types.PropertyGroup):
    # lens EnumProperty is registered dynamically in register() with a static
    # items list so Blender stores the string identifier, not the integer index.
    # A callback-based items= would store an integer that breaks when lenses are
    # added or removed and the alphabetical order shifts.
    fstop: FloatProperty(
        name="f-stop",
        min=0.5,
        soft_min=2.0,
        soft_max=22.0,
        max=64.0,
        default=2.0,
        precision=1,
        update=_on_fstop_change,
    )
    aperture_blades: IntProperty(
        name="Aperture Blades",
        description="0 = circular",
        min=0,
        max=20,
        default=0,
        update=_on_property_change,
    )
    blade_rotation: FloatProperty(
        name="Blade Rotation",
        subtype="ANGLE",
        min=0.0,
        max=6.2831853,
        default=0.0,
        update=_on_property_change,
    )
    chromatic_aberration: BoolProperty(
        name="Chromatic Aberration",
        default=True,
        update=_on_property_change,
    )
    lens_ghosts: BoolProperty(
        name="Lens Ghosts",
        default=False,
        update=_on_light_feature_toggle,
    )
    ghost_intensity: FloatProperty(
        name="Ghost Intensity",
        description="Brightness multiplier for ghost images",
        min=0.0,
        soft_max=10.0,
        max=100.0,
        default=1.0,
        precision=2,
        update=_on_property_change,
    )
    diffraction: BoolProperty(
        name="Diffraction",
        description="Simulate aperture diffraction starbursts (polygonal apertures only)",
        default=False,
        update=_on_light_feature_toggle,
    )
    debug_mode: EnumProperty(
        name="Debug Mode",
        items=[
            ("NORMAL", "Normal", "Standard rendering"),
            ("PINHOLE", "Pinhole", "Pinhole camera (no lens)"),
            ("DIAGNOSTIC", "Diagnostic", "Failure cause visualization"),
            ("EXIT_DIR", "Exit Direction", "Exit ray direction as RGB"),
            ("GHOSTS_ONLY", "Ghosts Only", "Show only ghost reflections"),
            ("GHOST_AIM", "Ghost Aim", "Light-aimed ghost diagnostic"),
        ],
        default="NORMAL",
        update=_on_property_change,
    )


def _is_using_physical_lens(cam):
    return (
        cam.type == "CUSTOM"
        and cam.custom_mode == "INTERNAL"
        and cam.custom_shader is not None
        and cam.custom_shader.name.startswith(_TEXT_BLOCK_PREFIX)
    )


class CAMERA_OT_apply_physical_lens(bpy.types.Operator):
    bl_idname = "camera.apply_physical_lens"
    bl_label = "Enable Physical Lens"
    bl_description = "Set this camera to use the physical lens shader"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        # リンクされたカメラデータへの編集はライブラリ側に保存されないため無効化する
        return context.object is not None and context.object.type == "CAMERA" and context.object.data.library is None

    def execute(self, context):
        cam = context.object.data
        cam.type = "CUSTOM"
        cam.custom_mode = "INTERNAL"
        _build_camera_shader(cam)
        lens = _get_lens(cam.physical_camera)
        _sync_focal_length(cam, lens)
        # 無効化中はライトを追従させていないため、ゴースト/回折が ON のまま再有効化したときに最新のライトを渡す
        _update_camera_lights(cam, context.scene)
        return {"FINISHED"}


class CAMERA_OT_disable_physical_lens(bpy.types.Operator):
    bl_idname = "camera.disable_physical_lens"
    bl_label = "Disable Physical Lens"
    bl_description = "Revert to a standard perspective camera"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (
            context.object is not None
            and context.object.type == "CAMERA"
            and context.object.data.library is None
            and _is_using_physical_lens(context.object.data)
        )

    def execute(self, context):
        cam = context.object.data
        cam.type = "PERSP"
        return {"FINISHED"}


def _cycles_device_warning(scene):
    """Return a warning message when the Cycles device cannot run OSL camera shaders, or None."""
    settings = getattr(scene, "cycles", None)
    if settings is None:
        return None
    # CPU は OSL カメラを動かせるため警告しない
    if getattr(settings, "device", "CPU") != "GPU":
        return None
    # compute_device_type の格納場所はバージョンで異なり得るため scene 側と preferences 側の両方を試し、取れないときは表示しない
    compute_type = getattr(settings, "compute_device_type", None)
    if not compute_type:
        try:
            compute_type = bpy.context.preferences.addons["cycles"].preferences.compute_device_type
        except (KeyError, AttributeError):
            return None
    if not compute_type or compute_type == "OPTIX":
        return None
    return f"OSL camera shaders need CPU or OptiX (GPU compute device is {compute_type})"


def _stored_light_count(cam):
    """Number of scene lights currently passed to the camera's shader parameters."""
    try:
        return int(cam.cycles_custom.get(scene_lights.LIGHT_COUNT_PARAM, 0))
    except AttributeError:
        return 0


def _draw_environment_warnings(layout, context, cam, props):
    """Show warnings when the environment prevents the physical lens from rendering."""
    scene = context.scene
    if scene is None or scene.render.engine != "CYCLES":
        layout.label(text="Enable the Cycles render engine", icon="ERROR")

    device_warning = _cycles_device_warning(scene) if scene is not None else None
    if device_warning is not None:
        layout.label(text=device_warning, icon="ERROR")

    error = _compile_errors.get(cam.name)
    if error is not None:
        # OSL のエラーメッセージは複数行になり得るため 1 行目だけ表示する
        layout.label(text=f"Shader compile error: {error.splitlines()[0]}", icon="ERROR")

    if props.lens_ghosts or props.diffraction:
        count = _stored_light_count(cam)
        if count == 0:
            layout.label(text="No lights are visible to camera rays (0 passed to the shader)", icon="ERROR")
            layout.label(
                text="Enable Object Properties > Visibility > Ray Visibility > Camera on the lights",
                icon="INFO",
            )
        else:
            layout.label(text=f"Scene lights passed to the shader: {count}", icon="LIGHT")


class CAMERA_PT_physical_lens(bpy.types.Panel):
    bl_label = "Physical Lens"
    bl_idname = "CAMERA_PT_physical_lens"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "data"

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == "CAMERA"

    def draw(self, context):
        layout = self.layout
        cam = context.object.data
        props = cam.physical_camera
        # リンクされたカメラデータへの編集はライブラリ側に保存されないため操作系を無効化する
        editable = cam.library is None

        if not _is_using_physical_lens(cam):
            operator = layout.operator("camera.apply_physical_lens", icon="CAMERA_DATA")
            operator.enabled = editable
            return

        _draw_environment_warnings(layout, context, cam, props)

        layout.use_property_split = True
        layout.use_property_decorate = False

        column = layout.column()
        column.enabled = editable

        column.prop(props, "lens")

        lens = _get_lens(props)

        if lens is not None:
            column.prop(props, "fstop", text=f"f-stop (min f/{min_fstop(lens):.1f})")
        else:
            column.prop(props, "fstop")

        column.separator()
        column.prop(props, "aperture_blades")
        column.prop(props, "blade_rotation")

        column.separator()
        column.prop(props, "chromatic_aberration")
        column.prop(props, "lens_ghosts")
        if props.lens_ghosts:
            column.prop(props, "ghost_intensity")
        column.prop(props, "diffraction")
        column.prop(props, "debug_mode")


class CAMERA_PT_physical_lens_diagram(bpy.types.Panel):
    bl_label = "Lens Diagram"
    bl_idname = "CAMERA_PT_physical_lens_diagram"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "data"
    bl_parent_id = "CAMERA_PT_physical_lens"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return (
            context.object is not None
            and context.object.type == "CAMERA"
            and _is_using_physical_lens(context.object.data)
            and diagram.has_previews()
        )

    def draw(self, context):
        props = context.object.data.physical_camera
        icon_id = diagram.get_icon_id(props.lens)
        if icon_id:
            row = self.layout.row()
            row.alignment = "CENTER"
            row.template_icon(icon_value=icon_id, scale=8.0)


_classes = (
    PhysicalCameraProperties,
    CAMERA_OT_apply_physical_lens,
    CAMERA_OT_disable_physical_lens,
    CAMERA_PT_physical_lens,
    CAMERA_PT_physical_lens_diagram,
)


def _draw_object_context_menu(self, context):
    if context.object is not None and context.object.type == "CAMERA" and _is_using_physical_lens(context.object.data):
        self.layout.separator()
        self.layout.operator("camera.disable_physical_lens")


def _camera_needs_lights(cam):
    """Check if a physical lens camera has ghosts or diffraction enabled."""
    if not _is_using_physical_lens(cam):
        return False
    props = cam.physical_camera
    return props.lens_ghosts or props.diffraction


def _apply_scene_lights(cam, lights):
    """Write lights to the camera's shader parameters. Returns True if they changed."""
    try:
        custom = cam.cycles_custom
    except AttributeError:
        return False

    values = scene_lights.light_param_values(lights)
    if not scene_lights.light_params_differ(custom, values):
        return False
    for name, value in values.items():
        custom[name] = value
    # ハンドラーから書き換えた IDProperty は依存グラフに通知されず、ビューポートのレンダリングに反映されないため
    cam.update_tag()
    return True


def _scene_cameras(scene):
    """Camera datablocks used by a scene (its active camera and linked camera objects)."""
    cameras = []
    objects = list(scene.objects)
    if scene.camera is not None:
        objects.append(scene.camera)
    for obj in objects:
        if obj.type == "CAMERA" and obj.data not in cameras:
            cameras.append(obj.data)
    return cameras


def _owner_scene(cam, preferred):
    """Pick the scene whose lights a camera should use, preferring ``preferred`` if it uses the camera."""
    if preferred is not None and cam in _scene_cameras(preferred):
        return preferred
    return next((scene for scene in bpy.data.scenes if cam in _scene_cameras(scene)), None)


def _update_camera_lights(cam, preferred_scene):
    scene = _owner_scene(cam, preferred_scene)
    if scene is not None:
        _update_scene_lights(scene, [cam])


def _update_scene_lights(scene, cameras=None):
    """Collect lights from scene and pass them to its cameras that use ghosts or diffraction."""
    global _updating_lights

    # ライトはカメラデータごとに 1 組しか持てないため、そのカメラを使うシーンのライトだけを渡し別シーンのライトを混ぜない
    # ゴースト/回折が OFF のカメラはライトを読まないため、パラメーターを書き換えて Cycles を再同期させない
    if cameras is None:
        cameras = _scene_cameras(scene)
    cameras = [cam for cam in cameras if _camera_needs_lights(cam)]
    if not cameras:
        return
    lights = scene_lights.collect_lights(scene)

    _updating_lights = True
    try:
        for cam in cameras:
            _apply_scene_lights(cam, lights)
    finally:
        _updating_lights = False


def _migrate_legacy_text_block(cam):
    """Migrate cameras using the old shared text datablock name."""
    if (
        cam.type == "CUSTOM"
        and cam.custom_mode == "INTERNAL"
        and cam.custom_shader is not None
        and cam.custom_shader.name == "Physical Lens OSL"
    ):
        _build_camera_shader(cam)


@persistent
def _on_load_post(_):
    """Rebuild shaders for all physical lens cameras after file load."""
    # カメラ削除で参照されなくなったテキストブロックはファイルに保存され続けるため、読込時に掃除する
    _remove_orphan_text_blocks()

    # Migrate cameras from the old shared text datablock
    for cam in bpy.data.cameras:
        _migrate_legacy_text_block(cam)

    has_any = False
    for cam in bpy.data.cameras:
        if _is_using_physical_lens(cam):
            has_any = True
            break

    if not has_any:
        return

    # 旧バージョンで保存したファイルはライトをソースに埋め込んだシェーダーのため、作り直してパラメーター方式へ移行する
    for cam in bpy.data.cameras:
        if not _is_using_physical_lens(cam):
            continue
        # リンクされたカメラやテキストはライブラリ側にのみ存在し編集が保存されないため書き換えない
        if cam.library is not None or (cam.custom_shader is not None and cam.custom_shader.library is not None):
            continue
        lens = _get_lens(cam.physical_camera)
        if lens is None:
            continue
        osl_source = codegen.build_camera_osl(_osl_template, lens)
        if cam.custom_shader is not None and cam.custom_shader.as_string() == osl_source:
            # 生成ソースが一致するなら書き込みも oslc コンパイルも不要で、ファイルを開くたびに全カメラを再コンパイルしない
            continue
        _build_camera_shader(cam, osl_source)

    # 複数シーンで共有するカメラは開いているシーンのライトを最後に書いて優先する (レンダリング時は render_pre で対象シーンに合わせる)
    current = bpy.context.scene
    for scene in bpy.data.scenes:
        if scene != current:
            _update_scene_lights(scene)
    if current is not None:
        _update_scene_lights(current)


@persistent
def _on_render_pre(scene):
    """Pass the rendered scene's lights to the camera shader parameters before each frame."""
    # bpy.context.scene はレンダリング中のシーンとは限らない (別シーンのレンダリングやコマンドラインの -S) ため引数のシーンを使う
    if scene is not None:
        _update_scene_lights(scene)


@persistent
def _on_depsgraph_update(scene, depsgraph):
    """Re-inject scene lights when lights or emissive meshes change."""
    if _updating_lights:
        return

    # ライトを必要とするカメラが無いシーンでは updates の走査が無駄になるため早期に抜ける
    if not any(_camera_needs_lights(cam) for cam in _scene_cameras(scene)):
        return

    needs_update = False
    for update in depsgraph.updates:
        uid = update.id
        if isinstance(uid, bpy.types.Object):
            if uid.type == "LIGHT" and (update.is_updated_transform or update.is_updated_shading):
                needs_update = True
                break
            if uid.type == "MESH" and (update.is_updated_transform or update.is_updated_shading):
                needs_update = True
                break
        elif isinstance(uid, bpy.types.Light):
            needs_update = True
            break
        elif isinstance(uid, bpy.types.Material):
            needs_update = True
            break

    if needs_update:
        _update_scene_lights(scene)


@persistent
def _on_frame_change(scene, depsgraph):
    """Update lights on frame change for animated lights."""
    _update_scene_lights(scene)


def register():
    global _lens_registry, _lens_items, _lens_map, _osl_template

    addon_dir = Path(__file__).parent
    template_path = addon_dir / "lens_camera.osl.template"
    lens_dir = addon_dir / "lenses"

    _osl_template, lenses = codegen.load_osl_template(template_path, lens_dir)
    _lens_registry = lenses
    _lens_items = [(lens["filename_stem"], lens["name"], "") for lens in lenses]
    _lens_map = {lens["filename_stem"]: lens for lens in lenses}

    PhysicalCameraProperties.__annotations__["lens"] = EnumProperty(
        name="Lens",
        items=_lens_items,
        update=_on_lens_change,
    )

    diagram.load_previews(_lens_registry)

    for cls in _classes:
        bpy.utils.register_class(cls)

    bpy.types.Camera.physical_camera = bpy.props.PointerProperty(type=PhysicalCameraProperties)
    bpy.types.OUTLINER_MT_object.append(_draw_object_context_menu)
    bpy.app.handlers.load_post.append(_on_load_post)
    bpy.app.handlers.render_pre.append(_on_render_pre)
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph_update)
    bpy.app.handlers.frame_change_post.append(_on_frame_change)


def unregister():
    diagram.cleanup()
    _compile_errors.clear()
    bpy.app.handlers.frame_change_post.remove(_on_frame_change)
    bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph_update)
    bpy.app.handlers.render_pre.remove(_on_render_pre)
    bpy.app.handlers.load_post.remove(_on_load_post)
    bpy.types.OUTLINER_MT_object.remove(_draw_object_context_menu)
    del bpy.types.Camera.physical_camera

    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)

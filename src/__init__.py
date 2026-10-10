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

_TEXT_BLOCK_NAME = "Physical Lens OSL"

_lens_registry: list[dict] = []
_lens_items: list[tuple] = []
_lens_index_map: dict[str, int] = {}
_osl_source: str = ""
_osl_source_base: str = ""
_updating_lights: bool = False
_cached_light_key = None


def _lens_index(props):
    idx = _lens_index_map.get(props.lens)
    if idx is not None:
        return idx
    if props.lens.isdigit():
        i = int(props.lens)
        if 0 <= i < len(_lens_registry):
            return i
    return 0


def _get_or_create_text_block():
    """Get or create the text datablock containing the generated OSL shader."""
    if _TEXT_BLOCK_NAME in bpy.data.texts:
        text = bpy.data.texts[_TEXT_BLOCK_NAME]
    else:
        text = bpy.data.texts.new(_TEXT_BLOCK_NAME)
    text.clear()
    text.write(_osl_source)
    return text


def sync_to_cycles(cam):
    """Push all PhysicalCameraProperties to cam.cycles_custom."""
    try:
        custom = cam.cycles_custom
    except AttributeError:
        return

    props = cam.physical_camera
    lens_index = _lens_index(props)

    custom["lens_type"] = lens_index
    custom["aperture_blades"] = props.aperture_blades
    from math import degrees
    custom["blade_rotation"] = degrees(props.blade_rotation)
    custom["chromatic_aberration"] = 1 if props.chromatic_aberration else 0
    custom["lens_ghosts"] = 1 if props.lens_ghosts else 0
    custom["ghost_intensity"] = props.ghost_intensity
    custom["diffraction"] = 1 if props.diffraction else 0

    debug_map = {"NORMAL": 0.0, "PINHOLE": 1.0, "DIAGNOSTIC": 2.0, "EXIT_DIR": 3.0, "GHOSTS_ONLY": 4.0, "GHOST_AIM": 5.0}
    custom["debug_mode"] = debug_map[props.debug_mode]

    if lens_index < len(_lens_registry):
        max_fstop = _lens_registry[lens_index]["max_fstop"]
        custom["aperture_scale"] = min(max_fstop / props.fstop, 1.0)
    else:
        custom["aperture_scale"] = 1.0


def _sync_focal_length(cam, lens_index):
    if lens_index < len(_lens_registry):
        cam.lens = _lens_registry[lens_index]["focal_length"]


def _on_lens_change(self, context):
    lens_index = _lens_index(self)
    if lens_index < len(_lens_registry):
        max_fstop = _lens_registry[lens_index]["max_fstop"]
        if self.fstop < max_fstop:
            self.fstop = max_fstop
    cam = context.object.data if context.object else None
    if cam:
        _sync_focal_length(cam, lens_index)
        sync_to_cycles(cam)


def _on_property_change(self, context):
    cam = context.object.data if context.object else None
    if cam:
        sync_to_cycles(cam)


def _on_light_feature_toggle(self, context):
    global _cached_light_key
    _cached_light_key = None
    cam = context.object.data if context.object else None
    if cam:
        sync_to_cycles(cam)
    if context.scene:
        _update_scene_lights(context.scene)


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
        update=_on_property_change,
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
        subtype='ANGLE',
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
        cam.type == 'CUSTOM'
        and cam.custom_mode == 'INTERNAL'
        and cam.custom_shader is not None
        and cam.custom_shader.name == _TEXT_BLOCK_NAME
    )


class CAMERA_OT_apply_physical_lens(bpy.types.Operator):
    bl_idname = "camera.apply_physical_lens"
    bl_label = "Enable Physical Lens"
    bl_description = "Set this camera to use the physical lens shader"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (
            context.object is not None
            and context.object.type == 'CAMERA'
        )

    def execute(self, context):
        cam = context.object.data
        text = _get_or_create_text_block()
        cam.type = 'CUSTOM'
        cam.custom_mode = 'INTERNAL'
        cam.custom_shader = text
        lens_index = _lens_index(cam.physical_camera)
        _sync_focal_length(cam, lens_index)
        sync_to_cycles(cam)
        return {'FINISHED'}


class CAMERA_OT_disable_physical_lens(bpy.types.Operator):
    bl_idname = "camera.disable_physical_lens"
    bl_label = "Disable Physical Lens"
    bl_description = "Revert to a standard perspective camera"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (
            context.object is not None
            and context.object.type == 'CAMERA'
            and _is_using_physical_lens(context.object.data)
        )

    def execute(self, context):
        cam = context.object.data
        cam.type = 'PERSP'
        return {'FINISHED'}


class CAMERA_PT_physical_lens(bpy.types.Panel):
    bl_label = "Physical Lens"
    bl_idname = "CAMERA_PT_physical_lens"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "data"

    @classmethod
    def poll(cls, context):
        return (
            context.object is not None
            and context.object.type == 'CAMERA'
        )

    def draw(self, context):
        layout = self.layout
        cam = context.object.data
        props = cam.physical_camera

        if not _is_using_physical_lens(cam):
            layout.operator("camera.apply_physical_lens", icon='CAMERA_DATA')
            return

        layout.use_property_split = True
        layout.use_property_decorate = False

        layout.prop(props, "lens")

        lens_index = _lens_index(props)

        if lens_index < len(_lens_registry):
            max_fstop = _lens_registry[lens_index]["max_fstop"]
            layout.prop(props, "fstop", text=f"f-stop (min f/{max_fstop})")
        else:
            layout.prop(props, "fstop")

        layout.separator()
        layout.prop(props, "aperture_blades")
        layout.prop(props, "blade_rotation")

        layout.separator()
        layout.prop(props, "chromatic_aberration")
        layout.prop(props, "lens_ghosts")
        if props.lens_ghosts:
            layout.prop(props, "ghost_intensity")
        layout.prop(props, "diffraction")
        layout.prop(props, "debug_mode")


class CAMERA_PT_physical_lens_diagram(bpy.types.Panel):
    bl_label = "Lens Diagram"
    bl_idname = "CAMERA_PT_physical_lens_diagram"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "data"
    bl_parent_id = "CAMERA_PT_physical_lens"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return (
            context.object is not None
            and context.object.type == 'CAMERA'
            and _is_using_physical_lens(context.object.data)
            and diagram.has_previews()
        )

    def draw(self, context):
        props = context.object.data.physical_camera
        icon_id = diagram.get_icon_id(props.lens)
        if icon_id:
            row = self.layout.row()
            row.alignment = 'CENTER'
            row.template_icon(icon_value=icon_id, scale=8.0)


_classes = (
    PhysicalCameraProperties,
    CAMERA_OT_apply_physical_lens,
    CAMERA_OT_disable_physical_lens,
    CAMERA_PT_physical_lens,
    CAMERA_PT_physical_lens_diagram,
)


def _draw_object_context_menu(self, context):
    if (
        context.object is not None
        and context.object.type == 'CAMERA'
        and _is_using_physical_lens(context.object.data)
    ):
        self.layout.separator()
        self.layout.operator("camera.disable_physical_lens")


def _lights_key(lights):
    return tuple(
        (lt['type'], lt['pos'], lt['dir'], lt['intensity'], lt['radius'])
        for lt in lights
    )


def _update_scene_lights(scene):
    """Collect lights from scene and regenerate the shader text block."""
    global _osl_source, _updating_lights, _cached_light_key
    cam_obj = scene.camera
    if cam_obj is None or not _is_using_physical_lens(cam_obj.data):
        lights = []
    elif not (cam_obj.data.physical_camera.lens_ghosts
              or cam_obj.data.physical_camera.diffraction):
        lights = []
    else:
        lights = scene_lights.collect_lights(scene)

    key = _lights_key(lights)
    if key == _cached_light_key:
        return
    _cached_light_key = key

    _osl_source = codegen.inject_scene_lights(_osl_source_base, lights)
    _updating_lights = True
    try:
        text = _get_or_create_text_block()
        for cam in bpy.data.cameras:
            if _is_using_physical_lens(cam):
                cam.custom_shader = text
                sync_to_cycles(cam)
    finally:
        _updating_lights = False


@persistent
def _on_load_post(_):
    """Update the shader text block and reassign to cameras after file load."""
    global _cached_light_key
    _cached_light_key = None
    if _TEXT_BLOCK_NAME not in bpy.data.texts:
        return
    _update_scene_lights(bpy.context.scene)


@persistent
def _on_render_pre(_):
    """Inject scene light positions into the shader before each frame."""
    _update_scene_lights(bpy.context.scene)


@persistent
def _on_depsgraph_update(scene, depsgraph):
    """Re-inject scene lights when lights or emissive meshes change."""
    if _updating_lights:
        return

    needs_update = False
    for update in depsgraph.updates:
        uid = update.id
        if isinstance(uid, bpy.types.Object):
            if uid.type == 'LIGHT' and (update.is_updated_transform
                                        or update.is_updated_shading):
                needs_update = True
                break
            if uid.type == 'MESH' and (update.is_updated_transform
                                       or update.is_updated_shading):
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
    global _lens_registry, _lens_items, _lens_index_map, _osl_source
    global _osl_source_base

    addon_dir = Path(__file__).parent
    template_path = addon_dir / "lens_camera.osl.template"
    lens_dir = addon_dir / "lenses"

    osl_source_base, lenses = codegen.generate_osl(template_path, lens_dir)
    _osl_source_base = osl_source_base
    _osl_source = codegen.inject_scene_lights(_osl_source_base)
    _lens_registry = lenses
    _lens_items = [
        (lens["filename_stem"], lens["name"], "") for lens in lenses
    ]
    _lens_index_map = {
        lens["filename_stem"]: i for i, lens in enumerate(lenses)
    }

    PhysicalCameraProperties.__annotations__["lens"] = EnumProperty(
        name="Lens",
        items=_lens_items,
        update=_on_lens_change,
    )

    diagram.load_previews(_lens_registry)

    for cls in _classes:
        bpy.utils.register_class(cls)

    bpy.types.Camera.physical_camera = bpy.props.PointerProperty(
        type=PhysicalCameraProperties
    )
    bpy.types.OUTLINER_MT_object.append(_draw_object_context_menu)
    bpy.app.handlers.load_post.append(_on_load_post)
    bpy.app.handlers.render_pre.append(_on_render_pre)
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph_update)
    bpy.app.handlers.frame_change_post.append(_on_frame_change)


def unregister():
    diagram.cleanup()
    bpy.app.handlers.frame_change_post.remove(_on_frame_change)
    bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph_update)
    bpy.app.handlers.render_pre.remove(_on_render_pre)
    bpy.app.handlers.load_post.remove(_on_load_post)
    bpy.types.OUTLINER_MT_object.remove(_draw_object_context_menu)
    del bpy.types.Camera.physical_camera

    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)

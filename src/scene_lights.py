"""Collect scene lights and generate OSL loader function.

Light positions and directions are stored in world space. The OSL shader
transforms them to camera space at render time via transform("world",
"camera", P), so camera movement does not trigger shader recompilation.
The data is passed as camera shader parameters, so light changes do not
trigger recompilation either.
"""

MAX_LIGHTS = 16


def collect_lights(scene):
    """Collect lights from the scene in world space.

    Finds both Blender LIGHT objects and mesh objects with emissive
    materials (Emission shader or Principled BSDF with emission).

    Returns a list of dicts with keys: type (0=positional, 1=sun),
    pos (world meters), dir (world-space toward-source for suns),
    intensity, radius (mm).
    """
    lights = []

    for obj in _render_objects(scene):
        if obj.type == "LIGHT" and obj.visible_camera:
            _collect_light_object(obj, lights)
        elif obj.type == "MESH":
            _collect_emissive_mesh(obj, lights)

    lights.sort(key=lambda light: light["intensity"], reverse=True)
    return lights[:MAX_LIGHTS]


def _render_objects(scene):
    """Objects rendered by the scene's view layers (excluded and render-disabled ones are skipped)."""
    # scene.objects は除外したコレクションやレンダリング無効のオブジェクトも含み、写らないライトにゴーストを向けてしまうため
    view_layers = [vl for vl in scene.view_layers if vl.use] or list(scene.view_layers)
    found = {}
    for view_layer in view_layers:
        stack = [view_layer.layer_collection]
        while stack:
            layer_collection = stack.pop()
            if layer_collection.exclude or layer_collection.collection.hide_render:
                continue
            for obj in layer_collection.collection.objects:
                if not obj.hide_render:
                    found.setdefault(obj.name_full, obj)
            stack.extend(layer_collection.children)
    return list(found.values())


def _collect_light_object(obj, lights):
    light = obj.data

    if light.type == "SUN":
        from mathutils import Vector

        emission_dir = obj.matrix_world.to_3x3() @ Vector((0, 0, -1))
        # Negate to get "toward source" direction.
        # Stored in world space; the shader transforms to camera space.
        lights.append(
            {
                "type": 1,
                "pos": (0.0, 0.0, 0.0),
                "dir": (-emission_dir.x, -emission_dir.y, -emission_dir.z),
                "intensity": light.energy * _luminance(light.color),
                "radius": 0.0,
            }
        )
    else:
        # POINT, SPOT, AREA — world-space position in meters
        pos = obj.matrix_world.translation
        if light.type == "AREA":
            radius = max(light.size, getattr(light, "size_y", light.size)) * 1000
        else:
            radius = light.shadow_soft_size * 1000
        lights.append(
            {
                "type": 0,
                "pos": (pos.x, pos.y, pos.z),
                "dir": (0.0, 0.0, 0.0),
                "intensity": light.energy * _luminance(light.color),
                "radius": radius,
            }
        )


def _collect_emissive_mesh(obj, lights):
    """Check mesh materials for emission and add as a positional light."""
    emission = _get_mesh_emission(obj)
    if emission is None:
        return

    strength, color_r, color_g, color_b = emission
    intensity = strength * _luminance((color_r, color_g, color_b))
    if intensity <= 0.0:
        return

    pos = obj.matrix_world.translation
    dims = obj.dimensions
    radius = max(dims.x, dims.y, dims.z) * 0.5 * 1000

    lights.append(
        {
            "type": 0,
            "pos": (pos.x, pos.y, pos.z),
            "dir": (0.0, 0.0, 0.0),
            "intensity": intensity,
            "radius": radius,
        }
    )


def _get_mesh_emission(obj):
    """Extract emission (strength, r, g, b) from an object's materials.

    Checks for Emission shader nodes and Principled BSDF emission.
    Returns the brightest emission found, or None.
    """
    best = None
    best_intensity = 0.0

    for slot in obj.material_slots:
        mat = slot.material
        # use_nodes は非推奨のため node_tree の有無で判定する (ノードを使わないマテリアルは node_tree が空)
        if mat is None or mat.node_tree is None:
            continue

        for node in mat.node_tree.nodes:
            strength = 0.0
            color = (1.0, 1.0, 1.0)

            if node.type == "EMISSION":
                color = _socket_default(node.inputs["Color"], (1.0, 1.0, 1.0))
                strength = _socket_default(node.inputs["Strength"], 1.0)

            elif node.type == "BSDF_PRINCIPLED":
                strength = _socket_default(node.inputs.get("Emission Strength"), 0.0)
                if strength <= 0.0:
                    continue
                color = _socket_default(node.inputs.get("Emission Color"), (1.0, 1.0, 1.0))

            else:
                continue

            intensity = strength * _luminance(color)
            if intensity > best_intensity:
                best_intensity = intensity
                best = (strength, color[0], color[1], color[2])

    return best


def _socket_default(socket, fallback):
    """Get a socket's default value, or fallback if socket is None."""
    if socket is None:
        return fallback
    val = socket.default_value
    if hasattr(val, "__len__"):
        return tuple(val)[: len(fallback)] if hasattr(fallback, "__len__") else val
    return float(val)


def _luminance(color):
    return 0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]


# ライトはソースに埋め込まずカメラのシェーダーパラメーター (cam.cycles_custom) で渡す
# ソースに埋め込むとライトを動かすたびに OSL の再コンパイルが走るため
LIGHT_COUNT_PARAM = "scene_light_count"


def light_param_names(index):
    """Return the (geometry, info) shader parameter names for light slot ``index``."""
    return f"scene_light_{index}", f"scene_light_{index}_info"


def generate_scene_light_defines():
    """Generate the light array size #defines shared by the shader functions."""
    return "\n".join(
        [
            f"#define MAX_LIGHTS {MAX_LIGHTS}",
            f"#define MAX_LIGHTS_3 {MAX_LIGHTS * 3}",
        ]
    )


def generate_light_params():
    """Generate the shader parameter declarations that carry scene lights."""
    lines = [
        f"int {LIGHT_COUNT_PARAM} = 0",
        '    [[ string description = "Set automatically by Physical Camera from the scene lights." ]],',
    ]
    for i in range(MAX_LIGHTS):
        geom, info = light_param_names(i)
        lines.append(f"vector {geom} = 0")
        lines.append('    [[ string description = "World position (m), or toward-source direction for sun." ]],')
        lines.append(f"vector {info} = 0")
        lines.append('    [[ string description = "Type (0=positional, 1=sun), intensity, radius (mm)." ]],')
    return "\n".join(lines)


def generate_load_scene_lights():
    """Generate the shader-body statements that unpack the light parameters into arrays."""
    lines = [f"num_lights = clamp({LIGHT_COUNT_PARAM}, 0, MAX_LIGHTS);"]
    for i in range(MAX_LIGHTS):
        geom, info = light_param_names(i)
        lines.append(
            f"unpack_scene_light({i}, {geom}, {info}, light_types, light_pos, light_dir, light_intensity, light_radius);"
        )
    return "\n".join(lines)


def light_param_values(lights):
    """Map collected lights to shader parameter values (unused slots are zero)."""
    values = {LIGHT_COUNT_PARAM: min(len(lights), MAX_LIGHTS)}
    for i in range(MAX_LIGHTS):
        geom, info = light_param_names(i)
        if i < len(lights):
            lt = lights[i]
            vec = lt["dir"] if lt["type"] == 1 else lt["pos"]
            values[geom] = tuple(float(v) for v in vec)
            values[info] = (float(lt["type"]), float(lt["intensity"]), float(lt["radius"]))
        else:
            values[geom] = (0.0, 0.0, 0.0)
            values[info] = (0.0, 0.0, 0.0)
    return values


def light_params_differ(current, values):
    """Return True if any value in ``values`` differs from the stored ``current`` mapping."""
    from math import isclose

    for name, value in values.items():
        stored = current.get(name)
        if stored is None:
            return True
        if isinstance(value, tuple):
            # IDProperty の配列は単精度で丸められることがあるため、厳密比較だと毎回書き込みが発生する
            if len(stored) != len(value):
                return True
            if any(not isclose(a, b, rel_tol=1e-6, abs_tol=1e-9) for a, b in zip(stored, value, strict=True)):
                return True
        elif stored != value:
            return True
    return False

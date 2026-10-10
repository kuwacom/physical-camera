"""Collect scene lights and generate OSL loader function.

Light positions and directions are stored in world space. The OSL shader
transforms them to camera space at render time via transform("world",
"camera", P), so camera movement does not trigger shader recompilation.
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

    for obj in scene.objects:
        if obj.type == 'LIGHT' and obj.visible_camera:
            _collect_light_object(obj, lights)
        elif obj.type == 'MESH':
            _collect_emissive_mesh(obj, lights)

    lights.sort(key=lambda l: l["intensity"], reverse=True)
    return lights[:MAX_LIGHTS]


def _collect_light_object(obj, lights):
    light = obj.data

    if light.type == 'SUN':
        from mathutils import Vector
        emission_dir = obj.matrix_world.to_3x3() @ Vector((0, 0, -1))
        # Negate to get "toward source" direction.
        # Stored in world space; the shader transforms to camera space.
        lights.append({
            "type": 1,
            "pos": (0.0, 0.0, 0.0),
            "dir": (-emission_dir.x, -emission_dir.y, -emission_dir.z),
            "intensity": light.energy * _luminance(light.color),
            "radius": 0.0,
        })
    else:
        # POINT, SPOT, AREA — world-space position in meters
        pos = obj.matrix_world.translation
        if light.type == 'AREA':
            radius = max(light.size, getattr(light, 'size_y', light.size)) * 1000
        else:
            radius = light.shadow_soft_size * 1000
        lights.append({
            "type": 0,
            "pos": (pos.x, pos.y, pos.z),
            "dir": (0.0, 0.0, 0.0),
            "intensity": light.energy * _luminance(light.color),
            "radius": radius,
        })


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

    lights.append({
        "type": 0,
        "pos": (pos.x, pos.y, pos.z),
        "dir": (0.0, 0.0, 0.0),
        "intensity": intensity,
        "radius": radius,
    })


def _get_mesh_emission(obj):
    """Extract emission (strength, r, g, b) from an object's materials.

    Checks for Emission shader nodes and Principled BSDF emission.
    Returns the brightest emission found, or None.
    """
    best = None
    best_intensity = 0.0

    for slot in obj.material_slots:
        mat = slot.material
        if mat is None or not mat.use_nodes:
            continue

        for node in mat.node_tree.nodes:
            strength = 0.0
            color = (1.0, 1.0, 1.0)

            if node.type == 'EMISSION':
                color = _socket_default(node.inputs['Color'], (1.0, 1.0, 1.0))
                strength = _socket_default(node.inputs['Strength'], 1.0)

            elif node.type == 'BSDF_PRINCIPLED':
                strength = _socket_default(
                    node.inputs.get('Emission Strength'), 0.0)
                if strength <= 0.0:
                    continue
                color = _socket_default(
                    node.inputs.get('Emission Color'), (1.0, 1.0, 1.0))

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
    if hasattr(val, '__len__'):
        return tuple(val)[:len(fallback)] if hasattr(fallback, '__len__') else val
    return float(val)


def _luminance(color):
    return 0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]


def generate_load_scene_lights(lights):
    """Generate an OSL function body that loads scene light data."""
    max3 = MAX_LIGHTS * 3
    lines = [
        f"#define MAX_LIGHTS {MAX_LIGHTS}",
        f"#define MAX_LIGHTS_3 {max3}",
        "",
        "void load_scene_lights(",
        "    output int num_lights,",
        "    output int light_types[MAX_LIGHTS],",
        "    output float light_pos[MAX_LIGHTS_3],",
        "    output float light_dir[MAX_LIGHTS_3],",
        "    output float light_intensity[MAX_LIGHTS],",
        "    output float light_radius[MAX_LIGHTS])",
        "{",
        f"    num_lights = {len(lights)};",
    ]

    for i, lt in enumerate(lights):
        lines.append(f"    light_types[{i}] = {lt['type']};")
        lines.append(
            f"    light_pos[{i*3}] = {lt['pos'][0]};  "
            f"light_pos[{i*3+1}] = {lt['pos'][1]};  "
            f"light_pos[{i*3+2}] = {lt['pos'][2]};"
        )
        lines.append(
            f"    light_dir[{i*3}] = {lt['dir'][0]};  "
            f"light_dir[{i*3+1}] = {lt['dir'][1]};  "
            f"light_dir[{i*3+2}] = {lt['dir'][2]};"
        )
        lines.append(f"    light_intensity[{i}] = {lt['intensity']};")
        lines.append(f"    light_radius[{i}] = {lt['radius']};")

    lines.append("}")
    return "\n".join(lines)

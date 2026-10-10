"""Generate lens diagram PNGs from TOML lens prescriptions using Pillow."""
# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow"]
# ///

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

_SRC_DIR = Path(__file__).resolve().parent.parent / "src"

# Add src/ to sys.path so we can import lenses directly,
# bypassing src/__init__.py which requires bpy
sys.path.insert(0, str(_SRC_DIR))
from lenses import load_lenses

_ICON_SIZE = 256
_SUPERSAMPLE = 2
_RENDER_SIZE = _ICON_SIZE * _SUPERSAMPLE
_PADDING = 24 * _SUPERSAMPLE
_BG_COLOR = (0, 0, 0, 0)
_GLASS_FILL = (128, 179, 230, 89)
_SURFACE_LINE = (217, 217, 217, 255)
_CEMENTED_LINE = (217, 217, 217, 128)
_STOP_COLOR = (217, 217, 217, 255)
_AXIS_COLOR = (255, 255, 255, 140)
_RAY_COLOR = (255, 190, 100, 160)


def _is_stop(s):
    return s.get("type") == "stop"


def _diagram_radius(s):
    """Effective radius for Y-Z cross-section diagram.

    Cylindrical_x surfaces have curvature only in X, so they appear flat
    in the Y-Z view. All other surfaces use their actual radius.
    """
    if s.get("type") == "cylindrical_x":
        return 0.0
    return s["radius"]


def _compute_vertex_positions(surfaces):
    positions = [0.0]
    for s in surfaces[:-1]:
        positions.append(positions[-1] + s["thickness"])
    return positions


def _find_elements(surfaces):
    elements = []
    start = None
    for i, s in enumerate(surfaces):
        if s["ior"] > 1.0:
            if start is None:
                start = i
        else:
            if start is not None:
                elements.append((start, i))
                start = None
    if start is not None:
        elements.append((start, len(surfaces) - 1))
    return elements


def _arc_x(vertex_x, radius, y):
    if radius == 0.0:
        return vertex_x
    center_x = vertex_x + radius
    r_sq = radius * radius
    y_sq = y * y
    if y_sq >= r_sq:
        return vertex_x
    return center_x - math.copysign(math.sqrt(r_sq - y_sq), radius)


def _arc_points(vertex_x, radius, semi_ap, to_px, steps=64):
    """Return list of pixel (x, y) tuples along an arc."""
    points = []
    for i in range(steps + 1):
        y = -semi_ap + (2 * semi_ap) * i / steps
        x = _arc_x(vertex_x, radius, y)
        points.append(to_px(x, y))
    return points


def _effective_semi_aperture(front_vx, front_r, front_ap, back_vx, back_r, back_ap):
    """Max semi-aperture where the front arc doesn't extend past the back arc."""
    max_ap = min(front_ap, back_ap)
    if _arc_x(front_vx, front_r, max_ap) <= _arc_x(back_vx, back_r, max_ap):
        return front_ap, back_ap
    # Binary search for the crossing height
    lo, hi = 0.0, max_ap
    for _ in range(50):
        mid = (lo + hi) * 0.5
        if _arc_x(front_vx, front_r, mid) <= _arc_x(back_vx, back_r, mid):
            lo = mid
        else:
            hi = mid
    return lo, lo


def _element_polygon(surfaces, positions, front_i, back_i, effective_aps, to_px):
    """Build a polygon outline for a glass element between two surfaces."""
    front_vx = positions[front_i]
    back_vx = positions[back_i]
    front_r = _diagram_radius(surfaces[front_i])
    back_r = _diagram_radius(surfaces[back_i])

    front_pts = _arc_points(front_vx, front_r, effective_aps[front_i], to_px)
    back_pts = _arc_points(back_vx, back_r, effective_aps[back_i], to_px)

    polygon = list(front_pts)
    polygon.append(back_pts[-1])
    polygon.extend(reversed(back_pts))
    polygon.append(front_pts[0])

    return polygon


def _draw_dashed_line(draw, x0, y0, x1, y1, color, width, dash=8, gap=6):
    dx = x1 - x0
    dy = y1 - y0
    length = math.sqrt(dx * dx + dy * dy)
    if length < 1:
        return
    nx, ny = dx / length, dy / length
    pos = 0.0
    while pos < length:
        end = min(pos + dash, length)
        draw.line(
            [(x0 + nx * pos, y0 + ny * pos), (x0 + nx * end, y0 + ny * end)],
            fill=color,
            width=width,
        )
        pos = end + gap


def _draw_arc(draw, vertex_x, radius, semi_ap, to_px, color, width):
    points = _arc_points(vertex_x, radius, semi_ap, to_px)
    draw.line(points, fill=color, width=width)


def _trace_ray(surfaces, positions, start_y):
    """Trace a parallel ray at given height through the lens. Returns point list or None."""
    ox = positions[0] - positions[-1] * 0.1
    oy = start_y
    dx, dy = 1.0, 0.0

    points = [(ox, oy)]
    n1 = 1.0

    for i, s in enumerate(surfaces):
        vx = positions[i]
        radius = _diagram_radius(s)
        semi_ap = s["aperture"] * 0.5
        n2 = s["ior"]

        if radius == 0.0:
            if abs(dx) < 1e-12:
                return None
            t = (vx - ox) / dx
            if t < 1e-6:
                n1 = n2
                continue
            hx = ox + t * dx
            hy = oy + t * dy
            if abs(hy) > semi_ap:
                return None
            points.append((hx, hy))
            ox, oy = hx, hy
            n1 = n2
            continue

        # Ray-sphere intersection
        cx = vx + radius
        ex, ey = ox - cx, oy
        a = dx * dx + dy * dy
        b = 2 * (ex * dx + ey * dy)
        c = ex * ex + ey * ey - radius * radius
        disc = b * b - 4 * a * c
        if disc < 0:
            return None
        sqrt_disc = math.sqrt(disc)
        t1 = (-b - sqrt_disc) / (2 * a)
        t2 = (-b + sqrt_disc) / (2 * a)

        # Pick hit closest to vertex
        h1x = ox + t1 * dx
        h2x = ox + t2 * dx
        if t1 > 1e-6 and (t2 <= 1e-6 or abs(h1x - vx) <= abs(h2x - vx)):
            t = t1
        elif t2 > 1e-6:
            t = t2
        else:
            return None

        hx = ox + t * dx
        hy = oy + t * dy
        if abs(hy) > semi_ap:
            return None
        points.append((hx, hy))

        # Surface normal facing against the ray
        inv_r = 1.0 / abs(radius)
        nx = (hx - cx) * inv_r
        ny = hy * inv_r
        if nx * dx + ny * dy > 0:
            nx, ny = -nx, -ny

        # Snell's law
        if n2 != n1:
            eta = n1 / n2
            cos_i = -(dx * nx + dy * ny)
            sin2_t = eta * eta * (1 - cos_i * cos_i)
            if sin2_t > 1.0:
                return None
            cos_t = math.sqrt(1 - sin2_t)
            dx = eta * dx + (eta * cos_i - cos_t) * nx
            dy = eta * dy + (eta * cos_i - cos_t) * ny
            length = math.sqrt(dx * dx + dy * dy)
            dx /= length
            dy /= length

        ox, oy = hx, hy
        n1 = n2

    # Extend past last surface
    extend = positions[-1] * 0.3
    points.append((ox + dx * extend, oy + dy * extend))
    return points


def _render_lens(surfaces):
    size = _RENDER_SIZE
    img = Image.new("RGBA", (size, size), _BG_COLOR)
    draw = ImageDraw.Draw(img)

    elements = _find_elements(surfaces)
    max_aperture = max(s["aperture"] for s in surfaces) * 0.5

    # Adaptive padding: reduce vertical padding for lenses where height
    # dominates width, so the front elements can extend closer to the
    # icon edges and the rear elements get more horizontal room.
    positions = _compute_vertex_positions(surfaces)
    total_length = positions[-1]
    aspect_ratio = (2 * max_aperture) / total_length if total_length > 0 else 1.0
    if aspect_ratio > 1.2:
        # Interpolate vertical padding from full to minimal as ratio grows
        t = min((aspect_ratio - 1.2) / 0.8, 1.0)
        min_padding = 4 * _SUPERSAMPLE
        v_padding = _PADDING + t * (min_padding - _PADDING)
    else:
        v_padding = _PADDING

    draw_w = size - 2 * _PADDING
    draw_h = size - 2 * v_padding

    scale_x = draw_w / total_length if total_length > 0 else 1.0
    scale_y = draw_h / (2 * max_aperture) if max_aperture > 0 else 1.0
    scale = min(scale_x, scale_y)

    x_offset = _PADDING + (draw_w - total_length * scale) * 0.5
    y_center = size * 0.5

    def to_px(lens_x, lens_y):
        px = x_offset + lens_x * scale
        py = y_center - lens_y * scale
        return (px, py)

    # Optical axis
    _draw_dashed_line(
        draw, _PADDING * 0.5, y_center, size - _PADDING * 0.5, y_center,
        _AXIS_COLOR, 1 * _SUPERSAMPLE,
        dash=8 * _SUPERSAMPLE, gap=6 * _SUPERSAMPLE,
    )

    # Compute effective drawing aperture for every surface, clamped where
    # adjacent arcs would cross — both within elements and across air gaps.
    effective_aps = []
    for i, s in enumerate(surfaces):
        eff = s["aperture"] * 0.5
        if i > 0 and not _is_stop(surfaces[i - 1]):
            s_prev = surfaces[i - 1]
            _, back_ap = _effective_semi_aperture(
                positions[i - 1], _diagram_radius(s_prev), s_prev["aperture"] * 0.5,
                positions[i], _diagram_radius(s), eff,
            )
            eff = min(eff, back_ap)
        if i < len(surfaces) - 1 and not _is_stop(surfaces[i + 1]):
            s_next = surfaces[i + 1]
            front_ap, _ = _effective_semi_aperture(
                positions[i], _diagram_radius(s), eff,
                positions[i + 1], _diagram_radius(s_next), s_next["aperture"] * 0.5,
            )
            eff = min(eff, front_ap)
        effective_aps.append(eff)

    # Fill glass elements
    for front_i, back_i in elements:
        for j in range(front_i, back_i):
            poly = _element_polygon(surfaces, positions, j, j + 1, effective_aps, to_px)
            draw.polygon(poly, fill=_GLASS_FILL)

    # Draw surface arcs
    for i, s in enumerate(surfaces):
        if _is_stop(s):
            continue
        semi_ap = effective_aps[i]
        r = _diagram_radius(s)
        is_cemented = False
        for front_i, back_i in elements:
            if front_i < i < back_i:
                is_cemented = True
                break
        if is_cemented:
            _draw_arc(draw, positions[i], r, semi_ap, to_px,
                       _CEMENTED_LINE, 2 * _SUPERSAMPLE)
        else:
            _draw_arc(draw, positions[i], r, semi_ap, to_px,
                       _SURFACE_LINE, 2 * _SUPERSAMPLE)

    # Draw element closing edges
    for front_i, back_i in elements:
        for j in range(front_i, back_i):
            ap_j = effective_aps[j]
            ap_j1 = effective_aps[j + 1]
            for sign in (1.0, -1.0):
                p0 = to_px(
                    _arc_x(positions[j], _diagram_radius(surfaces[j]), sign * ap_j),
                    sign * ap_j,
                )
                p1 = to_px(
                    _arc_x(positions[j + 1], _diagram_radius(surfaces[j + 1]), sign * ap_j1),
                    sign * ap_j1,
                )
                draw.line([p0, p1], fill=_SURFACE_LINE, width=2 * _SUPERSAMPLE)

    # Draw aperture stop
    for i, s in enumerate(surfaces):
        if _is_stop(s):
            stop_x = positions[i]
            semi_ap = s["aperture"] * 0.5
            notch = semi_ap * 0.15
            px_stop, _ = to_px(stop_x, 0)
            for sign in (1.0, -1.0):
                _, py_outer = to_px(0, sign * max_aperture)
                _, py_ap = to_px(0, sign * semi_ap)
                draw.line(
                    [(px_stop, py_outer), (px_stop, py_ap)],
                    fill=_STOP_COLOR,
                    width=2 * _SUPERSAMPLE,
                )
                _, py_notch = to_px(0, sign * (semi_ap - notch))
                draw.line(
                    [(px_stop, py_ap), (px_stop, py_notch)],
                    fill=_STOP_COLOR,
                    width=4 * _SUPERSAMPLE,
                )

    # Trace example rays — use the stop semi-aperture as the reference
    # height, since it defines the actual ray bundle that passes through
    # the system. For retrofocus designs the front element is much larger
    # than the useful ray bundle.
    stop_aps = [s["aperture"] * 0.5 for s in surfaces if _is_stop(s)]
    ray_semi_ap = stop_aps[0] if stop_aps else (
        min(s["aperture"] * 0.5 for s in surfaces)
    )
    clip_left = _PADDING * 0.5
    clip_right = size - _PADDING * 0.5
    for frac in (0.7, 0.5, 0.35, -0.35, -0.5, -0.7):
        ray_pts = _trace_ray(surfaces, positions, frac * ray_semi_ap)
        if ray_pts and len(ray_pts) >= 2:
            px_pts = [to_px(x, y) for x, y in ray_pts]
            # Clip to drawing area
            clipped = []
            for j in range(len(px_pts) - 1):
                ax, ay = px_pts[j]
                bx, by = px_pts[j + 1]
                dx = bx - ax
                if abs(dx) < 1e-6:
                    if clip_left <= ax <= clip_right:
                        clipped.append((ax, ay))
                        if j == len(px_pts) - 2:
                            clipped.append((bx, by))
                    continue
                if ax < clip_left:
                    if bx <= clip_left:
                        continue
                    t = (clip_left - ax) / dx
                    ax = clip_left
                    ay = ay + t * (by - ay)
                if bx > clip_right:
                    if ax >= clip_right:
                        continue
                    t = (clip_right - ax) / dx
                    bx = clip_right
                    by = ay + t * (by - ay)
                clipped.append((ax, ay))
                if j == len(px_pts) - 2:
                    clipped.append((bx, by))
            if len(clipped) >= 2:
                draw.line(clipped, fill=_RAY_COLOR, width=1 * _SUPERSAMPLE)

    return img.resize((_ICON_SIZE, _ICON_SIZE), Image.LANCZOS)


def main():
    lens_dir = _SRC_DIR / "lenses"
    output_dir = _SRC_DIR / "previews"
    output_dir.mkdir(exist_ok=True)

    lenses = load_lenses(lens_dir)
    for lens in lenses:
        img = _render_lens(lens["surfaces"])
        out_path = output_dir / f"{lens['filename_stem']}.png"
        img.save(out_path)
        print(f"  {out_path.name}")

    print(f"Generated {len(lenses)} diagrams in {output_dir}")


if __name__ == "__main__":
    main()

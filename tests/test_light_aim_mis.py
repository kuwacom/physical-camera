"""ゴースト・回折のライト照準 (70%) と一様標本化 (30%) の 1 サンプル MIS の 2 次元簡易モデル

シェーダーと同じ形の推定量 f / (0.7·p_aim + 0.3·p_uniform) の期待値が、一様標本化のみの推定量 f / p_uniform の期待値
(= 被積分関数の積分) と一致することを確かめる。照準可能なライトがない画素は一様のみで f / p_uniform
"""

import math
import random

AIM_FRACTION = 0.7
SAMPLES = 200_000

# (中心 x, 中心 y, 半軸 x, 半軸 y, 選択の重み)。一様領域 (半径 1 の円盤) からはみ出すもの・重なるものを含める
GHOST_TARGETS = [
    (0.2, -0.1, 0.15, 0.08, 5.0),
    (0.3, 0.0, 0.2, 0.25, 1.0),
    (0.9, 0.5, 0.3, 0.2, 2.0),
    (1.6, 0.0, 0.1, 0.1, 0.5),
]
GHOST_RADIUS = 1.0


def sample_target(targets, rng):
    total = sum(t[4] for t in targets)
    pick = rng.random() * total
    for target in targets:
        pick -= target[4]
        if pick <= 0.0:
            break
    cx, cy, rx, ry, _ = target
    r = math.sqrt(rng.random())
    a = 2.0 * math.pi * rng.random()
    return cx + r * math.cos(a) * rx, cy + r * math.sin(a) * ry


def target_pdf(targets, x, y):
    total = sum(t[4] for t in targets)
    pdf = 0.0
    for cx, cy, rx, ry, w in targets:
        dx = (x - cx) / rx
        dy = (y - cy) / ry
        if dx * dx + dy * dy <= 1.0:
            pdf += w / (math.pi * rx * ry)
    return pdf / total


def mis_estimate(f, sample_uniform, uniform_pdf, targets, rng):
    """1 サンプル MIS (balance heuristic) の推定値を SAMPLES 個返す"""
    values = []
    for _ in range(SAMPLES):
        if targets and rng.random() < AIM_FRACTION:
            x, y = sample_target(targets, rng)
        else:
            x, y = sample_uniform(rng)
        p_u = uniform_pdf(x, y)
        if targets:
            p = AIM_FRACTION * target_pdf(targets, x, y) + (1.0 - AIM_FRACTION) * p_u
        else:
            p = p_u
        values.append(f(x, y) / p if p > 0.0 else 0.0)
    return values


def uniform_estimate(f, sample_uniform, uniform_pdf, rng):
    values = []
    for _ in range(SAMPLES):
        x, y = sample_uniform(rng)
        values.append(f(x, y) / uniform_pdf(x, y))
    return values


def mean_and_error(values):
    n = len(values)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    return mean, math.sqrt(var / n)


def assert_matches(values, expected):
    mean, err = mean_and_error(values)
    assert abs(mean - expected) < 5.0 * err + 1e-3 * abs(expected), (mean, err, expected)


# --- ゴースト: 一様領域は半径 R の円盤。照準楕円のうち円盤外にはみ出した標本は寄与 0 (被積分関数が円盤外で 0)


def ghost_integrand(x, y):
    if x * x + y * y > GHOST_RADIUS * GHOST_RADIUS:
        return 0.0
    # 背景のなだらかな成分 + ライト付近の明るい成分
    bump = 40.0 * math.exp(-((x - 0.2) ** 2 + (y + 0.1) ** 2) / 0.005)
    bump += 15.0 * math.exp(-((x - 0.9) ** 2 + (y - 0.5) ** 2) / 0.01)
    return 1.0 + 0.5 * x + bump


def ghost_sample_uniform(rng):
    r = math.sqrt(rng.random()) * GHOST_RADIUS
    a = 2.0 * math.pi * rng.random()
    return r * math.cos(a), r * math.sin(a)


def ghost_uniform_pdf(x, y):
    return 1.0 / (math.pi * GHOST_RADIUS * GHOST_RADIUS)


def ghost_reference():
    # 一様標本化のみの推定量の期待値 = 円盤上の積分
    return disk_integral(ghost_integrand, 0.0, 0.0, GHOST_RADIUS, n=600)


def test_ghost_mis_matches_uniform_only():
    expected = ghost_reference()
    uniform = uniform_estimate(ghost_integrand, ghost_sample_uniform, ghost_uniform_pdf, random.Random(1))
    assert_matches(uniform, expected)
    mis = mis_estimate(ghost_integrand, ghost_sample_uniform, ghost_uniform_pdf, GHOST_TARGETS, random.Random(2))
    assert_matches(mis, expected)
    # 照準で明るい成分の分散が下がること (照準の目的)
    assert mean_and_error(mis)[1] < mean_and_error(uniform)[1]


def test_ghost_without_targets_is_uniform_only():
    # 照準できる画素がない場合に 1/0.3 を掛けると 3.3 倍明るくなる。一様のみの重みと同じであること
    expected = ghost_reference()
    values = mis_estimate(ghost_integrand, ghost_sample_uniform, ghost_uniform_pdf, [], random.Random(3))
    assert_matches(values, expected)


# --- 回折: 一様側は n 本の光条それぞれの軸方向・直交方向 Cauchy の積の平均 (平面全体が台)。PSF は軸方向 sinc² と直交方向ガウスの積の平均

BLADES = 6
BLADE_ROT = 0.3
THETA0 = 1.0
THETA0_PERP = THETA0 * math.cos(math.pi / BLADES) / math.sin(math.pi / BLADES)
# (中心 x, 中心 y, 半径, 選択の重み)。光源の円盤内だけで被積分関数が値を持つ
DIFF_LIGHTS = [(3.0, 0.5, 0.8, 4.0), (-1.0, -4.0, 1.5, 1.0)]


def sinc2(u):
    if abs(u) < 1e-6:
        return 1.0
    s = math.sin(math.pi * u) / (math.pi * u)
    return s * s


def spike_axes():
    for k in range(BLADES):
        a = BLADE_ROT + (k + 0.5) * 2.0 * math.pi / BLADES
        yield math.cos(a), math.sin(a)


def diffraction_psf_pdf(x, y):
    psf = pdf = 0.0
    for c, s in spike_axes():
        u = (x * c + y * s) / THETA0
        v = (-x * s + y * c) / THETA0_PERP
        psf += sinc2(u) * math.exp(-math.pi * v * v)
        pdf += 1.0 / ((1.0 + math.pi**2 * u * u) * (1.0 + math.pi**2 * v * v))
    norm = 1.0 / (BLADES * THETA0 * THETA0_PERP)
    return psf * norm, pdf * norm


def diffraction_radiance(x, y):
    return sum(1.0 for cx, cy, r, _ in DIFF_LIGHTS if (x - cx) ** 2 + (y - cy) ** 2 <= r * r)


def diffraction_integrand(x, y):
    radiance = diffraction_radiance(x, y)
    return diffraction_psf_pdf(x, y)[0] * radiance if radiance > 0.0 else 0.0


def diffraction_sample_uniform(rng):
    c, s = list(spike_axes())[min(int(rng.random() * BLADES), BLADES - 1)]
    u = math.tan(math.pi * (rng.random() - 0.5)) / math.pi * THETA0
    v = math.tan(math.pi * (rng.random() - 0.5)) / math.pi * THETA0_PERP
    return u * c - v * s, u * s + v * c


def diffraction_uniform_pdf(x, y):
    return diffraction_psf_pdf(x, y)[1]


def disk_integral(f, cx, cy, radius, n=400):
    # 極座標の中点則による円盤上の積分
    total = 0.0
    for i in range(n):
        r = (i + 0.5) / n * radius
        for j in range(n):
            a = (j + 0.5) / n * 2.0 * math.pi
            total += f(cx + r * math.cos(a), cy + r * math.sin(a)) * r
    return total * (radius / n) * (2.0 * math.pi / n)


def diffraction_reference():
    # 光源の円盤は互いに重ならないので円盤ごとの積分の和
    return sum(disk_integral(diffraction_integrand, cx, cy, r) for cx, cy, r, _ in DIFF_LIGHTS)


def test_psf_and_uniform_pdf_are_normalized():
    # PSF と一様側の密度はどちらも平面上で積分 1 (光条標本 1 つあたりの期待重みが 1)
    rng = random.Random(4)
    values = []
    for _ in range(SAMPLES):
        x, y = diffraction_sample_uniform(rng)
        psf, pdf = diffraction_psf_pdf(x, y)
        values.append(psf / pdf)
    assert_matches(values, 1.0)


def test_diffraction_mis_matches_uniform_only():
    expected = diffraction_reference()
    targets = [(cx, cy, r, r, w) for cx, cy, r, w in DIFF_LIGHTS]
    uniform = uniform_estimate(
        diffraction_integrand, diffraction_sample_uniform, diffraction_uniform_pdf, random.Random(5)
    )
    assert_matches(uniform, expected)
    mis = mis_estimate(
        diffraction_integrand, diffraction_sample_uniform, diffraction_uniform_pdf, targets, random.Random(6)
    )
    assert_matches(mis, expected)
    assert mean_and_error(mis)[1] < mean_and_error(uniform)[1]

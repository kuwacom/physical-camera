"""Japanese UI translations (bpy-free so tests can load this file directly)."""

# 日本語の訳文。キーは (翻訳コンテキスト, 英語の原文)
# Blender 本体がすでに訳している語(Lens, Normal, Chromatic Aberration など)は、本体の UI 全体を書き換えないよう含めない

DEFAULT = "*"
OPERATOR = "Operator"

TRANSLATIONS = {
    # プロパティ
    (DEFAULT, "f-stop"): "F 値",
    (DEFAULT, "f-stop (min f/{})"): "F 値 (最小 f/{})",
    (DEFAULT, "Aperture Blades"): "絞り羽根",
    (DEFAULT, "0 = circular"): "0 で円形",
    (DEFAULT, "Blade Rotation"): "羽根の回転",
    (DEFAULT, "Lens Ghosts"): "レンズゴースト",
    (DEFAULT, "Ghost Intensity"): "ゴーストの強さ",
    (DEFAULT, "Brightness multiplier for ghost images"): "ゴーストの明るさの倍率",
    (DEFAULT, "Diffraction"): "回折",
    (
        DEFAULT,
        "Simulate aperture diffraction starbursts (polygonal apertures only)",
    ): "絞り回折の光条をシミュレートします(多角形絞りのみ)",
    # デバッグ表示モード
    (DEFAULT, "Standard rendering"): "標準のレンダリング",
    (DEFAULT, "Pinhole"): "ピンホール",
    (DEFAULT, "Pinhole camera (no lens)"): "ピンホールカメラ(レンズなし)",
    (DEFAULT, "Diagnostic"): "診断",
    (DEFAULT, "Failure cause visualization"): "失敗の原因を可視化します",
    (DEFAULT, "Exit Direction"): "出射方向",
    (DEFAULT, "Exit ray direction as RGB"): "出射レイの方向を RGB で表示します",
    (DEFAULT, "Ghosts Only"): "ゴーストのみ",
    (DEFAULT, "Show only ghost reflections"): "ゴーストの反射だけを表示します",
    (DEFAULT, "Ghost Aim"): "ゴースト照準",
    (DEFAULT, "Light-aimed ghost diagnostic"): "ライトに向けたゴーストの診断表示",
    # オペレーター
    (OPERATOR, "Enable Physical Lens"): "物理レンズを有効化",
    (DEFAULT, "Set this camera to use the physical lens shader"): "このカメラで物理レンズシェーダーを使うようにします",
    (OPERATOR, "Disable Physical Lens"): "物理レンズを無効化",
    (DEFAULT, "Revert to a standard perspective camera"): "標準の透視カメラに戻します",
    # パネルの警告
    (DEFAULT, "Enable the Cycles render engine"): "レンダリングエンジンを Cycles にしてください",
    (
        DEFAULT,
        "OSL camera shaders need CPU or OptiX (GPU compute device is {})",
    ): "OSL カメラシェーダーには CPU か OptiX が必要です(GPU の計算デバイスは {})",
    (DEFAULT, "Shader compile error: {}"): "シェーダーのコンパイルエラー: {}",
    (
        DEFAULT,
        "No lights are visible to camera rays (0 passed to the shader)",
    ): "カメラレイに見えるライトがありません(シェーダーに渡した数: 0)",
    (
        DEFAULT,
        "Enable Object Properties > Visibility > Ray Visibility > Camera on the lights",
    ): "ライトのオブジェクトプロパティ > 可視性 > レイの可視性 > カメラをオンにしてください",
    (DEFAULT, "Scene lights passed to the shader: {}"): "シェーダーに渡したシーンのライト数: {}",
    # パネル
    (DEFAULT, "Physical Lens"): "物理レンズ",
    (DEFAULT, "Lens Diagram"): "レンズ断面図",
}

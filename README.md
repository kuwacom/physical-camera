# Physical Camera

日本語 | [English](README.en.md)

Blender のカメラを、物理ベースのレンズシミュレーションに置き換えるエクステンションです  
実際のレンズ構成を通して光線をトレースし、リアルな被写界深度、ボケ形状、色収差、ケラレを作り出します

このプロジェクトは Peter Simonsson 氏の [sidp/physical-camera](https://github.com/sidp/physical-camera) をフォークしたものです。

## 主な機能

- 多枚構成のレンズを通る逐次光線トレース
- 球面・非球面・円筒面に対応
- アナモルフィックレンズ対応(X/Y 2 軸の ABCD 光学と楕円ボケ)
- シングル/マルチコーティングの Fresnel 透過損失と自然なケラレ
- 分光的な色収差(400〜700nm を連続サンプリング)
- レンズ内反射によるゴーストと絞り回折の光条
- 特許データがあるレンズは群レンズ間隔可変のインナーフォーカスに対応
- 回転角を調整できる多角形絞り羽根
- ピント距離は Blender のカメラ設定に連動
- 光線の挙動を調べるデバッグ表示モード

## 必要環境

- Blender 4.5 以降
- Cycles
- CPU または OptiX レンダリング(OSL カメラは CUDA / HIP / Metal では使えません)

Cycles の **Open Shading Language** の項目は有効にしなくても動きます(4.5 / 5.2 の OptiX で確認)。有効にすると初回のみ Cycles 本体のコンパイルに数分かかるため、カメラ以外で OSL を使う場合だけ有効にしてください

## インストール

### 方法 1: プリファレンスから zip でインストール

1. [Releases](https://github.com/kuwacom/physical-camera/releases) から zip をダウンロードします
2. Blender で **編集 > プリファレンス > アドオン** を開きます
3. 右上の `⌄` メニューから **ディスクからインストール...** を選び、zip を選択します
4. 一覧の **Physical Camera** にチェックを入れて有効にします

### 方法 2: リポジトリから zip を作る

リポジトリ直下で次のコマンドを実行すると、インストール用の zip を作成できます

```sh
blender --command extension build
```

## 使い方

1. シーンでカメラを選択します
2. カメラプロパティのパネルを開きます
3. **Physical Lens** パネルの **Enable Physical Lens** をクリックします
4. レンズを選び、設定を調整します
   - **Lens** — 使用するレンズの構成
   - **f-stop** — 絞りの大きさ。下限はレンズの開放 F 値(レンズデータから近軸計算した実効値)で、パネルに表示されます
   - **Aperture Blades** — 0 で円形、3 以上で多角形のボケ
   - **Blade Rotation** — 絞り形状の回転
   - **Chromatic Aberration** — 分光分散の切り替え
   - **Lens Ghosts** — レンズ内反射によるゴーストのオン/オフ
   - **Ghost Intensity** — ゴーストの明るさの倍率(Lens Ghosts 有効時に表示)
   - **Diffraction** — 絞り回折による光条のシミュレーション(Aperture Blades が 3 以上のときのみ有効)
   - **Debug Mode** — Normal / Pinhole / Diagnostic(失敗の可視化)/ Exit Direction / Ghosts Only(ゴーストだけを描画)/ Ghost Aim(ゴーストの照準の診断)

ピント距離は、Blender 標準のカメラ Depth of Field 設定で制御します

### ゴーストと回折

Lens Ghosts と Diffraction は、シーンのライトを光源としてサンプリングします  
使用するライトには **オブジェクトプロパティ > 可視性 > レイの可視性 > カメラ** を ON にしてください(Blender の既定は OFF)  
発光メッシュも光源として扱います

パネルには、レンダリングを妨げる問題が警告として表示されます(Cycles が無効、GPU デバイスが OptiX 以外、カメラレイから見えるライトが 0 件など)  
物理レンズをやめて通常のカメラに戻すには、パネルの **Disable Physical Lens** をクリックします

Cycles の Custom Camera にある **Parameters** の値(aperture_blades など)はアドオンが自動で管理するため、編集しないでください

## 収録レンズ

- Canon RF 85mm f/1.2L USM(非球面、インナーフォーカス)
- Cooke Triplet 50mm f/4.5
- Double Gauss 50mm f/2
- Double Gauss 100mm f/1.7
- Mamiya 55mm f/2.8 N(中判)
- Mamiya 150mm f/4 Soft(中判、ソフトフォーカス)
- Navarro Anamorphic 2x Cinema(アナモルフィック、非球面)
- Neil Front Anamorphic 2x Cinema(アナモルフィック、インナーフォーカス)
- Nikkor AI AF Fisheye 16mm f/2.8D(インナーフォーカス)
- Petzval 85mm f/2.4
- Sonnar 50mm f/1.5
- Tessar 50mm f/2.8
- Zeiss Distagon 35mm f/4
- Zeiss Planar 80mm f/2.8(中判)
- Zuiko 18mm f/3.5(インナーフォーカス)

## レンズを追加する

`src/lenses/` に次の形式の `.toml` ファイルを作成します

```toml
[lens]
name = "Lens Name"
focal_length = 50.0
max_fstop = 2.0
coating = "multi"  # "none", "single", "multi"

[[surface]]
radius = 29.475
thickness = 3.76
ior = 1.67
aperture = 25.2
abbe_v = 57.0

[[surface]]
type = "stop"
radius = 0
thickness = 2.5
ior = 1.0
aperture = 20.0
abbe_v = 0.0

# ... more surfaces
```

サーフェスは前(被写体側)から後ろ(センサー側)の順に並べます。寸法はすべてミリメートルです。開口絞りは `type = "stop"` で `radius = 0`、`ior = 1.0` にします。非球面は `type = "aspheric"` で `conic` と `aspheric_coeffs` を使います。アナモルフィックレンズの円筒面は `type = "cylindrical_x"` または `type = "cylindrical_y"` を使います

レンズを追加したら、図版プレビューを再生成します: `uv run scripts/build_diagrams.py`

群レンズ間隔の可変化、非球面係数、円筒面の規約の詳細は [AGENTS.md](AGENTS.md) を参照してください

## 開発

開発環境は [uv](https://docs.astral.sh/uv/) で管理しています

```sh
# 開発用の依存関係をインストール(ruff, pillow, pytest)
uv sync

# 静的チェック
uv run ruff check .

# コードを整形
uv run ruff format .

# テストを実行
uv run pytest
```

## ライセンス

GPL-3.0-or-later

# PixelSeal 逐步复现

在 WSL 中用 CPU 为图片嵌入 64 位 ID，再测试裁剪、缩放、旋转等编辑之后能否恢复完整 ID。第 5 步在 Windows 编辑器中操作，其余命令在 WSL 中执行。PixelSeal 使用 Meta 官方模型，短 ID 使用本实验的 BCH 纠错和 CRC32 校验。已有的 blind_watermark 图片需要从原图重新嵌入 PixelSeal 水印。

## 1. 进入项目并准备环境

已有仓库时，请从仓库根目录开始。首次复现可直接检出实验分支：

```bash
git clone --branch PixelSeal https://github.com/gxcuit/blind_watermark.git
cd blind_watermark
```

在 WSL 终端设置以下变量，后面的步骤沿用同一个终端。

```bash
EXP=experiments/pixelseal
OUT="$EXP/outputs_walkthrough"
ID=0123456789abcdef
export NUMBA_CACHE_DIR=/tmp/pixelseal-numba-cache
```

若已装好 `.venv` 的依赖、官方源码和权重，可以直接到第 2 步。首次准备环境需安装 `uv`，然后执行下面两段。

```bash
if [ ! -x .venv/bin/python ]; then
  uv venv --python 3.14 .venv
fi

uv pip install --python .venv/bin/python torch torchvision \
  --index-url https://download.pytorch.org/whl/cpu
uv pip install --python .venv/bin/python \
  numpy pillow opencv-python omegaconf requests einops timm==0.9.16 \
  av pycocotools tqdm reedsolo ffmpeg-python galois==0.4.11
```

```bash
if [ ! -d "$EXP/upstream" ]; then
  git clone https://github.com/facebookresearch/videoseal.git "$EXP/upstream" &&
  git -C "$EXP/upstream" checkout f12875a9b5e711e0c6e2dc3a22aca18231ce4a1a
fi

if [ ! -f "$EXP/models/checkpoint.pth" ]; then
  mkdir -p "$EXP/models" &&
  curl -fL https://dl.fbaipublicfiles.com/videoseal/pixelseal/checkpoint.pth \
    -o "$EXP/models/checkpoint.pth.part" &&
  mv "$EXP/models/checkpoint.pth.part" "$EXP/models/checkpoint.pth"
fi
```

权重约 1.15 GiB，由命令从官方地址下载。权重目录、第三方源码、生成图片和运行日志会留在本地。原实验的依赖版本见 [environment.txt](environment.txt)。

## 2. 嵌入 ID 并验证完整图

```bash
.venv/bin/python "$EXP/verify_crop.py" --encoding bch \
  --embed examples/pic/ori_img.jpeg --id "$ID" --output "$OUT/watermarked.png"

.venv/bin/python "$EXP/verify_crop.py" --encoding bch \
  --decode "$OUT/watermarked.png"
```

第一条生成 `watermarked.png`，输出 `embedded_id`。第二条只读取这张 PNG，预期输出 `"decoded_id": "0123456789abcdef"`。确认完整图能提取后，再对副本做编辑。更换图片时，把 `--embed` 后的路径换成你的原图。ID 可以替换为任意 16 个十六进制字符，前导零也要保留。

## 3. 自动生成攻击图片

下面每条命令从同一张完整水印图生成一个独立副本。生成这些副本只需要 Pillow。

```bash
# 复现 Windows 裁剪案例，删掉顶部 395 行，得到 738×713
.venv/bin/python "$EXP/attack_image.py" "$OUT/watermarked.png" "$OUT/crop.png" \
  --crop 0 395 738 1108

# 等比例缩小到一半
.venv/bin/python "$EXP/attack_image.py" "$OUT/watermarked.png" "$OUT/scale.png" \
  --scale 0.5

# 逆时针旋转 15°，扩展画布并用黑色填充边角
.venv/bin/python "$EXP/attack_image.py" "$OUT/watermarked.png" "$OUT/rotate.png" \
  --rotate 15

# 水平镜像
.venv/bin/python "$EXP/attack_image.py" "$OUT/watermarked.png" "$OUT/flip.png" \
  --flip-horizontal

# 高斯模糊，半径 2 像素
.venv/bin/python "$EXP/attack_image.py" "$OUT/watermarked.png" "$OUT/blur.png" \
  --blur 2

# JPEG 质量 50
.venv/bin/python "$EXP/attack_image.py" "$OUT/watermarked.png" "$OUT/jpeg50.jpg" \
  --jpeg-quality 50

# 组合测试，裁剪后缩小到 70%，再以 JPEG 质量 75 保存
.venv/bin/python "$EXP/attack_image.py" "$OUT/watermarked.png" "$OUT/combined.jpg" \
  --crop 0 395 738 1108 --scale 0.7 --jpeg-quality 75
```

`--crop LEFT TOP RIGHT BOTTOM` 使用左上角为原点的坐标，右和下是边界。例如 `--crop 0 395 738 1108` 保留第 395 行起直至末尾的像素，行号从 0 开始。换图时按实际尺寸调整坐标。

用 `--resize 500 400` 可以指定宽高来测试非等比例拉伸，用 `--brightness 0.7` 可以变暗。选项按裁剪、缩放、旋转、镜像、亮度、模糊、保存的顺序执行。想改变顺序时，把上一步的输出作为下一步的输入。查看全部参数可以执行：

```bash
.venv/bin/python "$EXP/attack_image.py" --help
```

## 4. 提取并核对自动攻击结果

一条命令可以提取多张图片，共用一次模型加载。也可以只保留某一个路径单独提取。

```bash
.venv/bin/python "$EXP/verify_crop.py" --encoding bch --decode \
  "$OUT/crop.png" "$OUT/scale.png" "$OUT/rotate.png" "$OUT/flip.png" \
  "$OUT/blur.png" "$OUT/jpeg50.jpg" "$OUT/combined.jpg"
```

每张图的 JSON 中有 `file`、`decoded_id` 和 `bch_corrected_bits`。只有 `decoded_id` 与第 2 步设置的 `0123456789abcdef` 完全相同才算成功。`null` 表示没有得到通过纠错和校验的 ID。提取命令只需要待检图片路径和编码方式，直接使用图片被编辑后的尺寸。

本机用示例原图和 ID 跑过上述七个自动攻击，七张图均恢复了完整 ID。同位置裁剪纠正了 9 位，组合攻击纠正了 11 位。

## 5. 在 Windows 中手动编辑并提取

先在 WSL 执行以下命令，在 Windows 资源管理器中打开输出目录。

```bash
explorer.exe "$(wslpath -w "$OUT")"
```

1. 在 Windows 画图或其他编辑器中打开 `watermarked.png`，选择要保留的区域并裁剪。也可以缩放、旋转或调色。
2. 用“另存为”将结果保存到同一目录，命名为 `manual-crop.png`。如果要测试 JPEG 重编码，保存为 `manual-jpeg.jpg`。
3. 回到 WSL 执行以下命令。若另存到了其他 Windows 目录，可用 `wslpath -u 'C:\你的目录\manual-crop.png'` 查看对应的 WSL 路径。

```bash
.venv/bin/python "$EXP/verify_crop.py" --encoding bch \
  --decode "$OUT/manual-crop.png"
```

核对 `decoded_id` 是否等于 `0123456789abcdef`。若另存为 JPG，就把命令中的 `manual-crop.png` 换成 `manual-jpeg.jpg`。

如果划线、涂画等编辑后得到 `decoded_id: null`，可以加 `--bch-retry` 再提取：

```bash
.venv/bin/python "$EXP/verify_crop.py" --encoding bch --bch-retry \
  --decode "$OUT/manual2.jpg"
```

该选项在常规 BCH 解码失败后，挑出模型最不确定的 8 位，最多尝试 70 种翻转 4 位的组合。每个候选仍需通过 BCH 解码和 CRC32 校验，所需输入只有待检图片。JSON 中的 `bch_retry_used: true` 表示启动了重试，是否成功仍看 `decoded_id`。重试会增加 CPU 耗时，结果取决于错误位是否落在候选范围内。

实测 `manual2.jpg` 有 27 位错误，超过常规 BCH(255,99) 能保证纠正的 23 位。加上 `--bch-retry` 后恢复了 `0123456789abcdef`。要单独测试涂画的影响，可以在同一张水印图上画线后另存为 PNG，再额外另存一张 JPG，对两份文件分别提取。

## 6. 可选对照与原实验复跑

对原图运行提取，检查是否返回 `null`：

```bash
.venv/bin/python "$EXP/verify_crop.py" --encoding bch \
  --decode examples/pic/ori_img.jpeg
```

以下命令从仓库里的原图复跑同位置裁剪实验，使用 20 个随机 ID，并在 `outputs_bch/results.json` 写入逐项结果。若本地有原先的 `embedded.png` 和 `embedded2.png`，且两图仍符合原案例的裁剪关系，脚本还会加入旧算法图片作对照。原报告的同位置裁剪组是 20/20，结论适用于该样图和该裁剪操作。

```bash
.venv/bin/python "$EXP/verify_crop.py" --encoding bch \
  --skip-random --trials 20 --seed 20261118
```

原始 256 位和 Reed–Solomon 对照可用 `.venv/bin/python "$EXP/verify_crop.py"` 复跑，结果写入 `outputs/results.json`。旧的 Reed–Solomon 水印图须用 `--encoding rs` 提取。详见 [实验报告](RESULTS.md)。

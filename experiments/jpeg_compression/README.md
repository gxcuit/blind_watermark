# JPEG 转存后能否读回 64 位 ID

对比当前仓库的 `blind_watermark` 和 ShieldMnt `invisible-watermark` 的两种频域方法 `dwtDct`、`dwtDctSvd`。业务成功条件是从转存后的图片中逐位读对完整的 64 位 ID。每条 ID 都直接写入，不加 BCH 或其他纠错层。ShieldMnt 随包提供的 RivaGAN 模型只接受 32 位消息，因此本实验选取能直接写入 64 位的两种方法。[ShieldMnt 项目](https://github.com/ShieldMnt/invisible-watermark) · [本仓库核心算法](../../blind_watermark/bwm_core.py)

## 实测结果

**对于默认参数、单次 JPEG 转存且质量为 Q50、Q75 或 Q95 的场景，选 `blind_watermark`。** 两张照片各写入 5 个 ID，在这三档质量下都完整恢复。日常流程若会多次转存，同一个 Q50 终点可能得到不同结果。

每个单元格写作「完整恢复次数 / 10（总错误位数）」。每格共提取 640 位，任意一位出错就计为该条 ID 恢复失败。原始十六进制 ID 和每次提取结果见 [210 条逐次记录](results.csv)。

| 转存条件 | blind_watermark | ShieldMnt dwtDct | ShieldMnt dwtDctSvd |
|---|---:|---:|---:|
| PNG 无损对照 | 10/10（0） | 10/10（0） | 10/10（0） |
| JPEG Q95 | 10/10（0） | 0/10（291） | 5/10（22） |
| JPEG Q75 | 10/10（0） | 0/10（306） | 0/10（147） |
| JPEG Q50 | 10/10（0） | 0/10（306） | 0/10（306） |
| JPEG Q30 | 1/10（48） | 0/10（306） | 0/10（306） |
| JPEG Q10 | 0/10（56） | 0/10（306） | 0/10（306） |
| JPEG Q75 → 解码 → JPEG Q50 | 5/10（13） | 0/10（306） | 0/10（287） |

连续转存时，`blind_watermark` 在 738×1108 的照片上恢复了 5/5，在 512×512 的照片上为 0/5。两张照片的内容和尺寸都不同，此结果无法单独归因于分辨率。在单次 Q50 条件下，ShieldMnt 两个方法对全部样本均读出全零 ID，因此错误位数恰好相同。

本轮 OpenCV 的 JPEG 输出使用 4:2:0 色度抽样。结果覆盖这两张照片、这套编码器及各方法的默认嵌入强度。不同平台的质量刻度、色度抽样和转存次数可能不同。

## 复现

在仓库根目录执行。三个方法都用 CPU。ShieldMnt 的包在导入时依赖 PyTorch，运行这两个频域方法无需下载模型权重。

```bash
uv venv --python 3.14 .venv-jpeg
uv pip install --python .venv-jpeg/bin/python 'torch==2.15.0+cpu' \
  --index-url https://download.pytorch.org/whl/cpu
uv pip install --python .venv-jpeg/bin/python 'invisible-watermark==0.2.0' \
  'numpy==2.5.5' 'opencv-python==5.0.0.93' 'PyWavelets==1.10.0' 'Pillow==13.0.0'
.venv-jpeg/bin/python experiments/jpeg_compression/compare.py --output /tmp/jpeg-default.csv
```

脚本直接导入当前工作区的 `blind_watermark`。实测使用其 0.4.4 版本（提交 `92fd011`）、Python 3.14.7 和上述包版本，OpenCV 使用 libjpeg-turbo 3.1.2-70。每次运行请给 `--output` 一个新文件名，以便保留各次结果。

换成自己的照片、ID 数量和转存流程时，可以这样运行。空格分隔独立条件，逗号连接连续转存的质量值。下面最后一个条件是 Q85 保存并重新解码后，再以 Q60 转存。

```bash
.venv-jpeg/bin/python experiments/jpeg_compression/compare.py \
  --images /path/to/photo1.jpg /path/to/photo2.png \
  --ids 10 --seed 42 --qualities 90 60 30 85,60 \
  --output /tmp/jpeg-custom.csv
```

## 方法与指标

- 默认输入是仓库里的 [738×1108 照片](../../examples/pic/ori_img.jpeg) 和 [512×512 照片](../../examples/pic/Lena_512x512.jpg)。固定种子 `20261119` 生成 5 个 ID，两张照片和三种方法都用相同的 5 个 ID。
- 各方法使用默认嵌入强度。`blind_watermark` 使用 `bit` 模式，两个密码均为 1。每次嵌入后将结果以 8 位 PNG 在内存中编码并解码，建立无损对照。每个 JPEG 条件都从同一份 PNG 像素独立开始，宽高保持不变。各次转存都经过真正的 JPEG 编码和解码。
- 提取器只接收待测图和约定的算法参数、64 位长度。逐位比较在提取结束后执行。脚本打印每组的完整恢复数和总错误位数，CSV 保存每次试验的图片、尺寸、方法、转存条件、预期与读出的十六进制 ID、错误位数及成功标记。

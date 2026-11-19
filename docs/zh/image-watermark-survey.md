# 图像水印项目调研与选型

调研日期为 2026 年 11 月 19 日。主要场景是给已有照片写入 64 位用户或订单 ID，图片发布后，从收到的副本中读回完整 ID。

## 社交媒体发布时怎么选

- **确认平台保留宽高，只做 JPEG 重编码时，选 `blind_watermark`。** 它无需模型权重。在本项目的同图对照中，JPEG 质量 50 转存后，5 个 ID 均完整恢复，纠错前就已全部读对。
- **平台也可能缩图，或无法确定平台处理方式时，优先沿用 PixelSeal + BCH。** 本项目已有可运行的编解码流程。把样图宽高各缩小一半后，测试的 1 个 ID 仍完整恢复。部署需要加载约 1.15 GiB 的模型权重。[复现步骤与缩图测试](../../experiments/pixelseal/README.md)
- **需要在浏览器提取时，优先对照 Adobe TrustMark。** 官方提供 JavaScript 解码器，内置 BCH。68 位有效载荷档能容纳完整的 64 位 ID，可纠正 4 个错误位。[官方仓库](https://github.com/adobe/trustmark) · [容量定义](https://github.com/adobe/trustmark/blob/main/python/trustmark/datalayer.py)

前两项的成功次数来自本地编码测试。要验证特定平台，可把几张实际照片分别嵌入不同 ID，按日常方式上传，再从帖子保存平台分发的图片。记录下载图的宽高，以及正确 ID、错误 ID、未解出 ID 各出现多少次。这样能把单纯的 JPEG 重编码与平台额外缩图的影响区分开。

## PixelSeal 与 blind_watermark 的原理和实测

**`blind_watermark`** 对图像各通道做小波变换，把低频部分划成固定大小的块，再用 DCT 和奇异值量化写入比特。同一消息在多个块中重复，提取时可以综合各块的结果。转存 JPEG 后仍可能保有足够的重复信号；裁剪或缩放则可能改变块的起点、大小和编号。常规提取需要匹配的密码参数及水印长度或形状。它支持比特、文本和二值图案，适合固定画布、内部归档及轻量 CPU 部署。[核心实现](../../blind_watermark/bwm_core.py) · [载荷接口](../../blind_watermark/blind_watermark.py)

**PixelSeal** 用神经网络根据图像内容和 256 位消息生成细微扰动，在训练中让提取器从经过编辑的图像恢复消息，并按人眼对局部变化的敏感度调节水印强度。本项目额外用 BCH 和 CRC32 保护 64 位业务 ID。纠错层能把少量比特错误修复为完整 ID。[原论文](https://arxiv.org/abs/2512.16874) · [官方实现](https://github.com/facebookresearch/videoseal) · [本项目的编码实现](../../experiments/pixelseal/bch_id.py)

下表来自本次调研中的配对测试。输入为同一张 738 × 1108 照片。两种方法分别嵌入相同的 5 个 ID，每对使用完全相同的 256 位载荷及 BCH、CRC32 编码。成功指解出的 64 位 ID 与原值完全相同。

| 条件 | PixelSeal + BCH | blind_watermark + 相同 BCH |
|---|---:|---:|
| 完整 PNG | 5/5 | 5/5 |
| 完整图转存 JPEG，质量 50，宽高不变 | 5/5 | 5/5 |
| 删除顶部 395 行，仅凭剩余 64.35% 面积直接提取 | 5/5 | 0/5 |
| 上述裁剪后，每条 256 位消息在纠错前的错误数 | 7～15 位 | 120～138 位 |
| 完整 PNG 相对原图的 RGB PSNR 中位数，越高表示像素误差越小 | 46.85 dB | 38.40 dB |

对同一批 `blind_watermark` 裁剪图，按已知坐标放回原尺寸黑色画布，5 个 ID 又全部读对，纠错前错误数均为零。保留的照片像素相同，仅恢复分块对齐就改变了结果。[画布恢复实现](../../blind_watermark/recover.py)

仓库另有同一张图上的 PixelSeal 扩展实验。删除顶部 395 行后，直接写入随机 256 位消息时，完整消息恢复 0/5；使用 64 位 ID、CRC32 和 BCH 的另一组恢复 20/20。这说明业务评估应统计完整 ID。样本覆盖的是单张照片和特定编辑条件，跨图片、跨平台的成功率仍需分别测量。[实验报告](../../experiments/pixelseal/RESULTS.md) · [汇总数据](../../experiments/pixelseal/summary.json)

## 其他可直接处理已有图片的项目

下表依据作者论文、官方文档与仓库示例整理。这些项目在当前样图上的完整 ID 恢复率尚未实测。

| 项目 | 载荷与方法 | 适用场景、实测边界与许可 |
|---|---|---|
| **Adobe TrustMark** | 神经网络写入 100 位原始载荷。扣除版本位与 BCH 后，可用 40、61、68 或 75 位。 | 适合社交分享、JPEG 压缩和短 ID 追溯。官方 FAQ 讨论的随机四边裁剪约损失 20% 面积，与上表顶部单侧裁去约 36% 的条件不同。代码和模型均为 MIT。[仓库](https://github.com/adobe/trustmark) · [官方 FAQ](https://opensource.contentauthenticity.org/docs/durable-cr/tm-faq/) |
| **Meta Watermark Anything（WAM）** | 给指定区域嵌入 32 位消息，提取时同时定位区域；同图不同区域可携带不同消息。 | 适合商品被抠出后贴到新海报、拼图中的素材溯源。论文在特定实验中从占画面 10% 以内的小区域提取消息，平均错误少于 1 位。代码与新版 `wam_mit.pth` 为 MIT，论文原版 COCO 权重为非商用许可。[仓库](https://github.com/facebookresearch/watermark-anything) · [论文](https://arxiv.org/abs/2411.07231) |
| **StegaStamp** | 相机拍摄后先定位并校正透视，再解码。默认模型经纠错后有 56 位有效载荷。 | 适合印刷品、包装、展板和屏幕翻拍。作者展示了真实打印和拍摄结果。64 位业务 ID 可通过较短编号查表关联。仓库为 MIT，官方实现依赖较老的 TensorFlow 1.x。[代码](https://github.com/tancik/StegaStamp) · [实拍演示](https://www.matthewtancik.com/stegastamp) |
| **ShieldMnt invisible-watermark** | 提供 DWT-DCT、DWT-DCT-SVD 和 RivaGAN。作者的频域测试使用 64 位消息，所附 RivaGAN 模型为 32 位。 | 适合做 JPEG 转存和 CPU 频域方案对照。作者示例中频域法与 RivaGAN 都通过 JPEG 测试；缩放和改变宽高比的裁剪是已知弱项。仓库为 MIT。[仓库与测试表](https://github.com/ShieldMnt/invisible-watermark) |
| **Stéganô** | 用最低有效位（LSB）将文本写入像素，无需模型。 | 适合无损 PNG 的内部传递和教学。JPEG 重编码、缩放会改变承载消息的低位。GPL-3.0。[仓库与示例](https://github.com/cedricbonhomme/Stegano) |
| **ImageMagick** | 批量叠加可见文字、半透明 Logo 或平铺图案。 | 适合样片、设计审阅稿、电商预览。接收者能直接看到归属或编号。角标容易随裁剪丢失，平铺覆盖更多局部，也会遮挡更多内容。[项目仓库](https://github.com/ImageMagick/ImageMagick) · [官方水印示例](https://usage.imagemagick.org/annotating/) |

## AI 编辑和生成模型场景

| 项目 | 适用场景与许可 |
|---|---|
| **VINE** | 给已有图片加印后，评估 AI 重绘、全图编辑或重新生成对水印的影响。作者公开了 W-Bench、基线结果与编辑样例，适合复用相同条件进行比较。VINE-R 模型卡标注 MIT，GitHub 仓库采用 NTU 双重许可，商业使用需另行取得授权。[项目与 W-Bench](https://github.com/Shilin-LU/VINE) · [仓库许可](https://github.com/Shilin-LU/VINE/blob/main/LICENSE) · [模型卡](https://huggingface.co/Shilin-LU/VINE-R-Enc) |
| **Meta Stable Signature** | 微调潜空间扩散模型的解码器，让同一版本的生成器输出自带固定 48 位签名。适合能控制生成流程、希望识别模型来源的团队。若要给同一张照片的不同接收者分配不同 ID，可用上文支持逐图写入的方案。仓库主体为 CC-BY-NC，部分组件为 MIT。[官方实现与许可](https://github.com/facebookresearch/stable_signature) |

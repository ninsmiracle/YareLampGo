# YareLampGo V2.0 结构件

简体中文 | [English](README.en.md)

V2.0 是当前唯一维护的公开硬件结构版本。V1.0 结构件已从主线移除；两个版本的底座、支撑杆、电机外壳和灯头不应混装。

![YareLampGo V2.0 装配预览](YareLampGo_V2.0/assembly-preview.png)

## 文件

| 文件 | 说明 |
| --- | --- |
| `YareLampGo_V2.0/YareLampGo_V2.0_assembly.step` | SolidWorks 2022 导出的 STEP AP214 完整总成，单位为毫米。 |
| `YareLampGo_V2.0/assembly-preview.png` | 从官方组装说明中提取的总成预览。 |
| [`../../docs/hardware/v2/YareLampGo_V2.0_assembly_manual.docx`](../../docs/hardware/v2/YareLampGo_V2.0_assembly_manual.docx) | 带物料图片、紧固件位置、走线和装配顺序的图文说明。 |
| [`../../docs/hardware/v2/README.md`](../../docs/hardware/v2/README.md) | 可搜索的 V2.0 组装、电路和首次上电说明。 |

## 使用边界

- STEP 是完整装配总成，不是已经按 3D 打印流程拆好的逐件 STL/3MF 包。请在 CAD 中按实体/零件导出，并自行确认单位、公差、壁厚、螺纹嵌件孔和打印方向。
- 总成包含结构件以及 S3、C6、PCB、LED、舵机等参考模型。制造结构件前应隐藏或排除采购件。
- 首次安装或更换结构件后必须重新校准。不要复用另一台设备或 V1.0 的校准数据。
- V2.0 原始文件、校验和与能力边界见 [`../../docs/hardware/v2/SOURCE_MANIFEST.md`](../../docs/hardware/v2/SOURCE_MANIFEST.md)。

## 许可

除非本地文件另有说明，本目录中的公开 STEP 和预览图按 `CERN-OHL-W-2.0` 发布。仓库级资产授权表见 [ASSET_LICENSES.md](../../ASSET_LICENSES.md)。

本文件包不是经过量产签核的供应商图纸集。打印或加工前，请自行完成尺寸、公差、材料、载荷、线束活动空间和电气安全复核。

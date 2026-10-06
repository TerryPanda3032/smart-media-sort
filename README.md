# 智能素材分类系统（Smart Media Sort）

> 面向摄影师与内容创作者的本地素材整理工作台：收集 → 过滤 → AI 分类 → 交付，四步流水线一站式完成照片/视频素材的整理与交付。

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.141%2B-009688?logo=fastapi&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green.svg)

---

## 目录

- [项目简介](#项目简介)
- [核心特性](#核心特性)
- [四步工作流](#四步工作流)
- [技术栈](#技术栈)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [项目结构](#项目结构)
- [常见问题](#常见问题)
- [许可证](#许可证)

---

## 项目简介

智能素材分类系统是一个**本地运行**的 Web 应用，帮助摄影师、修图师和内容创作者把散乱的照片/视频素材整理成可交付的成品。它通过一套「收集 → 过滤 → AI 分类 → 交付」的四步流水线，把素材整理这件繁琐的工作自动化：

- **收集**：从相机卡、手机、网盘等任意来源把素材拷贝进项目，自动加水印、压缩照片、压缩视频；
- **过滤**：先删格式/损坏文件并分流为「图片素材 / 视频素材」，再做三级废片过滤（曝光 / 全画幅对焦 / 人脸质量），并支持**连拍/相似图分组选留**，人工可一键「拯救」或「保留」误判；
- **AI 分类**：视频自动抽帧打标签；照片通过自然语言对话让 AI 理解你的整理意图，再自动执行分类归档；
- **交付**：把整理好的「图片素材 / 视频素材」整体拷贝到指定输出目录并校验。

所有数据都保存在本地：**废片筛检与相似图检测全部由本地 DNN 模型（ONNX）离线完成，不消耗 API、不上传素材**；需要大模型的能力（照片分类、视频标签）则通过可配置的 OpenAI 兼容接口（默认 ModelScope 通义千问）提供。

## 核心特性

- **🔬 三级废片过滤（本地 DNN，不消耗 API）**：第一级分区块直方图曝光（8×8 宫格过曝/欠曝 + 人脸区域过曝否决）；第二级 12×12 分块 Tenengrad 判整幅对焦；第三级 YuNet 检脸 + eDifFIQA 人脸质量评分。结果写入 `筛选结果.json`，支持逐张复核与「拯救」。
- **🔗 连拍/相似图分组**：用 SSCD 深度描述子计算每张照片特征，只在**顺序相邻**的照片之间比对相似度并用并查集连通成组（连拍/连续抓拍），自动推荐组内最佳一张；在独立的「连拍组筛选模式」中逐组选留（快捷键 A/D/Q/E/空格），未保留的照片与废片统一收口移入「废弃物」。
- **💬 照片自然语言分类**：输入一句整理要求（如「先把照片分成 A 类和 B 类，再按需细分」），AI 先与你多轮确认方案，再通过「验收 AI + 分类 AI」双模型编排自动执行归档与重命名。
- **🎬 视频 AI 标签提取**：ffmpeg 抽帧（2fps）+ 多模态 AI 分析，自动生成视频标签，结果写入 `视频标签结果.json`。
- **⚡ GPU 加速视频压缩**：自动检测并优先使用 NVENC / QSV / AMF 硬件编码，支持 cuvid 硬解 + 硬缩放路径，处理速度显著优于纯 CPU。
- **🖼️ 照片压缩 + 水印**：单线程安全处理 + 局部区域水印合成，内存占用降低 60 倍以上，照片处理多线程并行加速 3~5 倍。
- **📡 SSE 实时进度**：拷贝、批处理、筛检、三级过滤、连拍检测、分类、交付全程实时推送进度，前端读条不卡死。
- **🎨 国风视觉设计**：竹纹、缠枝纹、粒子流等定制 UI，开箱即用的沉浸式操作体验。

## 四步工作流

| 步骤 | 名称 | 说明 |
| ---- | ---- | ---- |
| Step 1 | **收集** | 选择源文件夹 → 拷贝进项目，可选添加水印 / 压缩照片 / 压缩视频，实时显示总进度与当前文件进度 |
| Step 2 | **过滤** | ①筛检常规素材（删格式/损坏文件）→ ②分流为「图片素材 / 视频素材」→ ③三级废片过滤（曝光 / 全画幅对焦 / 人脸质量）→ ④连拍/相似图分组选留；可逐张「拯救/保留」误判，确认后废片与未保留相似图统一移入「废弃物」 |
| Step 3 | **AI 分类** | 视频模式：自动抽帧打标签；照片模式：自然语言对话制定分类方案 → AI 自动执行归档 |
| Step 4 | **交付** | 选择输出目录，将整理好的素材整体拷贝并校验，四位验证码确认后完成交付 |

## 技术栈

**后端**

- Python 3.10+
- [FastAPI](https://fastapi.tiangolo.com/) + Uvicorn
- Jinja2 模板引擎
- Pillow（图像处理）
- requests（调用 AI 接口）
- NumPy + [opencv-python-headless](https://pypi.org/project/opencv-python-headless/)（三级过滤与相似图检测的图像算法）
- [onnxruntime](https://onnxruntime.ai/)（本地 DNN 模型推理：YuNet / eDifFIQA / SSCD）
- ffmpeg（视频抽帧 / 压缩，可选，建议安装带 GPU 加速的版本）

**本地模型（ONNX，随仓库分发）**

- `face_detection_yunet_2023mar.onnx`（约 228KB）—— 人脸检测
- `ediffiqa_tiny_jun2024.onnx`（约 7MB）—— 人脸质量评分
- `sscd_disc_mixup.onnx`（约 94MB）—— 相似图/连拍描述子

**前端**

- 原生 JavaScript + [htmx](https://htmx.org/)（局部刷新）
- 手写 CSS（国风视觉体系）
- SSE（Server-Sent Events）实时进度

## 快速开始

### 环境要求

- Windows 10/11（推荐，内置文件夹选择对话框与 Edge 应用模式）
- Python 3.10+
- ffmpeg（可选，处理视频时需要；建议使用带 NVENC/QSV/AMF 硬件加速的构建）

### 安装与启动

```bash
# 1. 克隆仓库
git clone https://github.com/TerryPanda3032/smart-media-sort.git
cd smart-media-sort

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动后端（默认端口 1145）
python backend/main.py
```

启动后浏览器自动打开 `http://127.0.0.1:1145`。

> **Windows 用户**：也可以直接双击根目录的 `启动.bat`，脚本会自动检查 Python、配置清华 pip 镜像、安装依赖并以后台控制台方式启动后端，然后以 Edge 应用模式打开界面。

### 首次使用

1. 点击右上角 **设置**，配置「工作目录」（素材项目存放的根目录，需有写入权限）；
2. 填写 **API 地址 / API 密钥 / 推理模型**（见下方配置说明）；
3. 点击「保存并测试」验证连接；
4. 回到素材库首页，点击「+ 新建」创建项目，进入四步工作流。

## 配置说明

系统首次启动后会在项目根目录生成 `config.json`，所有配置也可在界面「设置」中修改：

| 配置项 | 说明 | 默认值 |
| ------ | ---- | ------ |
| `work_dir` | 素材项目存放的工作目录（需有写入权限） | 空 |
| `api_url` | OpenAI 兼容的 Chat Completions 接口地址 | `https://api-inference.modelscope.cn/v1/chat/completions` |
| `api_key` | API 密钥 | 空 |
| `model` | 推理模型（照片分类 / 视频标签等） | `Qwen/Qwen3.5-397B-A17B` |
| `reasoning_effort` | 主力模型思考强度（`low` / `medium` / `high`） | `medium` |
| `video_quality` | 视频压缩质量，对应 ffmpeg CRF（越小画质越高、编码越慢），范围 18–35 | `23` |
| `watermark_size` | 水印字号（以 1080 标准高度为基准的像素值） | `24` |
| `watermark_opacity` | 水印透明度（0–100，越大越不透明） | `80` |
| `ffmpeg_path` | ffmpeg 可执行文件路径（留空则使用系统 PATH） | 空 |

> 接口兼容任何 OpenAI Chat Completions 协议的服务，只需修改 `api_url`、`api_key` 与模型名即可接入其他供应商。
> 三级废片过滤与连拍/相似图检测为**本地 DNN 推理**，无需 API，也不受上述配置影响。

## 本地模型说明

`backend/models/` 内的 ONNX 模型随仓库分发，开箱即用：

| 文件 | 用途 | 加载方式 |
| ---- | ---- | -------- |
| `face_detection_yunet_2023mar.onnx` | 人脸检测（YuNet，5 关键点） | OpenCV `FaceDetectorYN` |
| `ediffiqa_tiny_jun2024.onnx` | 人脸质量评分（eDifFIQA-T） | OpenCV `dnn.readNetFromONNX` |
| `sscd_disc_mixup.onnx` | 相似图/连拍描述子（SSCD，512 维） | onnxruntime（CPU） |

> 相似图检测的 `sscd_disc_mixup.onnx` 由官方 TorchScript 源文件转换而来。若需重新生成，运行 `backend/export_sscd.py`（需 64 位 Python + torch，仅准备阶段跑一次；运行期无需 torch）。官方源文件 `sscd_disc_mixup.torchscript.pt` 体积约 94MB，未随仓库分发，脚本会在缺失时自动下载。

## 项目结构

```
smart-media-sort/
├── backend/                 # FastAPI 后端
│   ├── main.py              # 应用入口 + 全部路由注册
│   ├── config.py            # 配置读写
│   ├── project.py           # 项目数据管理（id.json、步骤、路径解析）
│   ├── fileops.py           # 文件操作（安全删除、复制进度、系统对话框）
│   ├── media.py             # 媒体扫描、GPU 检测
│   ├── copy_service.py      # 后台复制服务
│   ├── batch.py             # 批处理（照片/视频压缩 + 水印，GPU 加速）
│   ├── filter_service.py    # 常规废片筛检（删格式/损坏文件）
│   ├── quality_filter.py    # 三级废片过滤（曝光 / 对焦 / 人脸质量，本地 DNN）
│   ├── burst_service.py     # 连拍/相似图分组检测（SSCD 描述子 + 顺序窗口）
│   ├── export_sscd.py       # 一次性把官方 SSCD 导出为 ONNX（准备阶段用）
│   ├── photo_index.py       # 照片索引构建 / 素材分流
│   ├── photo_classify.py    # 照片分类方案拆解（推理模型）
│   ├── photo_classify_exec.py # 照片分类执行（验收 AI + 分类 AI 编排）
│   ├── video_tagger.py      # 视频 AI 标签提取
│   ├── deliver_service.py   # 交付服务
│   ├── sse_service.py       # SSE 进度状态
│   └── models/              # 本地 ONNX 模型（YuNet / eDifFIQA / SSCD）
├── static/                  # 前端资源
│   ├── css/                 # 样式（国风视觉体系）
│   ├── js/                  # 前端逻辑
│   ├── icons/               # 步骤图标
│   └── images/              # 背景图等
├── templates/               # Jinja2 模板
│   ├── index.html           # 启动页
│   ├── main.html            # 素材库首页
│   ├── project.html         # 项目页
│   └── panels/              # 四步面板模板
├── requirements.txt         # Python 依赖
├── 启动.bat                 # Windows 一键启动脚本
└── README.md
```

## 常见问题

**Q：视频处理提示找不到 ffmpeg？**
A：在「设置」中填写 ffmpeg 可执行文件的完整路径，或将其加入系统 PATH。建议使用带 NVENC/QSV/AMF 硬件加速的构建以提升处理速度。

**Q：创建项目失败，提示 PermissionError？**
A：`work_dir` 指向的目录当前用户没有写入权限。请在工作目录设置中改选一个有写入权限的目录（如 `D:\素材`）。

**Q：拷贝进度条卡住不动？**
A：请勿在任务运行期间修改后端代码（服务已关闭自动重载）。若仍异常，重启后端服务即可。

**Q：可以接入其他 AI 服务吗？**
A：可以。只要服务兼容 OpenAI Chat Completions 协议，修改 `api_url`、`api_key`、`model` 即可。

**Q：三级过滤 / 连拍检测提示缺少依赖或模型？**
A：先执行 `pip install -r requirements.txt`（含 numpy、opencv-python-headless、onnxruntime），并确认 `backend/models/` 下的 ONNX 模型齐全。仅相似图检测所需的 `sscd_disc_mixup.onnx` 若缺失，可用 `backend/export_sscd.py` 重新导出。

**Q：三级过滤与连拍检测可以同时跑吗？**
A：不可以。两者互斥，一方运行期间另一方会置灰，需等待其完成，以免同时占用算力、相互干扰。

## 许可证

本项目基于 [MIT License](./LICENSE) 开源。

> 注意：仓库未包含 `static/fonts/` 目录下的字体文件（部分字体含版权限制），界面会自动回退到系统字体；`ffmpeg/` 目录亦未包含，请自行安装 ffmpeg。`backend/models/` 下的 ONNX 模型**随仓库分发**（已含用户转换的 `sscd_disc_mixup.onnx`）。

---

## 从 Release 下载一键包（开箱即用）

不想折腾环境？直接到 [Releases](https://github.com/TerryPanda3032/smart-media-sort/releases) 页面下载最新的一键包，解压后双击 `启动.bat` 即可运行，无需手动安装 Python 和依赖，开箱即用。

> 一键包已内置 Python 运行时与全部依赖，适合不熟悉命令行配置的用户。

## API URL 与 API Key 基础知识

本系统的 AI 能力通过 **OpenAI 兼容的 Chat Completions 接口**调用大模型，只需配置 `api_url` 和 `api_key` 两项即可接入。

### 什么是 API URL？

API URL 是 AI 服务的接口地址，即「在哪里调用模型」。系统默认使用 ModelScope（魔搭）平台的接口：

```
https://api-inference.modelscope.cn/v1/chat/completions
```

如果你使用其他兼容 OpenAI 协议的服务（如阿里云百炼、DeepSeek、本地 Ollama 等），只需把 `api_url` 换成对应服务的接口地址即可，无需改动代码。

### 什么是 API Key？

API Key 是调用 AI 服务的**身份凭证**（相当于你的账号密码），用于验证身份与计费。以 ModelScope 为例：

1. 注册并登录 [ModelScope 魔搭社区](https://www.modelscope.cn)；
2. 进入「个人中心 → API-KEY 管理」页面；
3. 点击「创建 API-KEY」，复制生成的密钥填入系统「设置」中的 `api_key` 字段。

> ⚠️ API Key 等同于资金凭证，请妥善保管，切勿提交到代码仓库或分享给他人。

### 推荐模型

推荐使用 **Qwen3.8-27B**，并在「设置」中将「思考强度」选为**中思考模式（medium）**，在效果与速度之间取得最佳平衡，适合照片分类、视频标签等日常整理场景。

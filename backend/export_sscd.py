# -*- coding: utf-8 -*-
"""一次性把官方 SSCD（sscd_disc_mixup）导出为 ONNX，供 burst_service 用 onnxruntime 加载。

只在准备阶段跑一次；运行期不需要 torch，也不需要本脚本。

【重要】必须用 64 位 Python 运行 —— PyTorch 不发布 32 位 Windows 版本。
先确认位数：python -c "import struct;print(struct.calcsize('P')*8)"
若本应用运行环境是 32 位 Python，请单独装一个 64 位 Python 来跑本脚本，导出后即可卸载：

    # 1) 装 64 位 Python（国内镜像，无需管理员）
    #    https://mirrors.huaweicloud.com/python/3.11.9/python-3.11.9-amd64.exe
    #    python-3.11.9-amd64.exe /quiet InstallAllUsers=0 TargetDir=D:\\py64 PrependPath=0 Include_launcher=0
    # 2) 用它的 pip 装依赖（国内源）
    D:\\py64\\python.exe -m pip install torch onnx numpy -i https://pypi.tuna.tsinghua.edu.cn/simple
    # 3) 导出
    D:\\py64\\python.exe backend/export_sscd.py

若运行环境本就是 64 位 Python，则直接：
    pip install torch onnx numpy -i https://pypi.tuna.tsinghua.edu.cn/simple
    python backend/export_sscd.py

成功后生成 backend/models/sscd_disc_mixup.onnx（约 94MB）。
若 dl.fbaipublicfiles.com 下载太慢，可自行用其他方式取得
  https://dl.fbaipublicfiles.com/sscd-copy-detection/sscd_disc_mixup.torchscript.pt
放到 backend/models/ 后重跑本脚本（检测到本地文件会直接使用，不再下载）。
"""

import os
import struct
import sys

MODEL_URL = "https://dl.fbaipublicfiles.com/sscd-copy-detection/sscd_disc_mixup.torchscript.pt"
INPUT_SIZE = 320        # 与 burst_service.SSCD_INPUT_SIZE 保持一致
OPSET = 17

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(HERE, "models")
PT_PATH = os.path.join(MODELS_DIR, "sscd_disc_mixup.torchscript.pt")
ONNX_PATH = os.path.join(MODELS_DIR, "sscd_disc_mixup.onnx")


def main() -> int:
    if struct.calcsize("P") * 8 == 32:
        print("错误：当前 Python 是 32 位，而 PyTorch 没有 32 位 Windows 版本。")
        print("请改用 64 位 Python 运行本脚本（见文件顶部说明）。")
        return 1
    try:
        import torch
    except ImportError:
        print("缺少 torch，请先安装：")
        print("  pip install torch onnx numpy -i https://pypi.tuna.tsinghua.edu.cn/simple")
        return 1

    os.makedirs(MODELS_DIR, exist_ok=True)

    if not os.path.isfile(PT_PATH):
        print(f"下载模型（约 100MB）: {MODEL_URL}")
        torch.hub.download_url_to_file(MODEL_URL, PT_PATH)
    else:
        print(f"使用本地模型: {PT_PATH}")

    print("加载 TorchScript 模型…")
    model = torch.jit.load(PT_PATH, map_location="cpu").eval()

    dummy = torch.zeros(1, 3, INPUT_SIZE, INPUT_SIZE)
    print(f"导出 ONNX（opset {OPSET}，输入 1×3×{INPUT_SIZE}×{INPUT_SIZE}）…")
    try:
        # 新版 PyTorch 默认走 dynamo 导出，ScriptModule 需显式关闭
        torch.onnx.export(model, dummy, ONNX_PATH,
                          input_names=["input"], output_names=["embedding"],
                          opset_version=OPSET, dynamo=False)
    except TypeError:
        torch.onnx.export(model, dummy, ONNX_PATH,
                          input_names=["input"], output_names=["embedding"],
                          opset_version=OPSET)

    print(f"完成: {ONNX_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
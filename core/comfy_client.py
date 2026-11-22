"""ComfyUI 客户端（阶段二实现，阶段一只留接口与契约）。

契约（阶段二必须满足）：
- submit(workflow_path, params) -> prompt_id：POST /prompt，返回排队 id
- progress(prompt_id) -> dict：轮询 GET /history/{prompt_id}，返回自定义节点输出
- free() -> None：显存不足时 POST /free（8 GB 显卡必须能在 finally 里调用）
"""
from typing import Any


class ComfyClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8188"):
        self.base_url = base_url.rstrip("/")

    def submit(self, workflow_path: str, params: dict[str, Any]) -> str:
        raise NotImplementedError("阶段二实现：POST /prompt")

    def progress(self, prompt_id: str) -> dict[str, Any]:
        raise NotImplementedError("阶段二实现：GET /history/{prompt_id}")

    def free(self) -> None:
        raise NotImplementedError("阶段二实现：POST /free（卸载模型）")

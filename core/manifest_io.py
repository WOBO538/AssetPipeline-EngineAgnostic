"""读 manifest 的最小接口（GUI 阶段一用：载入示例、列资产；阶段二接生成结果）。"""
import json
import os


def load(path: str) -> dict:
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)


def summarize(data: dict) -> list[str]:
    lines = [f"pipelineId={data.get('pipelineId')}  units={data.get('units')}  "
             f"assets={len(data.get('assets') or [])}"]
    for a in data.get("assets") or []:
        model = a.get("model") or {}
        lines.append(f"  {a.get('id')}  {a.get('category')}  tris={model.get('triCount')}  "
                     f"tex={len(a.get('textures') or {})}")
    return lines


def repo_root_from(manifest_path: str) -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(manifest_path)))

"""Expand a single JSON submission through the ordinary argparse validators."""

import json
import sys
from pathlib import Path

from .errors import WorkflowError


def expand(raw):
    if "--input-json" not in raw:
        return raw
    pos = raw.index("--input-json")
    if pos + 1 >= len(raw) or raw.count("--input-json") != 1:
        raise WorkflowError("--input-json 需要一个文件路径或 -")
    source = raw[pos + 1]
    if source == "-":
        # Redirected Windows stdin may use a legacy locale even when stdout is UTF-8.
        stream = getattr(sys.stdin, "buffer", None)
        content = stream.read().decode("utf-8-sig") if stream is not None else sys.stdin.read()
    else:
        content = Path(source).read_text(encoding="utf-8-sig")
    value = json.loads(content)
    if not isinstance(value, dict):
        raise WorkflowError("填报必须是 JSON 对象")
    args = raw[:pos] + raw[pos + 2:]
    if not any(command in args for command in ("start", "report", "end")):
        raise WorkflowError("--input-json 仅支持 start/report/end")
    for name, item in value.items():
        option = "--" + name.replace("_", "-")
        if option in args or any(arg.startswith(option + "=") for arg in args):
            raise WorkflowError(f"不能重复指定 {option}")
        if item is None:
            continue
        if isinstance(item, bool):
            if item:
                args.append(option)
        elif isinstance(item, list):
            if name == "reviews":
                args.extend([option, json.dumps(item, ensure_ascii=False)])
                continue
            for member in item:
                if not isinstance(member, str):
                    raise WorkflowError(f"{name} 列表必须包含字符串")
                args.extend([option, member])
        elif isinstance(item, dict):
            args.extend([option, json.dumps(item, ensure_ascii=False)])
        elif isinstance(item, str):
            args.extend([option, item])
        else:
            raise WorkflowError(f"{name} 类型不正确")
    return args

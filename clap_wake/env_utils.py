from __future__ import annotations

from pathlib import Path


def load_env_value(env_path: Path, key: str) -> str | None:
    if not env_path.exists():
        return None

    prefix = f"{key}="
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or not stripped.startswith(prefix):
            continue
        value = stripped[len(prefix) :].strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        return value
    return None


def save_env_value(env_path: Path, key: str, value: str) -> None:
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    prefix = f"{key}="
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    new_line = f'{key}="{escaped}"'
    replaced = False

    updated: list[str] = []
    for line in lines:
        if line.strip().startswith(prefix):
            updated.append(new_line)
            replaced = True
        else:
            updated.append(line)

    if not replaced:
        if updated and updated[-1].strip():
            updated.append("")
        updated.append(new_line)

    env_path.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")

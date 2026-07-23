from __future__ import annotations

import json
import platform

from isoworld.render.pyray_3d import inspect_pyray_3d_abi


def main() -> int:
    abi = inspect_pyray_3d_abi()
    print(
        json.dumps(
            {
                "binding_distribution": abi.binding_distribution,
                "binding_version": abi.binding_version,
                "evidence": "abi_and_function_surface_only",
                "header_components": list(abi.header_components),
                "header_version": abi.header_version,
                "host": platform.system(),
                "native_3d_verified": False,
                "required_functions": list(abi.required_functions),
                "rlgl_version": abi.rlgl_version,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

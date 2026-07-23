"""Compatibility exports for the stdlib runtime GLB validator."""

from isoworld.content.gltf import (
    BIN_CHUNK_TYPE,
    DEFAULT_ALLOWED_EXTENSIONS,
    GLB_MAGIC,
    GLB_VERSION,
    JSON_CHUNK_TYPE,
    MAX_GLB_BYTES,
    MAX_GLTF_JSON_BYTES,
    METRIC_NAMES,
    GLBError,
    inspect_glb,
)

__all__ = [
    "BIN_CHUNK_TYPE",
    "DEFAULT_ALLOWED_EXTENSIONS",
    "GLB_MAGIC",
    "GLB_VERSION",
    "JSON_CHUNK_TYPE",
    "MAX_GLB_BYTES",
    "MAX_GLTF_JSON_BYTES",
    "METRIC_NAMES",
    "GLBError",
    "inspect_glb",
]

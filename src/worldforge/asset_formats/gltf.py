from __future__ import annotations

import base64
import binascii
import json
import math
import os
import re
import stat
import struct
from collections.abc import Collection, Mapping
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

GLB_MAGIC = b"glTF"
GLB_VERSION = 2
JSON_CHUNK_TYPE = 0x4E4F534A
BIN_CHUNK_TYPE = 0x004E4942

MAX_GLB_BYTES = 512 * 1024 * 1024
MAX_GLTF_JSON_BYTES = 16 * 1024 * 1024

METRIC_NAMES = (
    "nodes",
    "meshes",
    "materials",
    "textures",
    "skins",
    "bones",
    "influences",
    "animations",
    "vertices",
    "triangles",
    "external_uris",
)

# These extensions only add declarative material, transform, quantization, or
# light data. Compressed-geometry extensions are intentionally absent: their
# decoder and expansion limits belong to an explicit runtime target contract.
DEFAULT_ALLOWED_EXTENSIONS = frozenset(
    {
        "KHR_lights_punctual",
        "KHR_materials_unlit",
        "KHR_mesh_quantization",
        "KHR_texture_transform",
    }
)

_COMPONENT_BYTES = {
    5120: 1,  # BYTE
    5121: 1,  # UNSIGNED_BYTE
    5122: 2,  # SHORT
    5123: 2,  # UNSIGNED_SHORT
    5125: 4,  # UNSIGNED_INT
    5126: 4,  # FLOAT
}
_TYPE_COMPONENTS = {
    "SCALAR": 1,
    "VEC2": 2,
    "VEC3": 3,
    "VEC4": 4,
    "MAT2": 4,
    "MAT3": 9,
    "MAT4": 16,
}
_UNSIGNED_COMPONENT_TYPES = frozenset({5121, 5123, 5125})

_FORBIDDEN_METADATA_KEY_PARTS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "authoring",
        "blender",
        "cookie",
        "credential",
        "mcp",
        "modly",
        "openai",
        "password",
        "private_key",
        "prompt",
        "provider",
        "receipt",
        "secret",
        "signed_url",
        "token",
        "workflow",
    }
)
_FORBIDDEN_METADATA_VALUE_MARKERS = (
    "blender",
    "mcp://",
    "modly",
    "openai",
    "provider_response",
    "raw transcript",
)
_SECRET_TEXT_PATTERN = re.compile(
    r"(?:\bbearer\s+[a-z0-9._~+/-]{8,}|"
    r"\bsk-[a-z0-9_-]{12,}|"
    r"\bAKIA[0-9A-Z]{16}\b|"
    r"-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY-----)",
    re.IGNORECASE,
)


class GLBError(ValueError):
    """Raised when a GLB is malformed or violates the safe handoff contract."""


class _DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _parse_finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"non-finite JSON number: {value}")
    return parsed


def _read_regular_file(path: Path, *, max_bytes: int) -> bytes:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OSError("not a standalone regular file")
        if info.st_size > max_bytes:
            raise OSError(f"exceeds the {max_bytes}-byte limit")
        with os.fdopen(descriptor, "rb") as source:
            descriptor = None
            data = source.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise OSError(f"exceeds the {max_bytes}-byte limit")
        return data
    except OSError as exc:
        raise GLBError(f"Could not read {path}: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _json_array(document: dict[str, Any], name: str) -> list[Any]:
    value = document.get(name, [])
    if not isinstance(value, list):
        raise GLBError(f"glTF {name} must be an array")
    return value


def _index(value: Any, length: int, *, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < length:
        raise GLBError(f"{context} references an invalid index")
    return value


def _integer(
    value: Any,
    *,
    context: str,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or (maximum is not None and value > maximum)
    ):
        bounds = f">= {minimum}" if maximum is None else f"in {minimum}..{maximum}"
        raise GLBError(f"{context} must be an integer {bounds}")
    return value


def _object_array(document: dict[str, Any], name: str) -> list[dict[str, Any]]:
    values = _json_array(document, name)
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            raise GLBError(f"glTF {name}[{index}] must be an object")
    return values


def _extension_names(document: dict[str, Any], name: str) -> tuple[str, ...]:
    values = document.get(name, [])
    if not isinstance(values, list):
        raise GLBError(f"glTF {name} must be an array")
    if not all(isinstance(value, str) and value for value in values):
        raise GLBError(f"glTF {name} must contain non-empty strings")
    if len(set(values)) != len(values):
        raise GLBError(f"glTF {name} contains duplicate names")
    return tuple(values)


def _extensions_in_payload(value: Any) -> set[str]:
    names: set[str] = set()
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            extensions = current.get("extensions")
            if extensions is not None:
                if not isinstance(extensions, dict):
                    raise GLBError("each glTF extensions value must be an object")
                for name in extensions:
                    if not isinstance(name, str) or not name:
                        raise GLBError("glTF extension names must be non-empty strings")
                    names.add(name)
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return names


def _validate_runtime_metadata(document: dict[str, Any]) -> None:
    """Reject authoring-only or credential-like metadata from a runtime GLB.

    ``extras`` is intentionally forbidden rather than interpreted. Blender and
    other exporters can place arbitrary custom properties there, so accepting it
    would make the neutral GLB a covert path for prompts, provider configuration,
    transcripts, or credentials.
    """

    pending: list[tuple[str | None, Any, str]] = [(None, document, "glTF")]
    while pending:
        parent_key, value, context = pending.pop()
        if isinstance(value, dict):
            for key, child in value.items():
                child_context = f"{context}.{key}"
                if key == "extras":
                    raise GLBError(f"{child_context} is forbidden in a runtime-safe GLB")
                normalized = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
                if any(part in normalized for part in _FORBIDDEN_METADATA_KEY_PARTS):
                    raise GLBError(f"{child_context} contains authoring/provider/secret metadata")
                pending.append((key, child, child_context))
        elif isinstance(value, list):
            pending.extend(
                (parent_key, child, f"{context}[{index}]") for index, child in enumerate(value)
            )
        elif isinstance(value, str) and parent_key != "uri":
            normalized_value = value.casefold()
            if any(marker in normalized_value for marker in _FORBIDDEN_METADATA_VALUE_MARKERS):
                raise GLBError(f"{context} contains authoring/provider metadata")
            if _SECRET_TEXT_PATTERN.search(value):
                raise GLBError(f"{context} contains credential-like metadata")


def _external_uri(uri: str) -> bool:
    return not uri.lower().startswith("data:")


def _validate_relative_uri(uri: str) -> None:
    if not uri or "\\" in uri or "\x00" in uri:
        raise GLBError(f"unsafe external URI: {uri!r}")
    parsed = urlsplit(uri)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise GLBError(f"external URI is not a plain relative path: {uri!r}")
    decoded = unquote(parsed.path)
    if "\\" in decoded or "\x00" in decoded:
        raise GLBError(f"unsafe external URI: {uri!r}")
    relative = PurePosixPath(decoded)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise GLBError(f"unsafe external URI: {uri!r}")


def _decode_data_uri(uri: str, *, collection: str) -> bytes:
    header, separator, encoded = uri.partition(",")
    if not separator or not header.lower().endswith(";base64"):
        raise GLBError(f"glTF {collection} data URI must use base64 encoding")
    media_type = header[5:].split(";", 1)[0].casefold()
    allowed = (
        {"application/octet-stream", "application/gltf-buffer"}
        if collection == "buffers"
        else {"image/jpeg", "image/png", "image/webp"}
    )
    if media_type not in allowed:
        raise GLBError(f"glTF {collection} data URI media type is not runtime-safe")
    try:
        return base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise GLBError(f"glTF {collection} data URI is not valid base64") from exc


def _collect_uris(document: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    external: list[str] = []
    embedded: list[str] = []
    for collection_name in ("buffers", "images"):
        for index, item in enumerate(_json_array(document, collection_name)):
            if not isinstance(item, dict):
                raise GLBError(f"glTF {collection_name}[{index}] must be an object")
            if "uri" not in item:
                continue
            uri = item["uri"]
            if not isinstance(uri, str) or not uri:
                raise GLBError(f"glTF {collection_name}[{index}].uri must be a string")
            if _external_uri(uri):
                _validate_relative_uri(uri)
                external.append(uri)
            else:
                _decode_data_uri(uri, collection=collection_name)
                embedded.append(uri)
    return tuple(external), tuple(embedded)


def _buffer_views(document: dict[str, Any]) -> list[dict[str, Any]]:
    buffers = _object_array(document, "buffers")
    views = _object_array(document, "bufferViews")
    for index, view in enumerate(views):
        context = f"glTF bufferViews[{index}]"
        buffer_index = _index(view.get("buffer"), len(buffers), context=f"{context}.buffer")
        offset = _integer(view.get("byteOffset", 0), context=f"{context}.byteOffset")
        length = _integer(view.get("byteLength"), context=f"{context}.byteLength", minimum=1)
        buffer_length = _integer(
            buffers[buffer_index].get("byteLength"),
            context=f"glTF buffers[{buffer_index}].byteLength",
            minimum=1,
        )
        if offset + length > buffer_length:
            raise GLBError(f"{context} range exceeds its buffer")
        if "byteStride" in view:
            stride = _integer(
                view["byteStride"],
                context=f"{context}.byteStride",
                minimum=4,
                maximum=252,
            )
            if stride % 4:
                raise GLBError(f"{context}.byteStride must be a multiple of four")
        if "target" in view:
            target = view["target"]
            if (
                isinstance(target, bool)
                or not isinstance(target, int)
                or target not in {34962, 34963}
            ):
                raise GLBError(f"{context}.target is invalid")
    return views


def _element_size(accessor_type: str, component_size: int) -> int:
    if not accessor_type.startswith("MAT"):
        return _TYPE_COMPONENTS[accessor_type] * component_size
    width = int(accessor_type[-1])
    column_size = width * component_size
    aligned_column_size = (column_size + 3) // 4 * 4
    return width * aligned_column_size


def _validate_numeric_bounds(
    accessor: dict[str, Any],
    *,
    context: str,
    component_count: int,
) -> None:
    for name in ("min", "max"):
        if name not in accessor:
            continue
        values = accessor[name]
        if (
            not isinstance(values, list)
            or len(values) != component_count
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in values
            )
        ):
            raise GLBError(f"{context}.{name} must contain one finite number per component")


def _validate_sparse_accessor(
    accessor: dict[str, Any],
    *,
    context: str,
    count: int,
    value_alignment: int,
    element_size: int,
    views: list[dict[str, Any]],
) -> None:
    sparse = accessor.get("sparse")
    if sparse is None:
        return
    if not isinstance(sparse, dict):
        raise GLBError(f"{context}.sparse must be an object")
    sparse_count = _integer(
        sparse.get("count"), context=f"{context}.sparse.count", minimum=1, maximum=count
    )
    indices = sparse.get("indices")
    values = sparse.get("values")
    if not isinstance(indices, dict) or not isinstance(values, dict):
        raise GLBError(f"{context}.sparse requires indices and values objects")

    index_view = _index(
        indices.get("bufferView"), len(views), context=f"{context}.sparse.indices.bufferView"
    )
    index_component_type = indices.get("componentType")
    if (
        isinstance(index_component_type, bool)
        or not isinstance(index_component_type, int)
        or index_component_type not in _UNSIGNED_COMPONENT_TYPES
    ):
        raise GLBError(f"{context}.sparse.indices.componentType is invalid")
    index_offset = _integer(
        indices.get("byteOffset", 0), context=f"{context}.sparse.indices.byteOffset"
    )
    index_bytes = _COMPONENT_BYTES[index_component_type]
    if (views[index_view].get("byteOffset", 0) + index_offset) % index_bytes:
        raise GLBError(f"{context}.sparse.indices.byteOffset is not component-aligned")
    if index_offset + sparse_count * index_bytes > views[index_view]["byteLength"]:
        raise GLBError(f"{context}.sparse.indices range exceeds its bufferView")

    value_view = _index(
        values.get("bufferView"), len(views), context=f"{context}.sparse.values.bufferView"
    )
    value_offset = _integer(
        values.get("byteOffset", 0), context=f"{context}.sparse.values.byteOffset"
    )
    if (views[value_view].get("byteOffset", 0) + value_offset) % value_alignment:
        raise GLBError(f"{context}.sparse.values.byteOffset is not component-aligned")
    if value_offset + sparse_count * element_size > views[value_view]["byteLength"]:
        raise GLBError(f"{context}.sparse.values range exceeds its bufferView")


def _accessors(document: dict[str, Any], views: list[dict[str, Any]]) -> list[dict[str, Any]]:
    accessors = _object_array(document, "accessors")
    for index, accessor in enumerate(accessors):
        context = f"glTF accessors[{index}]"
        component_type = accessor.get("componentType")
        if (
            isinstance(component_type, bool)
            or not isinstance(component_type, int)
            or component_type not in _COMPONENT_BYTES
        ):
            raise GLBError(f"{context}.componentType is invalid")
        accessor_type = accessor.get("type")
        if not isinstance(accessor_type, str) or accessor_type not in _TYPE_COMPONENTS:
            raise GLBError(f"{context}.type is invalid")
        count = _integer(accessor.get("count"), context=f"{context}.count", minimum=1)
        component_size = _COMPONENT_BYTES[component_type]
        element_size = _element_size(accessor_type, component_size)

        normalized = accessor.get("normalized", False)
        if not isinstance(normalized, bool):
            raise GLBError(f"{context}.normalized must be a boolean")
        if normalized and component_type not in {5120, 5121, 5122, 5123}:
            raise GLBError(f"{context}.normalized is invalid for its componentType")

        offset = _integer(accessor.get("byteOffset", 0), context=f"{context}.byteOffset")
        alignment = 4 if accessor_type.startswith("MAT") else component_size
        if offset % alignment and "bufferView" not in accessor:
            raise GLBError(f"{context}.byteOffset is not component-aligned")
        if "bufferView" in accessor:
            view_index = _index(accessor["bufferView"], len(views), context=f"{context}.bufferView")
            view = views[view_index]
            stride = view.get("byteStride", element_size)
            if (view.get("byteOffset", 0) + offset) % alignment:
                raise GLBError(f"{context}.byteOffset is not component-aligned")
            if stride < element_size:
                raise GLBError(f"{context} element is wider than its bufferView byteStride")
            if stride % alignment:
                raise GLBError(f"{context} bufferView byteStride is not component-aligned")
            required = offset + (count - 1) * stride + element_size
            if required > view["byteLength"]:
                raise GLBError(f"{context} range exceeds its bufferView")
        elif offset:
            raise GLBError(f"{context}.byteOffset requires a bufferView")

        _validate_numeric_bounds(
            accessor,
            context=context,
            component_count=_TYPE_COMPONENTS[accessor_type],
        )
        _validate_sparse_accessor(
            accessor,
            context=context,
            count=count,
            value_alignment=alignment,
            element_size=element_size,
            views=views,
        )
    return accessors


def _accessor(accessors: list[dict[str, Any]], index: Any, *, context: str) -> dict[str, Any]:
    try:
        return accessors[_index(index, len(accessors), context=context)]
    except GLBError as exc:
        raise GLBError(f"{context} references an invalid accessor") from exc


def _accessor_count(accessors: list[dict[str, Any]], index: Any, *, context: str) -> int:
    return _accessor(accessors, index, context=context)["count"]


def _attribute_type(semantic: str) -> set[str] | None:
    if semantic in {"POSITION", "NORMAL"}:
        return {"VEC3"}
    if semantic == "TANGENT" or semantic.startswith(("JOINTS_", "WEIGHTS_")):
        return {"VEC4"}
    if semantic.startswith("TEXCOORD_"):
        return {"VEC2"}
    if semantic.startswith("COLOR_"):
        return {"VEC3", "VEC4"}
    return None


def _geometry_metrics(
    document: dict[str, Any], accessors: list[dict[str, Any]]
) -> tuple[int, int, int]:
    vertices = 0
    triangles = 0
    max_influences = 0
    materials = _object_array(document, "materials")
    for mesh_index, mesh in enumerate(_object_array(document, "meshes")):
        primitives = mesh.get("primitives")
        if not isinstance(primitives, list) or not primitives:
            raise GLBError(f"glTF meshes[{mesh_index}].primitives must be a non-empty array")
        for primitive_index, primitive in enumerate(primitives):
            context = f"glTF meshes[{mesh_index}].primitives[{primitive_index}]"
            if not isinstance(primitive, dict):
                raise GLBError(f"{context} must be an object")
            attributes = primitive.get("attributes")
            if not isinstance(attributes, dict) or not attributes:
                raise GLBError(f"{context}.attributes must be a non-empty object")
            attribute_counts: set[int] = set()
            for semantic, accessor_index in attributes.items():
                if not isinstance(semantic, str) or not semantic:
                    raise GLBError(f"{context}.attributes has an invalid semantic")
                attribute_accessor = _accessor(
                    accessors,
                    accessor_index,
                    context=f"{context}.attributes.{semantic}",
                )
                expected_types = _attribute_type(semantic)
                if expected_types is not None and attribute_accessor["type"] not in expected_types:
                    raise GLBError(f"{context}.attributes.{semantic} accessor type is invalid")
                if semantic.startswith("JOINTS_") and (
                    attribute_accessor["componentType"] not in {5121, 5123}
                    or attribute_accessor.get("normalized", False)
                ):
                    raise GLBError(
                        f"{context}.attributes.{semantic} must use unnormalized unsigned "
                        "bytes or shorts"
                    )
                if semantic.startswith("WEIGHTS_") and not (
                    attribute_accessor["componentType"] == 5126
                    or (
                        attribute_accessor["componentType"] in {5121, 5123}
                        and attribute_accessor.get("normalized", False)
                    )
                ):
                    raise GLBError(
                        f"{context}.attributes.{semantic} must use floats or normalized "
                        "unsigned values"
                    )
                attribute_counts.add(attribute_accessor["count"])
            if len(attribute_counts) != 1:
                raise GLBError(f"{context} vertex attributes have inconsistent counts")
            weight_sets = {
                int(semantic.removeprefix("WEIGHTS_"))
                for semantic in attributes
                if semantic.startswith("WEIGHTS_") and semantic.removeprefix("WEIGHTS_").isdigit()
            }
            joint_sets = {
                int(semantic.removeprefix("JOINTS_"))
                for semantic in attributes
                if semantic.startswith("JOINTS_") and semantic.removeprefix("JOINTS_").isdigit()
            }
            if weight_sets != joint_sets:
                raise GLBError(f"{context} JOINTS and WEIGHTS attribute sets must match")
            if weight_sets and weight_sets != set(range(max(weight_sets) + 1)):
                raise GLBError(f"{context} JOINTS and WEIGHTS attribute sets must be contiguous")
            max_influences = max(max_influences, len(weight_sets) * 4)
            position = attributes.get("POSITION")
            vertex_count = _accessor_count(accessors, position, context=f"{context}.POSITION")
            vertices += vertex_count

            element_count = vertex_count
            if "indices" in primitive:
                element_count = _accessor_count(
                    accessors,
                    primitive["indices"],
                    context=f"{context}.indices",
                )
                index_accessor = _accessor(
                    accessors,
                    primitive["indices"],
                    context=f"{context}.indices",
                )
                if (
                    index_accessor["type"] != "SCALAR"
                    or index_accessor["componentType"] not in _UNSIGNED_COMPONENT_TYPES
                    or index_accessor.get("normalized", False)
                ):
                    raise GLBError(f"{context}.indices accessor is not an unsigned scalar")
            if "material" in primitive:
                _index(
                    primitive["material"],
                    len(materials),
                    context=f"{context}.material",
                )
            targets = primitive.get("targets", [])
            if not isinstance(targets, list):
                raise GLBError(f"{context}.targets must be an array")
            for target_index, target in enumerate(targets):
                if not isinstance(target, dict) or not target:
                    raise GLBError(f"{context}.targets[{target_index}] must be a non-empty object")
                for semantic, accessor_index in target.items():
                    if semantic not in {"POSITION", "NORMAL", "TANGENT"}:
                        raise GLBError(f"{context}.targets[{target_index}] semantic is invalid")
                    target_accessor = _accessor(
                        accessors,
                        accessor_index,
                        context=f"{context}.targets[{target_index}].{semantic}",
                    )
                    expected_types = _attribute_type(semantic)
                    if expected_types is not None and target_accessor["type"] not in expected_types:
                        raise GLBError(
                            f"{context}.targets[{target_index}].{semantic} type is invalid"
                        )
                    if target_accessor["count"] != vertex_count:
                        raise GLBError(f"{context}.targets[{target_index}] count is inconsistent")
            mode = primitive.get("mode", 4)
            if isinstance(mode, bool) or not isinstance(mode, int) or not 0 <= mode <= 6:
                raise GLBError(f"{context}.mode is invalid")
            if mode == 4:
                if element_count % 3:
                    raise GLBError(f"{context} triangle element count is not divisible by three")
                triangles += element_count // 3
            elif mode in {5, 6}:
                triangles += max(0, element_count - 2)
    return vertices, triangles, max_influences


def _metrics(document: dict[str, Any], accessors: list[dict[str, Any]]) -> dict[str, int]:
    metrics = {
        name: len(_json_array(document, name))
        for name in ("nodes", "meshes", "materials", "textures", "skins", "animations")
    }
    node_count = metrics["nodes"]
    bones = 0
    for index, skin in enumerate(_json_array(document, "skins")):
        if not isinstance(skin, dict):
            raise GLBError(f"glTF skins[{index}] must be an object")
        joints = skin.get("joints")
        if not isinstance(joints, list) or not joints:
            raise GLBError(f"glTF skins[{index}].joints must be a non-empty array")
        if any(
            isinstance(joint, bool) or not isinstance(joint, int) or not 0 <= joint < node_count
            for joint in joints
        ) or len(set(joints)) != len(joints):
            raise GLBError(f"glTF skins[{index}].joints contains invalid node references")
        bones += len(joints)
    metrics["bones"] = bones
    (
        metrics["vertices"],
        metrics["triangles"],
        metrics["influences"],
    ) = _geometry_metrics(document, accessors)
    metrics["external_uris"] = 0
    return metrics


def _number_array(value: Any, length: int, *, context: str) -> None:
    if (
        not isinstance(value, list)
        or len(value) != length
        or any(
            isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item)
            for item in value
        )
    ):
        raise GLBError(f"{context} must contain exactly {length} finite numbers")


def _texture_info(value: Any, texture_count: int, *, context: str) -> None:
    if not isinstance(value, dict):
        raise GLBError(f"{context} must be an object")
    _index(value.get("index"), texture_count, context=f"{context}.index")
    if "texCoord" in value:
        _integer(value["texCoord"], context=f"{context}.texCoord")


def _validate_material_references(document: dict[str, Any]) -> None:
    textures = _object_array(document, "textures")
    images = _object_array(document, "images")
    samplers = _object_array(document, "samplers")
    views = _object_array(document, "bufferViews")

    for index, image in enumerate(images):
        context = f"glTF images[{index}]"
        has_uri = "uri" in image
        has_view = "bufferView" in image
        if has_uri == has_view:
            raise GLBError(f"{context} must contain exactly one of uri or bufferView")
        if has_view:
            _index(image["bufferView"], len(views), context=f"{context}.bufferView")
            media_type = image.get("mimeType")
            if not isinstance(media_type, str) or media_type not in {
                "image/jpeg",
                "image/png",
                "image/webp",
            }:
                raise GLBError(f"{context}.mimeType is not runtime-safe")

    for index, texture in enumerate(textures):
        context = f"glTF textures[{index}]"
        if "source" in texture:
            _index(texture["source"], len(images), context=f"{context}.source")
        if "sampler" in texture:
            _index(texture["sampler"], len(samplers), context=f"{context}.sampler")

    texture_fields = ("normalTexture", "occlusionTexture", "emissiveTexture")
    for index, material in enumerate(_object_array(document, "materials")):
        context = f"glTF materials[{index}]"
        pbr = material.get("pbrMetallicRoughness")
        if pbr is not None:
            if not isinstance(pbr, dict):
                raise GLBError(f"{context}.pbrMetallicRoughness must be an object")
            for name in ("baseColorTexture", "metallicRoughnessTexture"):
                if name in pbr:
                    _texture_info(pbr[name], len(textures), context=f"{context}.{name}")
        for name in texture_fields:
            if name in material:
                _texture_info(material[name], len(textures), context=f"{context}.{name}")


def _validate_light_references(document: dict[str, Any]) -> None:
    root_extensions = document.get("extensions", {})
    if not isinstance(root_extensions, dict):
        raise GLBError("glTF extensions must be an object")
    light_extension = root_extensions.get("KHR_lights_punctual")
    lights: list[dict[str, Any]] = []
    if light_extension is not None:
        if not isinstance(light_extension, dict):
            raise GLBError("glTF KHR_lights_punctual extension must be an object")
        raw_lights = light_extension.get("lights")
        if not isinstance(raw_lights, list) or not raw_lights:
            raise GLBError("glTF KHR_lights_punctual.lights must be a non-empty array")
        for index, light in enumerate(raw_lights):
            if not isinstance(light, dict):
                raise GLBError(f"glTF KHR_lights_punctual.lights[{index}] must be an object")
            lights.append(light)

    for index, node in enumerate(_object_array(document, "nodes")):
        extensions = node.get("extensions", {})
        if not isinstance(extensions, dict):
            raise GLBError(f"glTF nodes[{index}].extensions must be an object")
        reference = extensions.get("KHR_lights_punctual")
        if reference is None:
            continue
        if not isinstance(reference, dict):
            raise GLBError(f"glTF nodes[{index}] KHR_lights_punctual must be an object")
        _index(
            reference.get("light"),
            len(lights),
            context=f"glTF nodes[{index}] KHR_lights_punctual.light",
        )


def _validate_node_references(document: dict[str, Any]) -> None:
    nodes = _object_array(document, "nodes")
    meshes = _object_array(document, "meshes")
    skins = _object_array(document, "skins")
    cameras = _object_array(document, "cameras")
    children_by_node: list[list[int]] = []
    parent_counts = [0] * len(nodes)

    for index, node in enumerate(nodes):
        context = f"glTF nodes[{index}]"
        if "name" in node and (not isinstance(node["name"], str) or not node["name"]):
            raise GLBError(f"{context}.name must be a non-empty string")
        for name, collection in (("mesh", meshes), ("skin", skins), ("camera", cameras)):
            if name in node:
                _index(node[name], len(collection), context=f"{context}.{name}")

        children = node.get("children", [])
        if not isinstance(children, list):
            raise GLBError(f"{context}.children must be an array")
        normalized_children = [
            _index(child, len(nodes), context=f"{context}.children") for child in children
        ]
        if (
            len(set(normalized_children)) != len(normalized_children)
            or index in normalized_children
        ):
            raise GLBError(f"{context}.children contains duplicate or self references")
        for child in normalized_children:
            parent_counts[child] += 1
            if parent_counts[child] > 1:
                raise GLBError(f"glTF nodes[{child}] has more than one parent")
        children_by_node.append(normalized_children)

        has_matrix = "matrix" in node
        if has_matrix:
            _number_array(node["matrix"], 16, context=f"{context}.matrix")
            if any(name in node for name in ("translation", "rotation", "scale")):
                raise GLBError(f"{context}.matrix cannot be combined with TRS transforms")
        for name, length in (("translation", 3), ("rotation", 4), ("scale", 3)):
            if name in node:
                _number_array(node[name], length, context=f"{context}.{name}")

    pending = [index for index, parent_count in enumerate(parent_counts) if parent_count == 0]
    visited_count = 0
    offset = 0
    while offset < len(pending):
        node_index = pending[offset]
        offset += 1
        visited_count += 1
        pending.extend(children_by_node[node_index])
    if visited_count != len(nodes):
        raise GLBError("glTF node hierarchy contains a cycle")

    scenes = _object_array(document, "scenes")
    for index, scene in enumerate(scenes):
        roots = scene.get("nodes", [])
        if not isinstance(roots, list):
            raise GLBError(f"glTF scenes[{index}].nodes must be an array")
        normalized_roots = [
            _index(root, len(nodes), context=f"glTF scenes[{index}].nodes") for root in roots
        ]
        if len(set(normalized_roots)) != len(normalized_roots):
            raise GLBError(f"glTF scenes[{index}].nodes contains duplicate references")
        if any(parent_counts[root] for root in normalized_roots):
            raise GLBError(f"glTF scenes[{index}].nodes contains a non-root node")
    if "scene" in document:
        _index(document["scene"], len(scenes), context="glTF scene")


def _validate_skin_references(document: dict[str, Any], accessors: list[dict[str, Any]]) -> None:
    nodes = _object_array(document, "nodes")
    for index, skin in enumerate(_object_array(document, "skins")):
        context = f"glTF skins[{index}]"
        if "skeleton" in skin:
            _index(skin["skeleton"], len(nodes), context=f"{context}.skeleton")
        if "inverseBindMatrices" in skin:
            inverse = _accessor(
                accessors,
                skin["inverseBindMatrices"],
                context=f"{context}.inverseBindMatrices",
            )
            if inverse["componentType"] != 5126 or inverse["type"] != "MAT4":
                raise GLBError(f"{context}.inverseBindMatrices must use FLOAT MAT4 values")
            joints = skin.get("joints")
            if isinstance(joints, list) and inverse["count"] < len(joints):
                raise GLBError(f"{context}.inverseBindMatrices has too few entries")


def _validate_animation_references(
    document: dict[str, Any], accessors: list[dict[str, Any]]
) -> None:
    nodes = _object_array(document, "nodes")
    for animation_index, animation in enumerate(_object_array(document, "animations")):
        context = f"glTF animations[{animation_index}]"
        if "name" in animation and (
            not isinstance(animation["name"], str) or not animation["name"]
        ):
            raise GLBError(f"{context}.name must be a non-empty string")
        samplers = animation.get("samplers")
        channels = animation.get("channels")
        if not isinstance(samplers, list) or not samplers:
            raise GLBError(f"{context}.samplers must be a non-empty array")
        if not isinstance(channels, list) or not channels:
            raise GLBError(f"{context}.channels must be a non-empty array")
        sampler_inputs: list[dict[str, Any]] = []
        sampler_outputs: list[dict[str, Any]] = []
        sampler_interpolations: list[str] = []
        for sampler_index, sampler in enumerate(samplers):
            sampler_context = f"{context}.samplers[{sampler_index}]"
            if not isinstance(sampler, dict):
                raise GLBError(f"{sampler_context} must be an object")
            input_accessor = _accessor(
                accessors, sampler.get("input"), context=f"{sampler_context}.input"
            )
            output_accessor = _accessor(
                accessors,
                sampler.get("output"),
                context=f"{sampler_context}.output",
            )
            if input_accessor["componentType"] != 5126 or input_accessor["type"] != "SCALAR":
                raise GLBError(f"{sampler_context}.input must use FLOAT SCALAR values")
            interpolation = sampler.get("interpolation", "LINEAR")
            if not isinstance(interpolation, str) or interpolation not in {
                "LINEAR",
                "STEP",
                "CUBICSPLINE",
            }:
                raise GLBError(f"{sampler_context}.interpolation is invalid")
            sampler_inputs.append(input_accessor)
            sampler_outputs.append(output_accessor)
            sampler_interpolations.append(interpolation)

        targets: set[tuple[int, str]] = set()
        for channel_index, channel in enumerate(channels):
            channel_context = f"{context}.channels[{channel_index}]"
            if not isinstance(channel, dict):
                raise GLBError(f"{channel_context} must be an object")
            sampler_index = _index(
                channel.get("sampler"),
                len(samplers),
                context=f"{channel_context}.sampler",
            )
            target = channel.get("target")
            if not isinstance(target, dict):
                raise GLBError(f"{channel_context}.target must be an object")
            node = _index(target.get("node"), len(nodes), context=f"{channel_context}.target.node")
            path = target.get("path")
            if not isinstance(path, str) or path not in {
                "translation",
                "rotation",
                "scale",
                "weights",
            }:
                raise GLBError(f"{channel_context}.target.path is invalid")
            output_accessor = sampler_outputs[sampler_index]
            expected_type = {
                "translation": "VEC3",
                "rotation": "VEC4",
                "scale": "VEC3",
                "weights": "SCALAR",
            }[path]
            if output_accessor["componentType"] != 5126 or output_accessor["type"] != expected_type:
                raise GLBError(
                    f"{channel_context} output accessor must use FLOAT {expected_type} values"
                )
            if path != "weights":
                multiplier = 3 if sampler_interpolations[sampler_index] == "CUBICSPLINE" else 1
                if output_accessor["count"] != sampler_inputs[sampler_index]["count"] * multiplier:
                    raise GLBError(f"{channel_context} input and output counts are inconsistent")
            target_key = (node, path)
            if target_key in targets:
                raise GLBError(f"{context} contains duplicate channel targets")
            targets.add(target_key)


def _required_names(value: Collection[str], *, context: str) -> set[str]:
    if (
        isinstance(value, (str, bytes))
        or isinstance(value, Mapping)
        or not isinstance(value, Collection)
    ):
        raise GLBError(f"{context} must be a collection of non-empty strings")
    if not all(isinstance(name, str) and name for name in value):
        raise GLBError(f"{context} must contain non-empty strings")
    return set(value)


def _validate_required_names(
    document: dict[str, Any],
    *,
    required_node_names: Collection[str],
    required_animation_names: Collection[str],
) -> None:
    for collection_name, required in (
        ("nodes", _required_names(required_node_names, context="required_node_names")),
        (
            "animations",
            _required_names(required_animation_names, context="required_animation_names"),
        ),
    ):
        if not required:
            continue
        counts: dict[str, int] = {}
        for entry in _object_array(document, collection_name):
            name = entry.get("name")
            if isinstance(name, str) and name:
                counts[name] = counts.get(name, 0) + 1
        missing = required - counts.keys()
        ambiguous = {name for name in required if counts.get(name, 0) > 1}
        if missing:
            raise GLBError(
                f"GLB is missing required {collection_name} names: {', '.join(sorted(missing))}"
            )
        if ambiguous:
            raise GLBError(
                f"GLB has ambiguous required {collection_name} names: "
                f"{', '.join(sorted(ambiguous))}"
            )


def _normalized_budgets(budgets: Mapping[str, int] | None) -> dict[str, int]:
    if budgets is None:
        return {}
    if not isinstance(budgets, Mapping):
        raise GLBError("GLB budgets must be an object")
    result: dict[str, int] = {}
    for raw_name, value in budgets.items():
        if not isinstance(raw_name, str):
            raise GLBError("GLB budget names must be strings")
        name = raw_name.removeprefix("max_")
        if name not in METRIC_NAMES:
            raise GLBError(f"unknown GLB budget: {raw_name}")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise GLBError(f"GLB budget {raw_name} must be a non-negative integer")
        if name in result and result[name] != value:
            raise GLBError(f"conflicting GLB budgets for {name}")
        result[name] = value
    return result


def _validate_binary_buffer(document: dict[str, Any], binary: bytes | None) -> None:
    buffers = _object_array(document, "buffers")
    for index, buffer in enumerate(buffers):
        byte_length = _integer(
            buffer.get("byteLength"),
            context=f"glTF buffers[{index}].byteLength",
            minimum=1,
        )
        uri = buffer.get("uri")
        if isinstance(uri, str) and uri.lower().startswith("data:"):
            if len(_decode_data_uri(uri, collection="buffers")) != byte_length:
                raise GLBError(f"glTF buffers[{index}] data length does not match byteLength")
    if binary is None:
        for buffer in buffers:
            if "uri" not in buffer:
                raise GLBError("a buffer without a URI requires a BIN chunk")
        return
    if not buffers or not isinstance(buffers[0], dict) or "uri" in buffers[0]:
        raise GLBError("the GLB BIN chunk requires buffers[0] without a URI")
    byte_length = buffers[0].get("byteLength")
    assert isinstance(byte_length, int)
    padding = len(binary) - byte_length
    if padding < 0 or padding > 3:
        raise GLBError("the BIN chunk length does not match buffers[0].byteLength")
    if any(binary[byte_length:]):
        raise GLBError("the GLB BIN chunk has non-zero padding bytes")
    for index, buffer in enumerate(buffers[1:], start=1):
        if "uri" not in buffer:
            raise GLBError(f"glTF buffers[{index}] requires a URI")


def _used_accessor_indices(document: dict[str, Any], accessor_count: int) -> set[int]:
    used: set[int] = set()
    for mesh_index, mesh in enumerate(_object_array(document, "meshes")):
        primitives = mesh.get("primitives", [])
        assert isinstance(primitives, list)
        for primitive_index, primitive in enumerate(primitives):
            assert isinstance(primitive, dict)
            context = f"glTF meshes[{mesh_index}].primitives[{primitive_index}]"
            attributes = primitive.get("attributes", {})
            assert isinstance(attributes, dict)
            for semantic, accessor_index in attributes.items():
                used.add(
                    _index(
                        accessor_index,
                        accessor_count,
                        context=f"{context}.attributes.{semantic}",
                    )
                )
            if "indices" in primitive:
                used.add(
                    _index(
                        primitive["indices"],
                        accessor_count,
                        context=f"{context}.indices",
                    )
                )
            targets = primitive.get("targets", [])
            assert isinstance(targets, list)
            for target_index, target in enumerate(targets):
                assert isinstance(target, dict)
                for semantic, accessor_index in target.items():
                    used.add(
                        _index(
                            accessor_index,
                            accessor_count,
                            context=f"{context}.targets[{target_index}].{semantic}",
                        )
                    )

    for skin_index, skin in enumerate(_object_array(document, "skins")):
        if "inverseBindMatrices" in skin:
            used.add(
                _index(
                    skin["inverseBindMatrices"],
                    accessor_count,
                    context=f"glTF skins[{skin_index}].inverseBindMatrices",
                )
            )

    for animation_index, animation in enumerate(_object_array(document, "animations")):
        samplers = animation.get("samplers", [])
        assert isinstance(samplers, list)
        for sampler_index, sampler in enumerate(samplers):
            assert isinstance(sampler, dict)
            context = f"glTF animations[{animation_index}].samplers[{sampler_index}]"
            for name in ("input", "output"):
                used.add(
                    _index(
                        sampler[name],
                        accessor_count,
                        context=f"{context}.{name}",
                    )
                )
    return used


def _validate_binary_reachability(
    document: dict[str, Any],
    binary: bytes | None,
    views: list[dict[str, Any]],
    accessors: list[dict[str, Any]],
) -> None:
    """Reject semantically unreachable buffer payloads and covert BIN bytes."""

    used_accessors = _used_accessor_indices(document, len(accessors))
    unused_accessors = set(range(len(accessors))) - used_accessors
    if unused_accessors:
        rendered = ", ".join(str(index) for index in sorted(unused_accessors))
        raise GLBError(f"glTF contains unreferenced accessors: {rendered}")

    used_views: set[int] = set()
    for accessor_index in used_accessors:
        accessor = accessors[accessor_index]
        if "bufferView" in accessor:
            used_views.add(
                _index(
                    accessor["bufferView"],
                    len(views),
                    context=f"glTF accessors[{accessor_index}].bufferView",
                )
            )
        sparse = accessor.get("sparse")
        if isinstance(sparse, dict):
            indices = sparse.get("indices")
            values = sparse.get("values")
            assert isinstance(indices, dict) and isinstance(values, dict)
            used_views.add(
                _index(
                    indices["bufferView"],
                    len(views),
                    context=f"glTF accessors[{accessor_index}].sparse.indices.bufferView",
                )
            )
            used_views.add(
                _index(
                    values["bufferView"],
                    len(views),
                    context=f"glTF accessors[{accessor_index}].sparse.values.bufferView",
                )
            )

    for image_index, image in enumerate(_object_array(document, "images")):
        if "bufferView" in image:
            used_views.add(
                _index(
                    image["bufferView"],
                    len(views),
                    context=f"glTF images[{image_index}].bufferView",
                )
            )

    unused_views = set(range(len(views))) - used_views
    if unused_views:
        rendered = ", ".join(str(index) for index in sorted(unused_views))
        raise GLBError(f"glTF contains unreferenced bufferViews: {rendered}")
    if binary is None:
        return

    intervals = sorted(
        (
            view.get("byteOffset", 0),
            view.get("byteOffset", 0) + view["byteLength"],
        )
        for index, view in enumerate(views)
        if index in used_views and view["buffer"] == 0
    )
    if not intervals:
        raise GLBError("the GLB BIN chunk is not referenced by runtime data")

    declared_length = _object_array(document, "buffers")[0]["byteLength"]
    assert isinstance(declared_length, int)
    covered_until = 0
    for start, end in intervals:
        if start > covered_until and binary.count(b"\0", covered_until, start) != (
            start - covered_until
        ):
            raise GLBError("the GLB BIN chunk contains unreferenced non-zero bytes")
        covered_until = max(covered_until, end)
    if covered_until < declared_length and binary.count(b"\0", covered_until, declared_length) != (
        declared_length - covered_until
    ):
        raise GLBError("the GLB BIN chunk contains unreferenced non-zero bytes")


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    if not data.startswith(b"\xff\xd8"):
        return None
    offset = 2
    start_of_frame = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            return None
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            return None
        marker = data[offset]
        offset += 1
        if marker in {0x01, *range(0xD0, 0xD9)}:
            continue
        if offset + 2 > len(data):
            return None
        length = int.from_bytes(data[offset : offset + 2], "big")
        if length < 2 or offset + length > len(data):
            return None
        if marker in start_of_frame:
            if length < 7:
                return None
            height = int.from_bytes(data[offset + 3 : offset + 5], "big")
            width = int.from_bytes(data[offset + 5 : offset + 7], "big")
            return width, height
        offset += length
    return None


def _webp_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 20 or not data.startswith(b"RIFF") or data[8:12] != b"WEBP":
        return None
    kind = data[12:16]
    payload = data[20:]
    if kind == b"VP8X" and len(payload) >= 10:
        width = int.from_bytes(payload[4:7], "little") + 1
        height = int.from_bytes(payload[7:10], "little") + 1
        return width, height
    if kind == b"VP8L" and len(payload) >= 5 and payload[0] == 0x2F:
        width = 1 + payload[1] + ((payload[2] & 0x3F) << 8)
        height = 1 + (payload[2] >> 6) + (payload[3] << 2) + ((payload[4] & 0x0F) << 10)
        return width, height
    if kind == b"VP8 " and len(payload) >= 10 and payload[3:6] == b"\x9d\x01\x2a":
        width = int.from_bytes(payload[6:8], "little") & 0x3FFF
        height = int.from_bytes(payload[8:10], "little") & 0x3FFF
        return width, height
    return None


def _image_dimensions(data: bytes, media_type: str) -> tuple[int, int]:
    dimensions: tuple[int, int] | None = None
    if media_type == "image/png" and len(data) >= 24 and data.startswith(b"\x89PNG\r\n\x1a\n"):
        dimensions = struct.unpack(">II", data[16:24])
    elif media_type == "image/jpeg":
        dimensions = _jpeg_dimensions(data)
    elif media_type == "image/webp":
        dimensions = _webp_dimensions(data)
    if dimensions is None or dimensions[0] <= 0 or dimensions[1] <= 0:
        raise GLBError(f"embedded {media_type} image has an invalid header")
    return dimensions


def _max_texture_dimension(
    document: dict[str, Any],
    binary: bytes | None,
) -> int:
    buffer_views = _json_array(document, "bufferViews")
    maximum = 0
    for index, image in enumerate(_json_array(document, "images")):
        if not isinstance(image, dict):
            raise GLBError(f"glTF images[{index}] must be an object")
        media_type = image.get("mimeType")
        data: bytes
        uri = image.get("uri")
        if isinstance(uri, str) and uri.lower().startswith("data:"):
            data = _decode_data_uri(uri, collection="images")
            declared = uri[5:].split(";", 1)[0].casefold()
            if media_type is not None and media_type != declared:
                raise GLBError(f"glTF images[{index}] data URI and mimeType disagree")
            media_type = declared
        elif "bufferView" in image:
            view_index = image["bufferView"]
            if (
                isinstance(view_index, bool)
                or not isinstance(view_index, int)
                or not 0 <= view_index < len(buffer_views)
            ):
                raise GLBError(f"glTF images[{index}] references an invalid bufferView")
            view = buffer_views[view_index]
            if not isinstance(view, dict) or view.get("buffer") != 0 or binary is None:
                raise GLBError(f"glTF images[{index}] bufferView is not in the GLB BIN chunk")
            offset = view.get("byteOffset", 0)
            length = view.get("byteLength")
            if (
                isinstance(offset, bool)
                or not isinstance(offset, int)
                or offset < 0
                or isinstance(length, bool)
                or not isinstance(length, int)
                or length <= 0
                or offset + length > len(binary)
            ):
                raise GLBError(f"glTF images[{index}] bufferView range is invalid")
            data = binary[offset : offset + length]
        else:
            # External images have already been rejected by the default policy;
            # callers that allow them cannot derive a trustworthy dimension.
            continue
        if not isinstance(media_type, str) or media_type not in {
            "image/jpeg",
            "image/png",
            "image/webp",
        }:
            raise GLBError(f"glTF images[{index}].mimeType is not runtime-safe")
        maximum = max(maximum, *_image_dimensions(data, media_type))
    return maximum


def inspect_glb(
    path: str | Path,
    *,
    allowed_extensions: Collection[str] = DEFAULT_ALLOWED_EXTENSIONS,
    allow_external_uris: bool = False,
    budgets: Mapping[str, int] | None = None,
    max_bytes: int = MAX_GLB_BYTES,
    required_node_names: Collection[str] = (),
    required_animation_names: Collection[str] = (),
) -> dict[str, Any]:
    """Inspect a GLB without invoking Blender, a renderer, or extension code.

    The returned dictionary contains only structural facts derived from the
    container and its JSON document. ``required_node_names`` and
    ``required_animation_names`` can bind runtime entrypoints without adding
    provider data to the handoff. Any malformed container, dangling reference,
    undeclared or disallowed extension, unsafe URI, or exceeded budget raises
    ``GLBError``.
    """

    if not isinstance(allow_external_uris, bool):
        raise GLBError("allow_external_uris must be a boolean")
    max_bytes = _integer(
        max_bytes,
        context="max_bytes",
        minimum=1,
        maximum=MAX_GLB_BYTES,
    )
    allowed = _required_names(allowed_extensions, context="allowed_extensions")
    normalized_budgets = _normalized_budgets(budgets)
    required_nodes = _required_names(required_node_names, context="required_node_names")
    required_animations = _required_names(
        required_animation_names,
        context="required_animation_names",
    )

    source = Path(path)
    data = _read_regular_file(source, max_bytes=max_bytes)
    if len(data) < 20:
        raise GLBError("GLB is shorter than its header and JSON chunk")
    magic, version, declared_length = struct.unpack_from("<4sII", data)
    if magic != GLB_MAGIC:
        raise GLBError("invalid GLB magic")
    if version != GLB_VERSION:
        raise GLBError(f"unsupported GLB version: {version}")
    if declared_length != len(data):
        raise GLBError("GLB header length does not match the file")

    chunks: list[tuple[int, bytes]] = []
    offset = 12
    while offset < len(data):
        if len(data) - offset < 8:
            raise GLBError("truncated GLB chunk header")
        chunk_length, chunk_type = struct.unpack_from("<II", data, offset)
        offset += 8
        if chunk_length % 4:
            raise GLBError("GLB chunk length is not four-byte aligned")
        end = offset + chunk_length
        if end > len(data):
            raise GLBError("GLB chunk exceeds the declared file length")
        chunks.append((chunk_type, data[offset:end]))
        offset = end
    if not chunks or chunks[0][0] != JSON_CHUNK_TYPE:
        raise GLBError("the first GLB chunk must be JSON")
    expected_types = (JSON_CHUNK_TYPE, BIN_CHUNK_TYPE)
    if len(chunks) > 2 or any(
        chunk_type != expected_types[index] for index, (chunk_type, _) in enumerate(chunks)
    ):
        raise GLBError("GLB may contain exactly one JSON chunk and at most one BIN chunk")

    json_bytes = chunks[0][1]
    if not json_bytes or len(json_bytes) > MAX_GLTF_JSON_BYTES:
        raise GLBError(f"GLB JSON chunk must be in 1..{MAX_GLTF_JSON_BYTES} bytes")
    try:
        document = json.loads(
            json_bytes.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
            parse_float=_parse_finite_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise GLBError(f"invalid GLB JSON chunk: {exc}") from exc
    if not isinstance(document, dict):
        raise GLBError("GLB JSON chunk must contain an object")
    asset = document.get("asset")
    if not isinstance(asset, dict) or asset.get("version") != "2.0":
        raise GLBError("GLB JSON must declare asset.version 2.0")
    _validate_runtime_metadata(document)

    used = _extension_names(document, "extensionsUsed")
    required = _extension_names(document, "extensionsRequired")
    if not set(required).issubset(used):
        raise GLBError("extensionsRequired must be a subset of extensionsUsed")
    payload_extensions = _extensions_in_payload(document)
    undeclared = payload_extensions - set(used)
    if undeclared:
        raise GLBError(f"extension payload is not declared: {', '.join(sorted(undeclared))}")
    disallowed = (set(used) | set(required)) - allowed
    if disallowed:
        raise GLBError(f"GLB uses disallowed extensions: {', '.join(sorted(disallowed))}")

    external_uris, embedded_uris = _collect_uris(document)
    if external_uris and not allow_external_uris:
        raise GLBError(f"GLB contains external URIs: {', '.join(external_uris)}")
    binary = chunks[1][1] if len(chunks) == 2 else None
    binary_length = len(binary) if binary is not None else None
    _validate_binary_buffer(document, binary)
    views = _buffer_views(document)
    accessors = _accessors(document, views)
    _validate_node_references(document)
    _validate_skin_references(document, accessors)
    _validate_animation_references(document, accessors)
    _validate_material_references(document)
    _validate_light_references(document)
    _validate_required_names(
        document,
        required_node_names=required_nodes,
        required_animation_names=required_animations,
    )
    max_texture_dimension = _max_texture_dimension(document, binary)

    metrics = _metrics(document, accessors)
    _validate_binary_reachability(document, binary, views, accessors)
    metrics["external_uris"] = len(external_uris)
    for name, maximum in normalized_budgets.items():
        if metrics[name] > maximum:
            raise GLBError(f"GLB {name} budget exceeded: {metrics[name]} > {maximum}")

    return {
        "byte_length": len(data),
        "json_chunk_bytes": len(json_bytes),
        "bin_chunk_bytes": binary_length or 0,
        "extensions_used": list(used),
        "extensions_required": list(required),
        "external_uris": list(external_uris),
        "embedded_uris": len(embedded_uris),
        "max_texture_dimension": max_texture_dimension,
        "metrics": metrics,
    }

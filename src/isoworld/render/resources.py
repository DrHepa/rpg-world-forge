from __future__ import annotations

import hashlib
from typing import Any

from isoworld.content.renderpack import RenderAsset, RenderBinding, RenderPack
from isoworld.render.render_state import RenderState


class ResourceError(RuntimeError):
    """Raised when raylib cannot prepare a validated processed resource."""


class RaylibAssetRegistry:
    """Load native resources while borrowing, never closing, the supplied renderpack."""

    def __init__(self, pr: Any, renderpack: RenderPack) -> None:
        self.pr = pr
        self.renderpack = renderpack
        self.textures: dict[str, tuple[Any, ...]] = {}
        self.fonts: dict[str, Any] = {}
        self.sounds: dict[str, tuple[Any, ...]] = {}
        self.music: dict[str, tuple[Any, ...]] = {}
        self.shaders: dict[str, Any] = {}
        self._audio_initialized = False
        self._loaded = False
        self._last_audio_revision = -1
        self._current_music_asset: str | None = None
        self._current_music: Any | None = None

    def _paths(self, asset: RenderAsset, role: str) -> tuple[str, ...]:
        return tuple(str(self.renderpack.resolve_file(item)) for item in asset.files_for(role))

    def _require_valid(self, value: Any, validator_name: str, description: str) -> Any:
        validator = getattr(self.pr, validator_name, None)
        if callable(validator) and not validator(value):
            raise ResourceError(f"raylib rejected {description}")
        return value

    def _try_release(
        self,
        function_name: str,
        value: Any,
        description: str,
        errors: list[str],
    ) -> bool:
        try:
            getattr(self.pr, function_name)(value)
        except Exception as exc:
            errors.append(f"{description}: {exc}")
            return False
        return True

    def load(self) -> None:
        if self._loaded:
            return
        needs_audio = any(asset.kind in {"music", "sfx"} for asset in self.renderpack.assets)
        try:
            if needs_audio:
                self.pr.init_audio_device()
                self._audio_initialized = True
                ready = getattr(self.pr, "is_audio_device_ready", None)
                if callable(ready) and not ready():
                    raise ResourceError("raylib could not initialize the audio device")
            for asset in self.renderpack.assets:
                if asset.kind in {"portrait", "sprite", "spritesheet", "tileset", "ui", "vfx"}:
                    paths = self._paths(asset, "texture")
                    loaded_textures: list[Any] = []
                    self.textures[asset.id] = ()
                    for path in paths:
                        loaded_textures.append(
                            self._require_valid(
                                self.pr.load_texture(path),
                                "is_texture_valid",
                                f"texture {asset.id}",
                            )
                        )
                        self.textures[asset.id] = tuple(loaded_textures)
                    self._validate_clip_bounds(asset)
                elif asset.kind == "font":
                    paths = self._paths(asset, "font")
                    if paths:
                        self.fonts[asset.id] = self._require_valid(
                            self.pr.load_font(paths[0]),
                            "is_font_valid",
                            f"font {asset.id}",
                        )
                elif asset.kind == "sfx":
                    loaded_sounds: list[Any] = []
                    self.sounds[asset.id] = ()
                    for path in self._paths(asset, "audio"):
                        loaded_sounds.append(
                            self._require_valid(
                                self.pr.load_sound(path),
                                "is_sound_valid",
                                f"sound {asset.id}",
                            )
                        )
                        self.sounds[asset.id] = tuple(loaded_sounds)
                elif asset.kind == "music":
                    loaded_music: list[Any] = []
                    self.music[asset.id] = ()
                    for path in self._paths(asset, "audio"):
                        loaded_music.append(
                            self._require_valid(
                                self.pr.load_music_stream(path),
                                "is_music_valid",
                                f"music {asset.id}",
                            )
                        )
                        self.music[asset.id] = tuple(loaded_music)
                elif asset.kind == "shader":
                    vertex = self._paths(asset, "vertex_shader")
                    fragment = self._paths(asset, "fragment_shader")
                    self.shaders[asset.id] = self._require_valid(
                        self.pr.load_shader(
                            vertex[0] if vertex else None,
                            fragment[0] if fragment else None,
                        ),
                        "is_shader_valid",
                        f"shader {asset.id}",
                    )
            self._loaded = True
        except Exception as exc:
            cleanup_error: ResourceError | None = None
            try:
                self.close()
            except ResourceError as caught:
                cleanup_error = caught
            detail = f"Could not load processed renderpack resources: {exc}"
            if cleanup_error is not None:
                detail += f"; cleanup failures: {cleanup_error}"
            raise ResourceError(detail) from exc

    def _validate_clip_bounds(self, asset: RenderAsset) -> None:
        textures = self.textures.get(asset.id, ())
        if not textures or not asset.clips:
            return
        texture = textures[0]
        width = getattr(texture, "width", None)
        height = getattr(texture, "height", None)
        if not isinstance(width, int) or not isinstance(height, int):
            return
        for clip in asset.clips:
            for frame in clip.frames:
                if frame.x + frame.width > width or frame.y + frame.height > height:
                    raise ResourceError(
                        f"Clip {asset.id}:{clip.id} exceeds its {width}x{height} texture"
                    )

    def close(self) -> None:
        errors: list[str] = []
        if self._current_music is not None:
            self._try_release(
                "stop_music_stream",
                self._current_music,
                "stop current music",
                errors,
            )

        for asset_id in reversed(tuple(self.shaders)):
            if self._try_release(
                "unload_shader",
                self.shaders[asset_id],
                f"unload shader {asset_id}",
                errors,
            ):
                self.shaders.pop(asset_id)

        sequence_stores = (
            (self.music, "unload_music_stream", "music"),
            (self.sounds, "unload_sound", "sound"),
        )
        for store, function_name, kind in sequence_stores:
            for asset_id in reversed(tuple(store)):
                remaining = list(store[asset_id])
                for index in range(len(remaining) - 1, -1, -1):
                    if self._try_release(
                        function_name,
                        remaining[index],
                        f"unload {kind} {asset_id}/{index}",
                        errors,
                    ):
                        remaining.pop(index)
                if remaining:
                    store[asset_id] = tuple(remaining)
                else:
                    store.pop(asset_id)

        for asset_id in reversed(tuple(self.fonts)):
            if self._try_release(
                "unload_font",
                self.fonts[asset_id],
                f"unload font {asset_id}",
                errors,
            ):
                self.fonts.pop(asset_id)

        for asset_id in reversed(tuple(self.textures)):
            remaining = list(self.textures[asset_id])
            for index in range(len(remaining) - 1, -1, -1):
                if self._try_release(
                    "unload_texture",
                    remaining[index],
                    f"unload texture {asset_id}/{index}",
                    errors,
                ):
                    remaining.pop(index)
            if remaining:
                self.textures[asset_id] = tuple(remaining)
            else:
                self.textures.pop(asset_id)

        current_still_owned = self._current_music is not None and any(
            item is self._current_music for items in self.music.values() for item in items
        )
        if not current_still_owned:
            self._current_music = None
            self._current_music_asset = None

        if self._audio_initialized and not self.music and not self.sounds:
            try:
                self.pr.close_audio_device()
            except Exception as exc:
                errors.append(f"close audio device: {exc}")
            else:
                self._audio_initialized = False
        self._loaded = bool(
            self.shaders
            or self.music
            or self.sounds
            or self.fonts
            or self.textures
            or self._audio_initialized
        )
        if errors:
            raise ResourceError("Resource cleanup failed: " + "; ".join(errors))

    def binding(self, slot: str) -> RenderBinding | None:
        return self.renderpack.binding(slot)

    def layer_for(self, slot: str) -> int:
        binding = self.binding(slot)
        return binding.layer if binding is not None else 0

    def draw_binding(
        self,
        binding: RenderBinding,
        *,
        anchor_x: float,
        anchor_y: float,
        tick: int,
        moving: bool = False,
        tint: Any | None = None,
    ) -> bool:
        asset = self.renderpack.asset(binding.asset_id)
        textures = self.textures.get(asset.id, ())
        clip_id = binding.moving_clip if moving and binding.moving_clip else binding.clip
        if not textures or clip_id is None:
            return False
        clip = asset.clip(clip_id)
        frame = clip.frame_at(tick)
        scale = binding.scale
        source = self.pr.Rectangle(frame.x, frame.y, frame.width, frame.height)
        destination = self.pr.Rectangle(
            anchor_x,
            anchor_y,
            frame.width * scale,
            frame.height * scale,
        )
        origin = self.pr.Vector2(clip.pivot_x * scale, clip.pivot_y * scale)
        self.pr.draw_texture_pro(
            textures[0],
            source,
            destination,
            origin,
            0.0,
            tint if tint is not None else self.pr.WHITE,
        )
        return True

    def default_font(self) -> Any | None:
        binding = self.binding("ui:font")
        return self.fonts.get(binding.asset_id) if binding is not None else None

    def _event_binding(self, kind: str, subject_id: str | None) -> RenderBinding | None:
        if subject_id:
            specific = self.binding(f"event:{kind}:{subject_id}")
            if specific is not None:
                return specific
        return self.binding(f"event:{kind}")

    def _select_variation(self, values: tuple[Any, ...], key: str) -> Any | None:
        if not values:
            return None
        digest = hashlib.sha256(key.encode("utf-8")).digest()
        return values[int.from_bytes(digest[:4], "big") % len(values)]

    def sync_audio(self, state: RenderState) -> None:
        if not self._audio_initialized:
            return
        music_binding = self.binding(f"music:map:{state.map_id}") or self.binding("music:default")
        desired_asset = music_binding.asset_id if music_binding is not None else None
        if desired_asset != self._current_music_asset:
            if self._current_music is not None:
                self.pr.stop_music_stream(self._current_music)
            choices = self.music.get(desired_asset, ()) if desired_asset is not None else ()
            self._current_music = choices[0] if choices else None
            self._current_music_asset = desired_asset
            if self._current_music is not None:
                self.pr.play_music_stream(self._current_music)
        if self._current_music is not None:
            self.pr.update_music_stream(self._current_music)

        if state.revision == self._last_audio_revision:
            return
        for event in state.events:
            binding = self._event_binding(event.kind, event.subject_id)
            if binding is None:
                continue
            values = self.sounds.get(binding.asset_id, ())
            sound = self._select_variation(
                values,
                f"{state.revision}:{event.kind}:{event.actor_id}:{event.subject_id}",
            )
            if sound is not None:
                self.pr.play_sound(sound)
        self._last_audio_revision = state.revision

# Third-party notices

RPG World Forge itself is MIT-licensed. The audited M5 development, asset, and
release toolchain uses these pinned third-party distributions:

- Pillow 12.3.0 — Pillow project, HPND License.
- build 1.5.0 — PyPA build, MIT License.
- raylib 6.0.1.0 — raylib-python-cffi, Eclipse Public License 2.0; bundled raylib uses the zlib/libpng License.
- Ruff 0.15.22 — Astral Ruff, MIT License.
- setuptools 83.0.0 — PyPA setuptools, MIT License.
- wheel 0.47.0 — PyPA wheel, MIT License.

Generated standalone games record their complete install closure, including
CFFI and pycparser, in their own requirements.lock, platform.lock.json, and
THIRD_PARTY_NOTICES.md. Runtime bundles retain separate per-asset license
inventories; this notice does not grant rights to imported media.

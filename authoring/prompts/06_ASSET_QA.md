# Session: asset QA

Compare a candidate file against its specification, bibles, and in-engine
capture.

Check:

- Perspective, scale, pivot, light, palette, and silhouette consistency.
- Readability on every terrain at minimum resolution.
- Missing frames, jitter, cropping, alpha halos, seams, and incorrect loops.
- Unintended differences across directions or variations.
- Audio loudness, clicks, truncated tails, repetition, and masking.
- Path, name, format, metadata, and technical budget.
- Complete provenance, permissions, and licenses.
- Successful `build-renderpack`, runtime hash verification, raylib load, clip
  bounds, semantic binding, and resource cleanup.

Classify every finding as blocker, required fix, or observation. Final approval
always belongs to a person or the project's authorized lead reviewer.

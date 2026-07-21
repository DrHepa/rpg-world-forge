# M5 neutral production fixture

Narrative-neutral fixture anchored to `content/compiled/foundation.worldpack.json`. Regenerate outside the repository with `scripts/generate_m5_neutral.py --target /tmp/m5-neutral`. It executes locally, procedurally, and offline; the `openai` route is only the required contract namespace, and no provider, ML-model, or network call occurs.
The lock records the exact committed bytes. Regeneration is byte-stable within one supported toolchain; Pillow or zlib version changes may produce different PNG and receipt hashes while preserving the validated semantics.

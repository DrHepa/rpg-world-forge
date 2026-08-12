---
name: materialize-generic-game
description: Materialize and independently verify one exact ready generic game envelope outside the Forge repository.
---

# Materialize a generic standalone game

## Scope

Use only a verified `materialization_ready` bundle and a nonexistent external
target. This skill does not compile authoring data, produce assets, select an
adapter, or repair evidence.

## Invariants

The game may contain immutable gamepack logic, sealed assets, selected runtime
snapshot/implementation, game-specific code, verification scripts, and required
licenses/notices. It must contain no Forge distribution dependency, source
project, `.worldforge` control plane, prompt, provider SDK, model/credential
data, or runtime AI. Preserve the exact logic hash through lock, save, replay,
package, and extraction evidence.

## Completion

Run the independent verifier with Forge absent from `PYTHONPATH`, deterministic
headless/save/replay checks, package/extraction verification, and only the
native checks available for the declared platform. Authoring validity is not runtime executability. Missing hosted native evidence leaves release blocked.

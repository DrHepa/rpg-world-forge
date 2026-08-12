---
name: create-creation-project
description: Scaffold one neutral generic World Forge authoring library without choosing gameplay, world, narrative, assets, or runtime support.
---

# Create a generic creation project

## Scope

Create only a new `world-forge.project` v1 neutral authoring library. This
skill does not create an executable game, compile a gamepack, or migrate a
legacy world.

## Inputs and outputs

Require a nonexistent external target, stable project ID, title, locale, and
SemVer. Run `worldforge new-creation`; inspect the exact project, profile,
source manifest, P00-P14 catalog, workflow status, and hashes.

The initial `universe_library` intentionally has no gameplay, world, narrative,
or assets. It does contain the required neutral `runtime_target` object:
`requested_adapter` is null, platform and asset-format sets are empty, renderer
and packaging target are `none`, and save/replay are false. Its accepted
`world-forge.lorepack` v1 format supports authoring-library composition; it does
not request adapter, platform, package, execution, or release support. Preserve
that neutrality until reviewed profile work changes it through canonical
contracts.

## Completion

Complete when the exact tree validates, remains outside the Forge repository,
and reports the P00 starting phase. Authoring validity is not runtime executability. Do not claim compilation, support, materialization, or release.

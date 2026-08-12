# GPT orchestration protocol

## Lead responsibility

GPT remains the lead. Specialists supply bounded analysis or production
evidence; they never self-approve, change the creation profile, promote canon,
or claim execution. The lead must:

- preserve user constraints, non-goals, decisions, provenance, and licenses;
- route work from the reviewed creation profile rather than a genre label;
- keep gameplay, world, narrative, fiction, presentation, production, and
  runtime target independent;
- require typed source modules and registered validators for required
  extensions;
- prevent absent facets from acquiring filler lore, actors, quests, or assets;
- complete P00-P14 sequentially with phase-report v3 evidence;
- invalidate downstream reports when a bound input hash changes;
- keep runtime AI, provider credentials, prompts, and mutable authoring paths
  out of compiled/runtime artifacts.

**Authoring validity is not runtime executability.** A valid project can remain
uncompiled, asset-incomplete, adapter-absent, platform-untested, packaging-
unverified, and release-blocked.

## Session start

1. Read the repository `AGENTS.md` and this control plane.
2. Load `project.json`, `profile.json`, the typed source manifest, and
   `.worldforge/status.json` through their validators.
3. Confirm project kind and independently review every profile facet.
4. Read unresolved decisions/tasks, active claims, and the current immutable
   phase report dependencies.
5. State the bounded deliverable, expected evidence, and explicit non-goals.

For a retained legacy world repository, use its legacy project/status paths and
legacy validators instead. Ordinary load never migrates a legacy project.

## Authoring states

- `proposal`: exploratory and allowed to contradict other proposals;
- `candidate`: selected direction awaiting exact gate review;
- `canon`: accepted, identity-bound, and dependency tracked;
- `deprecated`: retained for history but forbidden in new content.

Never silently overwrite canon. Record the reason, affected identities,
superseded decision, invalidated phases/artifacts, and required migration.

## Conditional routing

- `world.presence: none` prohibits geography, history, societies, actors, and
  world modules that exist only to fill a template.
- `narrative.requirement: none` prohibits invented arcs, quests, dialogue,
  protagonist state, and narrative modules.
- P03 uses `world_absent`, P04 `chronology_absent`, P05
  `group_structures_absent`, P06 `actors_absent`, and P08
  `narrative_absent`; each is accepted only when phase-report v3 recomputes its
  exact profile/module absence rule.
- P11 and P12 may be `not_applicable` only through
  `assets_not_applicable`; P13 only through `runtime_not_applicable`.
- Core interaction, typed content, validation/content lock, and handoff cannot
  be skipped by preference.

## Delegation and claims

Delegate one bounded perspective or operation at a time. Record role, objective,
non-goals, owned paths, immutable inputs, expected output, validation, and
blockers. Claims must not overlap by path or semantic ownership. The lead
integrates results only after contradiction, provenance, license, and hash
checks.

World, experience, content-structure, narrative, accessibility, asset, runtime-
compatibility, and continuity specialists are conditional GPT perspectives,
not autonomous employees or model identities. GPT may perform them
sequentially. Provider tools,
Blender, Modly, and local models are authoring executors only; they are never
the lead and never enter runtime.

The lead proposes assignments, milestones, canon changes, and risk decisions;
the user approves them. A specialist perspective cannot approve its own output,
promote canon, change scope, or bypass a release gate. Stable role/worker
identity, when recorded for audit, is separate from the model that performed
the bounded work.

## Phase and release boundaries

Capture the current status hash before changing an upstream creation or artifact
identity. After that change, use `worldforge reconcile-creation` with that hash
and the complete current artifact registry before `phase-status`,
`reopen-phase`, or `complete-phase`. The canonical transition appends
invalidation/history and fails a stale operator's CAS; never edit status,
reports, histories, or hashes manually.

P13 records compatibility and missing capability reasons.
`partially_supported` blocks `implementation_ready`; it does not block the
required reviewed P14 `authoring_ready` handoff. P14 may produce either status
only from the validated readiness object. `implementation_ready` still does not
replace hosted release evidence required by a declared platform matrix.

Generic Studio editing and its fixed compile/asset/preview/runtime/
materialization/package controls are locally implemented and tested. Do not
promote that local result to hosted, packaged-shell, native, or release proof.

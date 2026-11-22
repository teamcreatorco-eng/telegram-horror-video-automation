# Motion decode performance recovery

The user requests a 20-second target per approved five-second clip and delegates the
implementation. Preserve FLUX, the approved NVFP4 LTX checkpoint, opening image,
prompts, seeds, 121 frames at 24 fps, resolution, offload and sampling configuration.

## Evidence and hypothesis

Latest rental 61852417 rendered 24 clips in 1,776 seconds, approximately 74 seconds
each. Its motion.log explicitly reports missing NATTEN and the slower Triton NA
DiffVAE fallback. Warm denoising progress bars show approximately 5–7 seconds for
the first pass and 6–7 seconds for refinement; prompt encoding, loading and decoding
are not individually timed. Thus missing accelerated decoding is a confirmed runtime
configuration defect, but the precise fraction of time it explains remains unmeasured.

## Fix and controls

Install the official hash-pinned NATTEN 0.21.5 binary matching the existing Linux
x86-64, CPython 3.12, Torch 2.10.0, CUDA 13.0 runtime. Use --no-deps; do not upgrade
Torch, build from source, replace weights or reduce generation settings. Exercise an
actual CUTLASS neighborhood kernel, not just import natten. Reuse an already working
backend. Keep the existing fallback and record it explicitly if the optional installation
or smoke test fails; no new mandatory reviewer or quality-rejection loop is introduced.
Run optional kernel validation in a separate process and persist its result; the renderer
must disable an importable but failed NATTEN backend before importing LTX. This avoids
both LTX's import-only selection and a failed CUDA smoke poisoning the render context.

Record prompt encoding, image conditioning, denoising, upsampling and lazy decoding
durations. A lazy video iterator must remain lazy and time only its own work, not the
consumer's encoding or queue wait. Include the helper in fresh and resumed worker
uploads and the cost-workload fingerprint so old measurements cannot qualify the new
runtime accidentally.

## Verification and remaining measurement

Offline tests cover binary selection, hash pinning, no Torch replacement, idempotency,
fallback, iterator semantics and unchanged argument forwarding. Full pytest and compileall
are required. A paid benchmark is separately gated by AGENTS.md: prepare a comparison on
the preserved instance using saved inputs and at least three warm clips. Record backend,
per-stage timing, complete clip time, first-clip overhead and visual output. Do not describe
20 seconds as achieved until measured. If this hypothesis fails, use measured stage
timings before selecting a second optimization.

The user explicitly selected code-only changes on 2026-11-22. The existing Vast GPU
must stay stopped. No paid test, on-host installation or 20-second claim is authorized
by that selection.

Offline verification: all 285 tests passed, including ten new runtime/profiling cases;
Python compileall, Bash syntax and diff whitespace checks passed. Independent code review
confirmed the final isolated fallback fix has no remaining concrete blockers. GPU speed,
wheel execution on the production host and resulting clip quality remain unmeasured.

Sources: pinned LTX source revision 4f521619ae0029e8fbb64d555030c2b121a33b4f;
https://github.com/SHI-Labs/NATTEN/releases/tag/v0.21.5;
https://github.com/Lightricks/LTX-2/blob/main/packages/ltx-pipelines/docs/optimization.md.

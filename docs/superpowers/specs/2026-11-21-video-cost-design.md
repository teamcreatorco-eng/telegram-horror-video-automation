# Ten-minute silent video cost and speed design

Status: approved for inline offline implementation on 2026-11-21. Paid benchmarks and deployment are not started by this approval.

## Outcome

Produce a complete ten-minute silent film through Telegram, preserving the approved FLUX image and LTX motion settings. Target total variable production cost is INR 150–170 at INR 96/USD (USD 1.5625–1.770833). Report allocated Railway editing/hosting costs separately and include them when judging the total. These are targets, not guarantees.

Builder call: ten minutes means 600 seconds of story footage, excluding optional branding. Render 120 clips at the approved 121 frames/24 fps, then trim each to 120 frames in editing for exactly five seconds per clip. The cost model counts 120 generation calls and does not pretend the removed frames are free to render. Story events needing the final frame must be accommodated by edit timing, never silently cut away.

## Evidence

The saved approved one-minute run rendered 12 five-second clips at a mean 55.97 seconds with FP8 and CPU offload. The production-kit run rendered 24 at 99.55 seconds with comparable logged settings. A two-clip NVFP4 sample averaged 53.83 seconds. Different hosts/runtimes prevent causal attribution to quantization alone. Encoding takes about 0.4 seconds, queue gaps generally under 0.5 seconds. The pipeline is the measured per-clip bottleneck; its slow internal stage is not yet isolated.

The rental selector ranks offers by estimated price without measured clip throughput. The worker defaults to CPU offload; Railway LTX_OFFLOAD is not forwarded into runtime.sh. Narration is currently Chatterbox and must be explicitly skipped for a silent request. Model download workers already run concurrently.

## Approaches

A: qualify hosts by measured successful clip cost, preserving runtime. Lowest implementation scope, but variable host availability can prevent the target.

B: qualify hosts and profile runtime stages; optimize only the measured bottleneck. Recommended by the assistant; user requested inline execution but has not yet approved this written scope. Greater diagnostic coverage without assuming a smaller model solves the problem.

Changing model, reducing resolution/frames/steps, repeating footage, or slowing footage is outside scope because approved quality must be preserved. Persistent rentals between films are outside scope because the user wants destruction on completion.

## Proposed scope for approach B

1. Add a pure cost model for pending clip count, warm seconds/clip, cold setup, image work, transfers, failures, writing fees and editing allocation. Keep observed and estimated fields distinct. Never sum overlapping model-download times as wall time. Include first-clip overhead separately.
2. Record model/runtime fingerprints, CPU allocation, memory, GPU identity, PCIe link, power/clocks where readable, and stage timings. Missing telemetry is explicit unknown, not zero. No credentials in records.
3. Rank eligible hosts with matching benchmark evidence by projected complete-job cost. Unknown hosts retain an explicit estimate and uncertainty. Do not describe them as verified fast, rent multiple GPUs for comparison, or silently discard a slow rental with paid downloads.
4. Expose validated runtime options through the rental boundary; preserve current defaults. Profile first, then propose stage retention/offload or compilation independently. Do not disable all offloading merely because allocated VRAM appears low.
5. Add an explicit silent request path that bypasses every narration download, GPU synthesis and local TTS branch. Preserve narration configuration for other requests. Keep visual review disabled.
6. Destroy the successful job's rental only after verified artifacts are copied off it. Preserve unfinished work on failure. Reuse saved assets when resuming.

## Budget and acceptance

For 120 five-second clips, USD 0.70/hour and 50 seconds/clip imply USD 1.1667 (INR 112) for motion alone. At 55 seconds this is USD 1.2833 (INR 123.20). A INR 170 target leaves INR 58 or INR 46.80 respectively for all other stages. No separately invented budget allowance is evidence of actual cost.

Offline acceptance: cost calculations include all stages; rankings handle unknown/stale benchmarks; config reaches the worker; silent jobs invoke no narration; resume preserves saved work; host observations contain no secrets. This phase does not prove 50 seconds/clip or the final price.

Paid acceptance, separately authorized: same saved image/prompt/seed, 121 frames, 24 fps, 1280x768 and approved 8+3 steps; first clip plus at least three warmed clips. Compare quality and record exact full hardware/software identities. Only one performance variable changes per comparison. Stop after repeated unsuccessful hypotheses and reassess instead of stacking fixes. A complete cold ten-minute run is ultimately required to verify the total target, including retries and transfer charges.

## Constraints and open decisions

No GPU rental, rendering, dependency installation, deployment, or provider test during design. The existing Telegram run remains cancelled. Earlier no-test instruction remains in force until the user explicitly approves offline checks for this work. Do not claim code is verified without those checks. No automatic budget abort or duration limit is introduced; if actual costs exceed estimates, report honestly using the existing recovery policy.

After approval, write a task-by-task implementation plan, execute inline in an isolated branch, and perform a final independent code review. Deployment and a paid benchmark remain separate from offline implementation.

## Approved visual freeze

No changes to generation worker scripts, model weights or precision, prompts, references, frame count, resolution, steps, camera or motion settings. Runtime/offload/compilation tuning is deferred. Host selection and read-only accounting are the implementation scope. Existing production configuration is not changed; silent production must be selected explicitly. Offline regression checks are part of the requested executing-plans workflow; paid provider/render tests remain excluded.

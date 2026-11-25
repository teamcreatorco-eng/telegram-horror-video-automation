# Rental overhead measurement and cost ranking
Preserve the approved FLUX/LTX/Chatterbox recipe and single-opening NVFP4 rendering.
Target $1.50 per ten-minute rental, as an objective rather than a spending cap.
Measure installation, worker phases and transfers. Keep incremental motion telemetry so failed runs
retain useful timing evidence. Use only qualified same-host measurements for cost ranking, and
include measured setup time when available. Reduce unnecessary transfers of unchanged reference
files within one live SSH session. A restart invalidates that transfer cache.
Approaches considered: timing and measured ranking, or prebuilding a GPU image. Start with
measurement and avoiding redundant transfers. Defer a published image until setup logs identify
what is slow and a registry/build environment is available. No paid test or image publication.

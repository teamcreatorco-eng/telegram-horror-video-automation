import json
import sys
import time
import torch
from diffusers import Flux2KleinPipeline
from image_download import prepare_flux
from PIL import Image
from common import ROOT, RESULTS, event

tasks = json.loads((ROOT / sys.argv[1]).read_text(encoding="utf-8"))
path = prepare_flux(ROOT, event)
event("image_model_loading")
pipe = Flux2KleinPipeline.from_pretrained(path, torch_dtype=torch.bfloat16, local_files_only=True).to("cuda")
event("image_model_loaded")
output_dir = RESULTS / sys.argv[2] if len(sys.argv) > 2 else RESULTS
output_dir.mkdir(exist_ok=True)
for task in tasks:
    start = time.monotonic()
    refs = []
    for filename in task.get("generation_refs", task["refs"]):
        with Image.open(ROOT / filename) as image:
            refs.append(image.convert("RGB"))
    with torch.inference_mode():
        result = pipe(prompt=task["prompt"], image=refs or None, width=1536, height=864,
                      num_inference_steps=4, guidance_scale=1.0,
                      generator=torch.Generator("cuda").manual_seed(task["seed"])).images[0]
    out = output_dir / task["file"]
    temp = out.with_suffix(".partial.png")
    result.save(temp)
    temp.replace(out)
    event("still_done", name=out.name, seconds=time.monotonic() - start)

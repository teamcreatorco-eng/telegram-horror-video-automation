"""Submit one opening image per Wan 2.2 shot to the local ComfyUI process."""
import hashlib
import json
import re
import shutil
import sys
import time
import uuid
from urllib import error, request

from common import ROOT, RESULTS, event

COMFY = ROOT / "ComfyUI"
URL = "http://127.0.0.1:8188"
MODEL = "wan2.2-a14b-fp8"


def node(kind, **inputs):
    return {"class_type": kind, "inputs": inputs}


def graph(shot, name, prefix):
    seed = shot["seed"]
    sampling = dict(noise_seed=seed, steps=20, cfg=4, sampler_name="euler", scheduler="simple")
    return {
        "1": node("LoadImage", image=name),
        "2": node("CLIPLoader", clip_name="umt5_xxl_fp8_e4m3fn_scaled.safetensors", type="wan", device="default"),
        "3": node("CLIPTextEncode", clip=["2", 0], text=shot["video_prompt"]),
        "4": node("CLIPTextEncode", clip=["2", 0], text=(
            "fade, dissolve, crossfade, cut, slideshow, frozen pose, teleportation, geometry morph, "
            "extra people, doubled limbs, face drift, warped props, 3D, photorealism, subtitles")),
        "5": node("VAELoader", vae_name="wan_2.1_vae.safetensors"),
        "6": node("WanImageToVideo", positive=["3", 0], negative=["4", 0], vae=["5", 0],
                  start_image=["1", 0], width=848, height=480, length=shot["frames"], batch_size=1),
        "7": node("UNETLoader", unet_name="wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors", weight_dtype="default"),
        "8": node("UNETLoader", unet_name="wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors", weight_dtype="default"),
        "9": node("ModelSamplingSD3", model=["7", 0], shift=8),
        "10": node("ModelSamplingSD3", model=["8", 0], shift=8),
        "11": node("KSamplerAdvanced", **sampling, model=["9", 0], positive=["6", 0], negative=["6", 1],
                   latent_image=["6", 2], add_noise="enable", start_at_step=0, end_at_step=10,
                   return_with_leftover_noise="enable"),
        "12": node("KSamplerAdvanced", **sampling, model=["10", 0], positive=["6", 0], negative=["6", 1],
                   latent_image=["11", 0], add_noise="disable", start_at_step=10, end_at_step=20,
                   return_with_leftover_noise="disable"),
        "13": node("VAEDecode", samples=["12", 0], vae=["5", 0]),
        "14": node("CreateVideo", images=["13", 0], fps=shot["fps"]),
        "15": node("SaveVideo", video=["14", 0], filename_prefix=prefix, format="mp4", codec={"codec": "h264"}),
    }


def api(path, payload=None, timeout=20):
    data = json.dumps(payload).encode() if payload is not None else None
    req = request.Request(URL + path, data=data, headers={"Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return json.load(response)
    except error.HTTPError as exc:
        raise RuntimeError(f"ComfyUI {path} failed: {exc.read().decode(errors='replace')[:1200]}") from None


def render(plan):
    from motion_recovery import reusable, mark_complete
    for shot in plan["shots"]:
        if reusable(RESULTS,shot):
            event('video_recovered',id=shot['id'])
            continue
        ident = shot["id"]
        source = RESULTS / shot["starting_image_file"]
        if not re.fullmatch(r"\d{3}", ident) or not source.is_file() or source.parent != RESULTS:
            raise ValueError("Invalid Wan motion source.")
        if "ending_image_file" in shot or "ending_frame" in shot:
            raise ValueError("Wan motion accepts a single opening image.")
        prefix = f"{MODEL}-{ident}-{uuid.uuid4().hex}"
        image_name = prefix + ".png"
        staged_input = COMFY / "input" / image_name
        shutil.copyfile(source, staged_input)
        start = time.monotonic()
        event("video_start", id=ident, model=MODEL, source_image=source.name,
              source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), seed=shot["seed"],
              frames=shot["frames"], fps=shot["fps"])
        try:
            job = api("/prompt", {"prompt": graph(shot, image_name, prefix)})["prompt_id"]
            while not (result := api("/history/" + job).get(job)):
                time.sleep(3)
            if not result["status"]["completed"]:
                raise RuntimeError(f"Wan shot {ident} failed: {result['status']}")
            output, = (COMFY / "output").glob(prefix + "*.mp4")
            target = RESULTS / f"shot-{ident}.mp4"
            output.replace(target)
            mark_complete(RESULTS,shot)
            event("video_done", id=ident, seconds=time.monotonic() - start, bytes=target.stat().st_size,
                  sha256=hashlib.sha256(target.read_bytes()).hexdigest())
        finally:
            staged_input.unlink(missing_ok=True)


def main():
    if len(sys.argv) > 1:
        render(json.loads((ROOT / sys.argv[1]).read_text(encoding="utf-8")))
        return
    seen = set()
    while True:
        for item in sorted(ROOT.glob("motion-request-*.json")):
            if item not in seen:
                render(json.loads(item.read_text(encoding="utf-8")))
                seen.add(item)
        time.sleep(1)


if __name__ == "__main__":
    main()

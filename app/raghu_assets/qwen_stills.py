"""Render reference-based Qwen 2511 images through the pinned ComfyUI worker."""
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib import error, request
import uuid

from PIL import Image

from common import ROOT, RESULTS, event

COMFY = ROOT / "QwenComfyUI"
URL = "http://127.0.0.1:8189"


def node(kind, **inputs):
    return {"class_type": kind, "inputs": inputs}


def graph(prompt, references, seed, prefix):
    """Follow Comfy-Org's non-Lightning 40-step Qwen Edit 2511 template."""
    nodes = {
        "1": node("UNETLoader", unet_name="qwen_image_edit_2511_fp8mixed.safetensors", weight_dtype="default"),
        "2": node("CLIPLoader", clip_name="qwen_2.5_vl_7b_fp8_scaled.safetensors", type="qwen_image", device="default"),
        "3": node("VAELoader", vae_name="qwen_image_vae.safetensors"),
        "4": node("ModelSamplingAuraFlow", model=["1", 0], shift=3.1),
        "5": node("CFGNorm", model=["4", 0], strength=1.0),
        "20": node("ImageScale", image=["10", 0], upscale_method="lanczos", width=1536, height=864, crop="center"),
        "21": node("VAEEncode", pixels=["20", 0], vae=["3", 0]),
    }
    image_inputs = {}
    for index, name in enumerate(references):
        key = str(10 + index)
        nodes[key] = node("LoadImage", image=name)
        image_inputs[f"image{index + 1}"] = ["20", 0] if index == 0 else [key, 0]
    nodes.update({
        "30": node("TextEncodeQwenImageEditPlus", clip=["2", 0], vae=["3", 0], prompt=prompt, **image_inputs),
        "31": node("TextEncodeQwenImageEditPlus", clip=["2", 0], vae=["3", 0], prompt="", **image_inputs),
        "32": node("FluxKontextMultiReferenceLatentMethod", conditioning=["30", 0], reference_latents_method="index_timestep_zero"),
        "33": node("FluxKontextMultiReferenceLatentMethod", conditioning=["31", 0], reference_latents_method="index_timestep_zero"),
        "34": node("KSampler", model=["5", 0], seed=seed, steps=40, cfg=4.0,
                   sampler_name="euler", scheduler="simple", positive=["32", 0], negative=["33", 0],
                   latent_image=["21", 0], denoise=1.0),
        "35": node("VAEDecode", samples=["34", 0], vae=["3", 0]),
        "36": node("SaveImage", images=["35", 0], filename_prefix=prefix),
    })
    return nodes


def api(path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = request.Request(URL + path, data=data, headers={"Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=20) as response:
            return json.load(response)
    except error.HTTPError as exc:
        raise RuntimeError(f"Qwen worker rejected {path}: {exc.read().decode(errors='replace')[:500]}") from None


def download_models():
    from huggingface_hub import hf_hub_download

    for item in json.loads((ROOT / "qwen-model-manifest.json").read_text()):
        started = time.monotonic()
        target = COMFY / "models" / Path(item["file"]).relative_to("split_files")
        if not target.is_file() or target.stat().st_size != item["bytes"]:
            source = Path(hf_hub_download(
                item["repo"], item["file"], revision=item["revision"],
                local_dir=ROOT / "qwen-downloads", token=False,
            ))
            with source.open("rb") as stream:
                valid = hashlib.file_digest(stream, "sha256").hexdigest() == item["sha256"]
            if source.stat().st_size != item["bytes"] or not valid:
                raise RuntimeError("Qwen model download failed checksum verification.")
            target.parent.mkdir(parents=True, exist_ok=True)
            source.replace(target)
        event("model_download", model="qwen-image-edit-2511-fp8", file=target.name,
              bytes=target.stat().st_size, seconds=time.monotonic() - started)


def task_references(task, directory):
    # The ComfyUI encoder accepts three images. Reserve them for the scene and its cast.
    names = task.get("generation_refs", task["refs"])
    canonical = [name for name in names if name.startswith("results/")]
    names = canonical or names[:1]
    if len(names) > 3:
        raise RuntimeError("Qwen supports a background and at most two character references for this workflow.")
    staged = []
    for index, name in enumerate(names):
        path = (ROOT / name).resolve()
        if not path.is_relative_to(ROOT.resolve()) or not path.is_file():
            raise RuntimeError("A Qwen reference image is missing or outside the worker directory.")
        target = directory / f"reference-{index + 1}.png"
        with Image.open(path) as image:
            image.convert("RGB").save(target)
        staged.append(str(target.relative_to(COMFY / "input")))
    if not staged:
        # Character sheets have text descriptions and use a neutral starter canvas.
        target = directory / "neutral.png"
        Image.new("RGB", (1536, 864), (150, 150, 150)).save(target)
        staged.append(str(target.relative_to(COMFY / "input")))
    return staged


def render(tasks, output):
    for task in tasks:
        filename = task["file"]
        if Path(filename).name != filename or not filename.endswith(".png"):
            raise RuntimeError("Invalid Qwen output filename.")
        started = time.monotonic()
        prefix = "qwen-" + uuid.uuid4().hex
        directory = COMFY / "input" / prefix
        directory.mkdir(parents=True)
        try:
            references = task_references(task, directory)
            prompt = task["prompt"]
            if any(name.startswith("results/") for name in task.get("generation_refs", task["refs"])):
                prompt = prompt.split(" The last reference supplies", 1)[0]
                prompt += " Preserve the supplied character identities and the established architecture. Clean flat 2D ink and cel shading."
            event("still_start", model="qwen-image-edit-2511-fp8", name=filename, steps=40,
                  seed=task["seed"], reference_count=len(references), prompt=prompt)
            job = api("/prompt", {"prompt": graph(prompt, references, task["seed"], prefix)})["prompt_id"]
            until = time.monotonic() + 1800
            while not (result := api("/history/" + job).get(job)):
                if time.monotonic() > until:
                    raise RuntimeError("Qwen image generation stopped responding.")
                time.sleep(2)
            if not result["status"]["completed"]:
                raise RuntimeError("Qwen image generation failed. Inspect the saved Qwen log.")
            saved = result["outputs"]["36"]["images"][0]
            source = (COMFY / "output" / saved["subfolder"] / saved["filename"]).resolve()
            if not source.is_relative_to((COMFY / "output").resolve()):
                raise RuntimeError("Qwen returned a file outside its output directory.")
            target = output / filename
            temporary = target.with_suffix(".partial.png")
            shutil.copyfile(source, temporary)
            with Image.open(temporary) as image:
                image.verify()
            temporary.replace(target)
            source.unlink()
            event("still_done", model="qwen-image-edit-2511-fp8", name=filename,
                  seconds=time.monotonic() - started, sha256=hashlib.sha256(target.read_bytes()).hexdigest())
        finally:
            shutil.rmtree(directory)


def main():
    tasks = json.loads((ROOT / sys.argv[1]).read_text(encoding="utf-8"))
    output = RESULTS / sys.argv[2] if len(sys.argv) > 2 else RESULTS
    output.mkdir(exist_ok=True)
    with (ROOT / "gpu-model.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        download_models()
        with (RESULTS / "qwen.log").open("a") as log:
            server = subprocess.Popen([
                sys.executable, str(COMFY / "main.py"), "--listen", "127.0.0.1",
                "--port", "8189", "--lowvram", "--disable-auto-launch",
            ], stdout=log, stderr=subprocess.STDOUT)
            try:
                for attempt in range(120):
                    if server.poll() is not None:
                        raise RuntimeError("Qwen server exited during startup. Inspect qwen.log.")
                    try:
                        api("/system_stats")
                        break
                    except (OSError, error.URLError):
                        time.sleep(1)
                else:
                    raise RuntimeError("Qwen server did not become ready.")
                render(tasks, output)
            finally:
                server.terminate()
                try:
                    server.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()


if __name__ == "__main__":
    main()

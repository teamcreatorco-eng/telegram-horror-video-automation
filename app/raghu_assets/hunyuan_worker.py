"""HunyuanVideo-1.5 720p I2V using the official Comfy-Org template settings.

One starting image, 121 frames at 24 fps; no ending-image conditioning.
"""
import wan_worker as worker
from qwen_stills import COMFY, download_models

node = worker.node


def graph(shot, name, prefix):
    return {
        "1": node("LoadImage", image=name),
        "2": node("DualCLIPLoader", clip_name1="qwen_2.5_vl_7b_fp8_scaled.safetensors",
                  clip_name2="byt5_small_glyphxl_fp16.safetensors", type="hunyuan_video_15", device="default"),
        "3": node("CLIPTextEncode", clip=["2", 0], text=shot["video_prompt"]),
        "4": node("CLIPTextEncode", clip=["2", 0], text=""),
        "5": node("VAELoader", vae_name="hunyuanvideo15_vae_fp16.safetensors"),
        "6": node("CLIPVisionLoader", clip_name="sigclip_vision_patch14_384.safetensors"),
        "7": node("CLIPVisionEncode", clip_vision=["6", 0], image=["1", 0], crop="center"),
        "8": node("HunyuanVideo15ImageToVideo", positive=["3", 0], negative=["4", 0],
                  vae=["5", 0], start_image=["1", 0], clip_vision_output=["7", 0],
                  width=1280, height=720, length=shot["frames"], batch_size=1),
        "9": node("UNETLoader", unet_name="hunyuanvideo1.5_720p_i2v_fp16.safetensors", weight_dtype="default"),
        "10": node("ModelSamplingSD3", model=["9", 0], shift=7.0),
        "11": node("KSampler", model=["10", 0], seed=shot["seed"], steps=20, cfg=6.0,
                   sampler_name="euler", scheduler="simple", positive=["8", 0], negative=["8", 1],
                   latent_image=["8", 2], denoise=1.0),
        "12": node("VAEDecodeTiled", samples=["11", 0], vae=["5", 0], tile_size=512,
                   overlap=64, temporal_size=32, temporal_overlap=4),
        "13": node("CreateVideo", images=["12", 0], fps=shot["fps"]),
        "14": node("SaveVideo", video=["13", 0], filename_prefix=prefix, format="mp4", codec={"codec": "h264"}),
    }


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["--setup"]:
        download_models("hunyuan-model-manifest.json")
    else:
        worker.COMFY = COMFY
        worker.MODEL = "hunyuanvideo-1.5-720p-i2v"
        worker.graph = graph
        worker.main()

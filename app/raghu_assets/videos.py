import hashlib,json,time,traceback,fcntl,os
import torch
from common import ROOT,RESULTS,event
from motion_recovery import reusable,mark_complete
def main():
 gpu_lock=(ROOT/'gpu-model.lock').open('w')
 fcntl.flock(gpu_lock,fcntl.LOCK_EX)
 quantization=os.getenv("LTX_QUANTIZATION","fp8")
 if quantization=="nvfp4":
  from ltx_core.quantization.nvfp4 import build_nvfp4_prequant_policy as build_policy
 else:
  from ltx_core.quantization.fp8_cast import build_policy
 from ltx_core.model.video_vae import get_video_chunks_number
 from ltx_pipelines.distilled import DistilledPipeline
 from ltx_pipelines.utils.model_paths import ModelPaths
 from ltx_pipelines.utils.types import OffloadMode,ImageConditioningInput
 from ltx_pipelines.utils.media_io import encode_video
 assert torch.cuda.is_available()
 event('video_hardware',gpu=torch.cuda.get_device_name(),torch=torch.__version__)
 base=ROOT/'models/LTX-2.5'
 weight="nvfp4" if quantization=="nvfp4" else "bf16"
 transformer=str(base/f'diffusion_models/ltx-2.5-22b-distilled-transformer-{weight}.safetensors')
 paths=ModelPaths.from_split(transformer_path=transformer,text_encoder_path=str(base/'text_encoders/gemma4-12b-with-proj-ltx-2.5-bf16.safetensors'),video_vae_path=str(base/'vae/ltx-2.5-video-vae-bf16.safetensors'),audio_vae_path=str(base/'vae/ltx-2.5-audio-vae-bf16.safetensors'))
 pipe=DistilledPipeline(model_paths=paths,spatial_upsampler_path=str(base/'latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors'),loras=[],quantization=build_policy(transformer),offload_mode=OffloadMode(os.getenv("LTX_OFFLOAD", "cpu")))
 event('video_model_ready',pipeline='DistilledPipeline',resolution=[1280,768],steps=[8,3],reference_video=False,control_lora=False,quantization=quantization,prompt_enhancement=False)
 def render(plan):
  for i,s in enumerate(plan["shots"]):
   if reusable(RESULTS,s):
    event('video_recovered',id=s['id'])
    continue
   source=RESULTS/s['starting_image_file'];t=time.time();torch.cuda.reset_peak_memory_stats()
   if not source.is_file():
    raise ValueError("Motion requires an approved opening image.")
   images=[ImageConditioningInput.at(str(source),0,1.0)]
   ending_log={}
   if 'ending_image_file' in s:
    ending=RESULTS/s['ending_image_file']
    last=s['ending_frame']
    if last != s['frames'] - 1 or not ending.is_file():
     raise ValueError("Paired motion requires its approved ending image at the last frame.")
    images.append(ImageConditioningInput.at(str(ending),last,1.0))
    ending_log=dict(end_image=ending.name,end_sha256=hashlib.sha256(ending.read_bytes()).hexdigest(),ending_frame=last)
   seed=s.get('seed',55400+i)
   event('video_start',id=s['id'],prompt=s['video_prompt'],source_image=source.name,source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),seed=seed,num_frames=s['frames'],fps=s['fps'],**ending_log)
   with torch.inference_mode():
    output=pipe(prompt=s['video_prompt'],seed=seed,height=768,width=1280,num_frames=s['frames'],frame_rate=s['fps'],images=images,enhance_prompt=False)
    torch.cuda.synchronize()
    event('video_pipeline_returned',id=s['id'],seconds=time.time()-t)
    target=RESULTS/f"shot-{s['id']}.mp4";temp=RESULTS/f"shot-{s['id']}.partial.mp4"
    encode_video(video=output.video,fps=s['fps'],audio=None,output_path=str(temp),video_chunks_number=get_video_chunks_number(output.num_frames,output.tiling_config))
    temp.replace(target)
    mark_complete(RESULTS,s)
   event('video_done',id=s['id'],seconds=time.time()-t,peak_vram=torch.cuda.max_memory_allocated(),bytes=target.stat().st_size,sha256=hashlib.sha256(target.read_bytes()).hexdigest())
 if os.getenv("RAGHU_WATCH") != "1":
  render(json.loads((ROOT/'render-plan.json').read_text()))
  return
 seen = set()
 while True:
  for request in sorted(ROOT.glob('motion-request-*.json')):
   if request.name in seen:
    continue
   seen.add(request.name)
   plan = json.loads(request.read_text())
   (RESULTS / request.name.replace('.json','.accepted')).write_text('ok')
   render(plan)
   (RESULTS / request.name.replace('.json','.done')).write_text('ok')
  time.sleep(.5)
if __name__=='__main__':
 try:main()
 except Exception as e:event('worker_error',phase='videos',type=type(e).__name__,message=str(e));traceback.print_exc();raise

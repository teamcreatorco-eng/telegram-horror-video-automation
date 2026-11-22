#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ -f runtime.sh ]; then
  source runtime.sh
fi
export HF_HOME="$PWD/hf-cache" HF_HUB_DISABLE_XET=1 HF_HUB_DISABLE_TELEMETRY=1
export HF_HUB_DISABLE_PROGRESS_BARS=1 TOKENIZERS_PARALLELISM=false PIP_NO_CACHE_DIR=1
export PYTHONUNBUFFERED=1 PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True OMP_NUM_THREADS=8
ensure_comfy() {
      if [ ! -f qwen-env/.ready ]; then
        if ! command -v git >/dev/null; then
          apt-get update -qq
          apt-get install -y -qq git
        fi
        git init QwenComfyUI
        git -C QwenComfyUI fetch --depth 1 https://github.com/Comfy-Org/ComfyUI.git 0263e48806e5be43fa926d3961caac29cd268d84
        git -C QwenComfyUI checkout --detach FETCH_HEAD
        mkdir -p QwenComfyUI/input
        python -m venv --without-pip --system-site-packages qwen-env
        python -m pip --python qwen-env/bin/python install -r QwenComfyUI/requirements.txt huggingface-hub pillow
        touch qwen-env/.ready
      fi
}
case "$1" in
  stills)
    if [ "${RAGHU_IMAGE_MODEL:-flux}" = qwen2511 ]; then
      ensure_comfy
      shift
      qwen-env/bin/python qwen_stills.py "$@"
      exit 0
    fi
    if [ ! -e image-env/.ready ]; then
      python -m venv --without-pip --system-site-packages image-env
      python -m pip --python image-env/bin/python install diffusers==0.42.1 transformers==5.22.1 accelerate==1.15.0 pillow==13.0.0 protobuf==7.36.2 sentencepiece==0.2.3 huggingface-hub==2.4.0
      touch image-env/.ready
    fi
    shift
    image-env/bin/python stills.py "$@"
    ;;
  voice)
    if [ ! -f voice-env/.ready ]; then
      if ! command -v git >/dev/null; then
        apt-get update -qq
        apt-get install -y -qq git
      fi
      python -m venv --without-pip --system-site-packages voice-env
      python -m pip --python voice-env/bin/python install --no-deps \
        git+https://github.com/resemble-ai/chatterbox.git@ab55923c27757c90cfcc407baa57dc25a7d25217 \
        git+https://github.com/resemble-ai/Perth.git@ff1c8ac55a976971245cdd53c18d6131ca00d993
      python -m pip --python voice-env/bin/python install 'numpy<2' 'scipy<1.17' \
        transformers==5.2.0 diffusers==0.29.0 librosa s3tokenizer conformer spacy-pkuseg \
        pykakasi soundfile pyloudnorm omegaconf
      touch voice-env/.ready
    fi
    if [ ! -f voice-env/.alignment-ready ]; then
      python -m pip --python voice-env/bin/python install faster-whisper==1.2.1
      touch voice-env/.alignment-ready
    fi
    voice-env/bin/python voice_clone.py "$2"
    ;;
  prepare-motion|motion)
    if ! command -v ffprobe >/dev/null; then
      apt-get update -qq
      apt-get install -y -qq ffmpeg
    fi
    if [ "${RAGHU_MOTION_MODEL:-ltx}" = hunyuan15 ]; then
      ensure_comfy
      if [ ! -f qwen-env/.hunyuan-ready ]; then
        qwen-env/bin/python hunyuan_worker.py --setup
        touch qwen-env/.hunyuan-ready
      fi
      if [ "$1" = motion ]; then
        qwen-env/bin/python QwenComfyUI/main.py --listen 127.0.0.1 --port 8188 --lowvram --disable-auto-launch >results/comfy.log 2>&1 &
        server_pid=$!
        trap 'kill "$server_pid" 2>/dev/null || true' EXIT
        for wait_step in $(seq 1 120); do
          if curl -fsS --max-time 2 http://127.0.0.1:8188/system_stats >/dev/null 2>&1; then break; fi
          kill -0 "$server_pid"
          sleep 1
        done
        curl -fsS --max-time 5 http://127.0.0.1:8188/system_stats >/dev/null
        if [ "${RAGHU_WATCH:-0}" = 1 ]; then
          qwen-env/bin/python hunyuan_worker.py
        else
          qwen-env/bin/python hunyuan_worker.py render-plan.json
        fi
      fi
      exit 0
    fi
    if [ "${RAGHU_MOTION_MODEL:-ltx}" = wan22 ]; then
      if [ ! -f wan-env/.ready ]; then
        if ! command -v git >/dev/null; then
          apt-get update -qq
          apt-get install -y -qq git
        fi
        git init ComfyUI
        git -C ComfyUI fetch --depth 1 https://github.com/Comfy-Org/ComfyUI.git e9afbccc2669677076feb08e52658f129cd8c12b
        git -C ComfyUI checkout --detach FETCH_HEAD
        mkdir -p ComfyUI/input
        python -m venv --without-pip --system-site-packages wan-env
        python -m pip --python wan-env/bin/python install -r ComfyUI/requirements.txt huggingface-hub
        wan-env/bin/python wan_setup.py
        touch wan-env/.ready
      fi
      if [ "$1" = motion ]; then
        wan-env/bin/python ComfyUI/main.py --listen 127.0.0.1 --port 8188 --lowvram >results/comfy.log 2>&1 &
        server_pid=$!
        trap 'kill "$server_pid" 2>/dev/null || true' EXIT
        for wait_step in $(seq 1 120); do
          if curl -fsS --max-time 2 http://127.0.0.1:8188/system_stats >/dev/null 2>&1; then
            break
          fi
          kill -0 "$server_pid"
          sleep 1
        done
        curl -fsS --max-time 5 http://127.0.0.1:8188/system_stats >/dev/null
        if [ "${RAGHU_WATCH:-0}" = 1 ]; then
          wan-env/bin/python wan_worker.py
        else
          wan-env/bin/python wan_worker.py render-plan.json
        fi
      fi
      exit 0
    fi
    if [ ! -e video-env/.ready ]; then
      if ! command -v git; then
        apt-get update -qq
        apt-get install -y -qq git
      fi
      git init engine
      git -C engine fetch --depth 1 https://github.com/Lightricks/LTX-2.git 4f521619ae0029e8fbb64d555030c2b121a33b4f
      git -C engine checkout --detach FETCH_HEAD
      python -m venv --without-pip --system-site-packages video-env
      python -m pip --python video-env/bin/python install transformers==5.14.1 accelerate safetensors huggingface-hub av openimageio colour-science 'cloudpickle>=3.1' 'scipy>=1.14' einops pillow tqdm
      python -m pip --python video-env/bin/python install --no-deps ./engine/packages/ltx-core ./engine/packages/ltx-pipelines
      video-env/bin/python download_video_only.py
      touch video-env/.ready
    fi
    if [ "${LTX_QUANTIZATION:-fp8}" = nvfp4 ]; then
      if [ ! -f video-env/.nvfp4-ready ]; then
        export PATH=/usr/local/cuda/bin:$PATH TORCH_CUDA_ARCH_LIST=12.0 MAX_JOBS=4
        python -m pip --python video-env/bin/python install setuptools wheel ninja
        if ! command -v nvcc >/dev/null; then
          unset CUDA_HOME CUDA_PATH
          python -m pip --python video-env/bin/python install 'cuda-toolkit[nvcc,cccl]==13.0.*'
        fi
        python -m pip --python video-env/bin/python install --no-deps --no-build-isolation ./engine/packages/ltx-kernels >results/kernels.log 2>&1
        video-env/bin/python -c 'from ltx_core.quantization.nvfp4 import require_nvfp4; require_nvfp4()'
        touch video-env/.nvfp4-ready
      fi
      video-env/bin/python -c 'from ltx_core.quantization.nvfp4 import require_nvfp4; require_nvfp4()'
    fi
    if [ "$1" = motion ]; then
      rm -f results/video-decode-backend.json
      video-env/bin/python motion_runtime.py || true
      video-env/bin/python videos.py
    fi
    ;;
  *) exit 2 ;;
esac

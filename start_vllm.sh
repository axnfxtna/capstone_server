#!/bin/bash
# Start vLLM serving Typhoon2-70B on GPUs 1,2,3 (tensor-parallel-size 3)
# GPU 3 is shared with Typhoon2-Audio sidecar (FP16, ~16GB) on port 8001
# --gpu-memory-utilization 0.80 reserves ~16GB free on each GPU (vs 8GB at default 0.90)
CUDA_VISIBLE_DEVICES=0,1,2,3 ~/vllm_env/bin/python \
    -m vllm.entrypoints.openai.api_server \
    --model scb10x/llama3.1-typhoon2-70b-instruct \
    --dtype bfloat16 \
    --tensor-parallel-size 4 \
    --max-model-len 4096 \
    --port 8080 \
    --gpu-memory-utilization 0.65 \
    --enforce-eager \
    --disable-custom-all-reduce

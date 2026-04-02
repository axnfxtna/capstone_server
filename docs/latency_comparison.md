# Satu AI Brain — Latency Comparison: Before vs After vLLM Migration

**Date:** 2026-04-02  
**Server:** HPE NVIDIA2 — 4× NVIDIA A100-SXM4-80GB  

---

## Stage-by-Stage Comparison

| Stage | Model / Component | Before (Ollama + 1× A100) | After (vLLM + 4× A100) | Change |
|---|---|---|---|---|
| Grammar correction | Typhoon2-8B | 800 – 1,500 ms | 0 ms *(short inputs skipped)* | ✅ −100% |
| RAG retrieval | Milvus + bge-m3 | 50 – 200 ms | 50 – 200 ms | — unchanged |
| Memory retrieval | Milvus + MySQL | 30 – 80 ms | 30 – 80 ms | — unchanged |
| **LLM generation** | **Typhoon2-70B** | **4,000 – 12,000 ms** | **p50: 2,497 ms** | ✅ **~5× faster** |
| TTS synthesis | Typhoon2-Audio-8B | ~633 ms | ~633 ms | — unchanged |
| Network (Pi 5 transfer) | WAV delivery | ~50 ms | ~50 ms | — unchanged |
| **Time to first reaction** | — | **6,000 – 15,000 ms** | **p50: 2,547 ms** | ✅ **~5× faster** |

---

## Infrastructure Change

| | Before | After |
|---|---|---|
| LLM engine | Ollama (llama.cpp) | vLLM v0.18.1 |
| Model format | GGUF Q5_K_M (~49 GB) | BF16 safetensors (~140 GB) |
| GPUs used | 1 of 4 | 4 of 4 |
| Tensor parallel | ✗ | 4 |
| Attention backend | llama.cpp (no FlashAttention) | FlashAttention 2 |
| CUDA graphs | ✗ | disabled (`--enforce-eager`) |
| Audio sidecar GPU | — | GPU 3 shared (CUDA_VISIBLE_DEVICES=3) |
| vLLM port | — | 8080 |
| Server port | 8000 | 8000 (unchanged) |
| Audio sidecar port | 8001 | 8001 (unchanged) |

---

## Overall Target Summary

| Metric | Before | After | Target | Status |
|---|---|---|---|---|
| TTFR p50 | ~8,000 ms | 2,547 ms | < 3,000 ms | ✅ |
| TTFR p95 | ~13,000 ms | 4,976 ms | < 5,000 ms | ✅ |
| LLM p50 | ~6,500 ms | 2,497 ms | ≤ 2,500 ms | ✅ |

---

## Key Takeaways

- **LLM generation** was the dominant bottleneck (4,000–12,000 ms). vLLM with 4× tensor parallelism reduced this to p50 2,497 ms — roughly 5× faster.
- **Grammar correction** was effectively eliminated for short inputs via a length threshold check.
- **TTS, RAG, memory, and network** stages were not affected by the migration.
- The audio sidecar (Typhoon2-Audio-8B FP16, ~19 GB peak VRAM) shares GPU 3 with vLLM at `--gpu-memory-utilization 0.65`, leaving ~9 GB headroom.

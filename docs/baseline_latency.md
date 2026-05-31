# Baseline Latency — Week 1

## Hardware
- GPU: NVIDIA RTX 4060 Laptop (8GB VRAM)
- OS: WSL2 Ubuntu 22.04
- CUDA: 12.4

## Model: Qwen3-1.7B (via llama.cpp GGUF)

### Test Prompt
```
What is a refrigerant leak and how do you detect it? /no_think
```
- n_predict: 256
- ngl: 99 (full GPU offload)
- thinking mode: OFF (`/no_think`)

## Results

| Quantization | File Size | Prompt (t/s) | Generation (t/s) |
|---|---|---|---|
| Q4_K_M | ~1.1 GB | 297.5 | 150.8 |
| Q8_0 | ~1.7 GB | 386.0 | 95.5 |

## Analysis

- Q8_0 prompt processing เร็วกว่า Q4_K_M (~30%)
- Q8_0 generation ช้ากว่า Q4_K_M (~37%)
- สำหรับ edge deployment ที่ช่างรอคำตอบ **Generation speed สำคัญกว่า**
- **Q4_K_M เหมาะกว่าสำหรับ production** — generation เร็วกว่า UX ดีกว่า
- Q8_0 ใช้เป็น quality ceiling สำหรับ ablation study

## Conclusion

Production target: **Q4_K_M**  
Quality ceiling: **Q8_0**  
Fine-tuning pipeline: Base FP16 → QLoRA → Merge → Quantize Q4_K_M

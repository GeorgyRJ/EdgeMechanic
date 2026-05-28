# EdgeMechanic

Offline agentic LLM system for HVAC field technicians — runs 100% on-device with RAG, structured logging, and LoRA fine-tuning.

## Stack
- **LLM:** Qwen3-1.7B (GGUF Q4_K_M) via llama.cpp
- **RAG:** ChromaDB + BGE-M3 embedding
- **Agent:** LangChain
- **Structured Output:** Pydantic + llama.cpp grammar mode
- **Fine-tuning:** Unsloth + QLoRA
- **UI:** Streamlit
- **Storage:** SQLite + JSON

## Hardware Target
- Edge laptop (offline) — NVIDIA RTX 4060 8GB VRAM
- WSL2 Ubuntu 22.04

## Project Structure
```
EdgeMechanic/
├── data/              # HVAC manuals and processed chunks
├── src/
│   ├── agent/         # LangChain agent routing
│   ├── rag/           # ChromaDB retrieval pipeline
│   ├── logging/       # Pydantic schemas + SQLite logging
│   └── ui/            # Streamlit interface
├── eval/              # 30-50 question HVAC test set
├── grammars/          # llama.cpp grammar files (JSON enforcement)
├── notebooks/         # Experiments and ablation study
└── docs/              # Architecture and design notes
```

## Roadmap
- [x] Week 1 — Environment setup, llama.cpp + CUDA build, baseline inference
- [ ] Week 2 — Pydantic schema + grammar mode
- [ ] Week 3 — RAG pipeline
- [ ] Week 4 — LangChain agent
- [ ] Week 5 — Streamlit UI + session management
- [ ] Week 6 — Synthetic dataset + LoRA fine-tuning
- [ ] Week 7 — Ablation study
- [ ] Week 8 — Evaluation
- [ ] Week 9 — Polish + documentation

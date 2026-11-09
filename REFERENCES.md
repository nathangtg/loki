# References

> Consolidated academic references for the LOKI project.
> Sourced from the literature review, research phases, and the IEEE architecture paper.

---

## Table of Contents

- [A. Foundational LLM Agent & Planning](#a-foundational-llm-agent--planning)
- [B. Web & GUI Automation Agents](#b-web--gui-automation-agents)
- [C. RAG & Memory-Augmented LLM Systems](#c-rag--memory-augmented-llm-systems)
- [D. Accessibility Tree & UI Understanding](#d-accessibility-tree--ui-understanding)
- [E. On-Device & Edge LLM Inference](#e-on-device--edge-llm-inference)
- [F. Multi-Agent Orchestration Frameworks](#f-multi-agent-orchestration-frameworks)
- [G. Benchmark Datasets & Evaluation](#g-benchmark-datasets--evaluation)
- [H. RPA & Workflow Automation](#h-rpa--workflow-automation)
- [I. Performance Optimisation & Fine-Tuning](#i-performance-optimisation--fine-tuning)
- [J. Systems Referenced in Architecture Paper](#j-systems-referenced-in-architecture-paper)

---

## A. Foundational LLM Agent & Planning

| Tag | Paper | Authors | Venue | Year |
|:----|:------|:--------|:------|:-----|
| L1 | **ReAct: Synergizing Reasoning and Acting in Language Models** | Yao, S., Zhao, J., Yu, D., Shafran, N., Narasimhan, K., Cao, Y. | ICLR | 2023 |
| L2 | **Chain-of-Thought Prompting Elicits Reasoning in Large Language Models** | Wei, J., Wang, X., Schuurmans, D., Bosma, M., Ichter, B., Xia, F., Chi, E., Le, Q., Zhou, D. | NeurIPS | 2022 |
| L3 | **Toolformer: Language Models Can Teach Themselves to Use Tools** | Schick, T., Dwivedi-Yu, J., Dessì, R., Raileanu, R., Cettolo, M., Zettlemoyer, L., Scialom, T. | NeurIPS | 2023 |
| L4 | **Tree of Thoughts: Deliberate Problem Solving with Large Language Models** | Yao, S., Yu, D., Zhao, J., Shafran, I., Griffiths, T., Cao, Y., Narasimhan, K. | NeurIPS | 2023 |
| L5 | **HuggingGPT: Solving AI Tasks with ChatGPT and Its Friends in Hugging Face** | Shen, Y., Song, S., Tan, X., Li, D., Lu, W., Zhuang, Y. | NeurIPS | 2023 |
| L6 | **Auto-GPT: An Autonomous GPT-4 Experiment** | Significant Gravitas | GitHub | 2023 |

**Relevance to LOKI:** LOKI's six-node pipeline (Plan → Observe → Reason → Act → Reflect → Finish) descends directly from ReAct's observe-reason-act loop, extended with plan-mutation (Reflect) and CoT-style step decomposition (Plan). The controller-specialist pattern from HuggingGPT is adopted with browser/native tools as specialists.

---

## B. Web & GUI Automation Agents

| Tag | Paper | Authors | Venue | Year |
|:----|:------|:--------|:------|:-----|
| L7 | **A Real-World WebAgent with Planning, Long Context Understanding, and Program Synthesis** | Gur, I., Furuta, H., Herber, A., Safdari, M., et al. | ICLR | 2024 |
| L8 | **Mind2Web: Towards a Generalist Agent for the Web** | Deng, X., Gu, Y., Zheng, B., Chen, S., Stevens, S., Wang, B., Sun, H., Su, Y. | NeurIPS | 2023 |
| L9 | **From Pixels to UI Actions: Learning to Follow Instructions via Graphical User Interfaces** | Shaw, P., Joshi, M., Cohan, J., Fisch, A., et al. | NeurIPS | 2023 |
| L10 | **CogAgent: A Visual Language Model for GUI Agents** | Hong, H., Wang, W., Lv, Q., Xu, J., et al. | CVPR | 2024 |
| L11 | **AppAgent: Multimodal Agents as Smartphone Users** | Zhang, C., Yang, K., Liu, Z., Han, Y., Chen, M., Deng, Y., Liu, A. X. | arXiv:2312.13771 | 2023 |
| L12 | **UFO: A UI-Focused Agent for Windows OS Interaction** | Zhang, C., Liang, J., Wu, Y., Chen, H., et al. | arXiv:2402.07939 | 2024 |
| L13 | **Cradle: Empowering Foundation Agents Towards General Computer Control** | Wang, W., Chen, B., Chen, Y., Wang, Z., et al. | arXiv:2403.03186 | 2024 |
| L14 | **Computer Use (Beta)** | Anthropic | Documentation | 2024 |

**Relevance to LOKI:** LOKI's dual-surface (browser + native) design parallels UFO's Windows approach, ported to macOS via PyObjC. Unlike CogAgent (18B VLM) and Anthropic Computer Use (cloud + pixels), LOKI achieves comparable control with a 4-bit on-device model and structured AX-tree observation.

---

## C. RAG & Memory-Augmented LLM Systems

| Tag | Paper | Authors | Venue | Year |
|:----|:------|:--------|:------|:-----|
| L15 | **Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks** | Lewis, P., Perez, E., Piktus, A., Petroni, F., et al. | NeurIPS | 2020 |
| L16 | **Self-RAG: Learning to Retrieve, Generate, and Critique Through Self-Reflection** | Asai, A., Wu, Z., Wang, Y., Sil, A., Hajishirzi, H. | ICLR | 2024 |
| L17 | **Voyager: An Open-Ended Embodied Agent with Large Language Models** | Wang, Z., Cai, S., Liu, A., Ma, X., Liang, Y. | arXiv:2305.16291 | 2023 |
| L18 | **RAPTOR: Recursive Abstractive Processing for Tree-Organized Retrieval** | Sarthi, P., Abdullah, S., Tuli, A., Khanna, S., Goldie, A., Manning, C. D. | ICLR | 2024 |
| L19 | **Cognitive Architectures for Language Agents** | Sumers, T. R., Yao, S., Narasimhan, K., Griffiths, T. L. | TMLR | 2024 |

**Relevance to LOKI:** LOKI's CAG (Context-Augmented Generation) layer derives from RAG, narrowed to operational facts (credentials, URLs, navigation paths). The session memory system maps to all four memory types in Sumers et al.'s cognitive architecture taxonomy.

---

## D. Accessibility Tree & UI Understanding

| Tag | Paper | Authors | Venue | Year |
|:----|:------|:--------|:------|:-----|
| L20 | **UIBert: Learning Generic Multimodal Representations for UI Understanding** | Bai, C., Zhou, S., Zhou, C., Zhang, Q., Xiong, C. | IJCAI | 2021 |
| L21 | **Screen2Words: Automatic Mobile UI Summarization with Multimodal Learning** | Wang, B., Li, G., Li, X., Lv, T., Tamber, A. K., Dou, D. | UIST | 2021 |
| L22 | **UGIF: UI Grounded Instruction Following** | Venkatesh, S., Guo, X., Mao, H., Sinha, G., et al. | arXiv:2211.07615 | 2022 |
| L23 | **ActionBert: Leveraging User Actions for Semantic Understanding of User Interfaces** | He, Z., Gao, J., Mou, L., Liu, K., Riedl, L., Liu, A. X. | AAAI | 2021 |
| L24 | **Spotlight: Mobile UI Understanding via a Multi-Level Vision-Language Pretraining** | Jiang, G., Dong, L., Lin, D., Gu, H., et al. | arXiv:2209.14927 | 2022 |

**Relevance to LOKI:** LOKI's AX-tree extraction addresses the same element-understanding challenge as UIBert and ActionBert, but structurally via CDP/macOS APIs rather than through learned multimodal representations.

---

## E. On-Device & Edge LLM Inference

| Tag | Paper | Authors | Venue | Year |
|:----|:------|:--------|:------|:-----|
| L25 | **LLaMA 2: Open Foundation and Fine-Tuned Chat Models** | Touvron, H., Martin, L., Stone, K., Albert, P., et al. | arXiv:2307.09288 | 2023 |
| L26 | **Phi-3 Technical Report: A Highly Capable Language Model Locally on Your Phone** | Abdin, M., Jacobs, S. A., Awan, A. A., et al. | arXiv:2404.14219 | 2024 |
| L27 | **GPTQ: Accurate Post-Training Quantization for Generative Pre-Trained Transformers** | Frantar, E., Ashkboos, S., Hoefler, T., Alistarh, D. | ICLR | 2023 |
| L28 | **LLM.int8(): 8-Bit Matrix Multiplication for Transformers at Scale** | Dettmers, T., Lewis, M., Belkada, Y., Zettlemoyer, L. | NeurIPS | 2022 |
| L29 | **AWQ: Activation-Aware Weight Quantization for LLM Compression and Acceleration** | Lin, J., Tang, J., Tang, H., Yang, S., Dang, X., Han, S. | MLSys | 2024 |

**Relevance to LOKI:** LOKI's on-device inference with 4-bit quantised Gemma 4 relies on the quantisation techniques pioneered by GPTQ and AWQ. The open-weight ecosystem established by LLaMA 2 and validated by Phi-3 enables privacy-preserving local inference.

---

## F. Multi-Agent Orchestration Frameworks

| Tag | Paper | Authors | Venue | Year |
|:----|:------|:--------|:------|:-----|
| L30 | **AutoGen: Enabling Next-Generation LLM Applications via Multi-Agent Conversation** | Wu, Q., Bansal, G., Zhang, J., Wu, Y., et al. | arXiv:2308.08155 | 2023 |
| L31 | **MetaGPT: Meta Programming for a Multi-Agent Collaborative Framework** | Hong, S., Zheng, X., Chen, J., Cheng, Y., et al. | ICLR | 2024 |
| L32 | **Semantic Kernel** | Microsoft | GitHub | 2023 |

**Relevance to LOKI:** LOKI's LangGraph cycle implements a deterministic finite-state variant of multi-agent conversation, with the typed `LokiState` dictionary providing the structured inter-node contract that MetaGPT's document pool provides.

---

## G. Benchmark Datasets & Evaluation

| Tag | Paper | Authors | Venue | Year |
|:----|:------|:--------|:------|:-----|
| L33 | **Learning to Navigate the Web** | Gur, E., Rueckert, U., Faust, A., Hakkani-Tur, D. | ICLR | 2019 |
| L34 | **Android in the Wild: A Large-Scale Dataset for Android Device Control** | Rawles, C., Li, A., Rodriguez, D., Riva, O., Lillicrap, T. | NeurIPS | 2023 |
| L35 | **AssistGUI: Task-Oriented Desktop Graphical User Interface Automation** | Gao, D., Ji, T., Zhou, L., Lin, M., et al. | CVPR | 2024 |
| L36 | **VisualWebArena: Evaluating Multimodal Agents on Realistic Visual Web Tasks** | Koh, J., Lo, R., Jang, L., Duvvur, V., et al. | ACL | 2024 |

**Relevance to LOKI:** MiniWob++ (L33) and WebArena provide canonical micro-task and realistic web benchmarks. AssistGUI targets the same desktop GUI task space as LOKI's native AX-tree path.

---

## H. RPA & Workflow Automation

| Tag | Paper | Authors | Venue | Year |
|:----|:------|:--------|:------|:-----|
| L37 | **Robotic Process Automation** | van der Aalst, W. M. P., Bichler, M., Heinzl, A. | Bus. Inf. Syst. Eng. | 2018 |
| L38 | **From Natural Language Processing to Automated Reasoning: Applications and Future Directions** | Chakraborti, T., Isahara, Y., Kambhampati, S. | AAAI Workshop (iRPA) | 2020 |
| L39 | **TaskBench: Benchmarking Large Language Models for Task Automation** | Shen, Y., Song, K., Tan, X., Li, D., Lu, W., Zhuang, Y. | NeurIPS | 2023 |
| L40 | **A Survey on Large Language Model-Based Autonomous Agents** | Wang, L., Ma, C., Feng, X., Zhang, Z., et al. | Front. Comput. Sci. | 2024 |

**Relevance to LOKI:** LOKI realises the NLP-to-RPA vision proposed by Chakraborti et al. (L38), with the Plan node accepting natural language and generating structured JSON driving Playwright and macOS Accessibility API.

---

## I. Performance Optimisation & Fine-Tuning

These references were identified during research phase 2 for optimising LOKI's inference pipeline.

| Tag | Paper | Authors | Venue / Source | Year |
|:----|:------|:--------|:---------------|:-----|
| R1 | **V-Droid: Advancing Mobile GUI Agents Through Generative Verifiers** | Dai, G., Jiang, S., Li, T., et al. (Microsoft Research) | arXiv:2503.15937 | 2025 |
| R2 | **Screen2AX: Vision-Based Approach for Automatic macOS Accessibility Generation** | Muryn, V., Sumyk, M., Hirna, M., Garkot, S., Shamrai, M. | arXiv:2507.16704 | 2025 |
| R3 | **OmniParser for Pure Vision Based GUI Agent** | Lu, Y., Yang, J., Shen, Y., Awadallah, A. (Microsoft) | arXiv:2408.00203 | 2024 |
| R4 | **OSWorld: Benchmarking Multimodal Agents for Open-Ended Tasks in Real Computer Environments** | Xie, T., Zhang, D., Chen, J., Li, X., et al. | NeurIPS (arXiv:2404.07972) | 2024 |
| R5 | **SeeAct: GPT-4V(ision) is a Generalist Web Agent, if Grounded** | Zheng, B., Gou, B., Kil, J., Sun, H., Su, Y. | ICML (arXiv:2401.01614) | 2024 |
| R6 | **LLMLingua-2: Data Distillation for Efficient and Faithful Task-Agnostic Prompt Compression** | Pan, Z., Wu, Q., Jiang, H., Xia, M., Luo, X., et al. | ACL (arXiv:2403.12968) | 2024 |
| R7 | **QLoRA: Efficient Finetuning of Quantized LLMs** | Dettmers, T., Pagnoni, A., Holtzman, A., Zettlemoyer, L. | NeurIPS (arXiv:2305.14314) | 2023 |

**Relevance to LOKI:** These papers inform potential optimisation strategies including verifier-driven action selection (replacing generative reasoning with classification), self-speculative decoding for faster inference, SLM fine-tuning on LOKI-specific action datasets, and GRPO-based reinforcement fine-tuning.

---

## J. Systems Referenced in Architecture Paper

These systems are cited in the LOKI IEEE architecture paper (`papers/Architecture.tex`).

| System | Reference | Description |
|:-------|:----------|:------------|
| Browser-Use | `browser-use` | Web automation framework |
| OpenAdapt | `openadapt` | Open-source RPA platform |
| Agent-E | `agent-e` (Abuelsaad, T. et al.) | Web automation agent |
| SeeAct | `seeact` (Zheng, B. et al.) | Vision-based web agent |
| ScreenAgent | `ScreenAgent` (Liu, R. et al.) | Screen-based agent |
| OSWorld | `osworld` (Xie, T. et al.) | OS-level automation benchmark |
| WebArena | `webarena` (Zhou, S. et al.) | Realistic web environment benchmark |
| Generative Agents | `generative-agents` (Park, J. S. et al.) | Simulative agent architectures |
| MemGPT | `memgpt` (Packer, C. et al.) | Memory-augmented LLM system |
| EmbeddingGemma | `embeddinggemma` (Google DeepMind) | Embedding model |
| LangGraph | `langgraph` (LangChain, Inc.) | Agent orchestration framework |

---

## Citation

If you use LOKI or reference this work in academic publications, please cite using the
[CITATION.cff](CITATION.cff) file or the following BibTeX:

```bibtex
@software{prananta2026loki,
  author    = {Prananta G., Nathan Aldyth},
  title     = {{LOKI}: Local Orchestration Kernel Interface},
  year      = {2026},
  url       = {https://github.com/nathangtg/loki},
  note      = {Final Year Project, Sunway University, Malaysia.}
}
```

---

> **Disclosure:** The literature review was compiled with AI-assisted research tools.
> All cited works correspond to papers known within their stated venues.
> Human verification of DOIs and page numbers against IEEE Xplore / arXiv is

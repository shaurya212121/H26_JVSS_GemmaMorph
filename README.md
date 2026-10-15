# GemmaMorph — Autonomous Dependency Exit Rehearsal Engine
**Track 1: Best Use of Gemma 4 (Dependency Resilience)**  
**Hacktoberfest Hack Day Mumbai × ISTE KJSSE 2026**  
**Team Name:** `JVSS` | **Final Repository:** `H26_JVSS_GemmaMorph`

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.12 | 3.13](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![Model: Google Gemma](https://img.shields.io/badge/Model-Google%20Gemma%202%20%2F%204-purple.svg)](https://ai.google.dev/gemma)
[![Target: LinkWatch](https://img.shields.io/badge/Target-rishrek%2FLinkWatch-green.svg)](https://github.com/rishrek/LinkWatch)

---

## 📌 Executive Summary

Modern open-source repositories often suffer from silent dependency decay. Pinned libraries get abandoned, interfaces deprecate, and runtime environments drift—yet maintainers rarely discover these breaking points until production crashes.

Most AI developer tools provide shallow, hallucination-prone recommendations by merely feeding `requirements.txt` into a generic chatbot. **GemmaMorph** takes a radically different engineering approach: **Exit Rehearsal**.

> **The Core Philosophy:** Don't just tell maintainers what package to replace. Rehearse the replacement in a disposable execution sandbox, run real test suites, measure edit coverage, and output a tamper-evident **Dependency Exit Capsule**.

---

## 🔬 Ground-Truth Discoveries in Target Repository (`LinkWatch`)

When GemmaMorph executes on `LinkWatch`, it uncovers real, verified bugs and architectural rot that static linters miss:

1. **The Pinned MarkupSafe Collision (`Jinja2==2.11.3`):**  
   Running baseline `pytest` on Python 3.12/3.13 triggers `ImportError: cannot import name 'soft_unicode' from 'markupsafe'`. The pinned Jinja2 2.11 release relies on deprecated MarkupSafe APIs that modern package managers no longer bundle.
2. **Abandoned System Calls (`appdirs==1.4.4`):**  
   Last updated in 2017 (>6 years stale), unmaintained and deprecated across modern OS distributions. Used directly in `linkwatch/cache.py:4`.
3. **Archived Serializer (`toml==0.10.2`):**  
   Archived in 2020. Python 3.11+ provides native stdlib `tomllib`, making this third-party dependency completely obsolete. Used in `linkwatch/config.py:3`.
4. **Pydantic v1 Deprecations:**  
   `linkwatch/config.py:4` uses Pydantic v1 `@validator` decorators with `pydantic==1.10.13`, despite `pyproject.toml` demanding `pydantic>=2`.
5. **Undeclared Direct Import (`certifi`):**  
   Imported directly in `linkwatch/checker.py` without being listed in `pyproject.toml` or `requirements.txt`.

---

## 🏗️ The 6-Stage "Exit Rehearsal" Architecture

```
┌───────────────────────────────────────────────────────────────────────────────────┐
│                                TARGET REPO: LinkWatch                             │
└─────────────────────────────────────────┬─────────────────────────────────────────┘
                                          │
                                          ▼
┌───────────────────────────────────────────────────────────────────────────────────┐
│ STAGE 1: Dependency Discovery (Deterministic Python Engine)                       │
│ • Parses requirements*.txt, pyproject.toml [project.dependencies]                 │
│ • Detects manifest version conflicts & undeclared direct imports                  │
└─────────────────────────────────────────┬─────────────────────────────────────────┘
                                          │
                                          ▼
┌───────────────────────────────────────────────────────────────────────────────────┐
│ STAGE 2: Risk Scanner (PyPI Metadata + Live PyTest Observation)                   │
│ • Fetches PyPI release dates, maintenance classifiers, python_requires            │
│ • Runs baseline pytest, attributing test crashes & deprecation warnings           │
│ • Local PyPI offline cache (_pypi_cache.json) guarantees 100% offline reliability │
└─────────────────────────────────────────┬─────────────────────────────────────────┘
                                          │
                                          ▼
┌───────────────────────────────────────────────────────────────────────────────────┐
│ STAGE 3: AST Call-Site Tracer (Ground Truth)                                      │
│ • Deep traversal of all repo .py files with Python `ast`                          │
│ • Records exact file, line number, API invocation, and 3-line context windows     │
└─────────────────────────────────────────┬─────────────────────────────────────────┘
                                          │
                                          ▼
┌───────────────────────────────────────────────────────────────────────────────────┐
│ STAGE 4: Gemma 4 / Gemma 2 Migration Reasoning (AI Layer)                         │
│ • Ingests AST snippets, failure logs, and PyPI metadata                           │
│ • Emits structured JSON: exact old_text/new_text pairs (NOT fragile unified diffs)│
│ • Validates text replacements directly against source files on disk               │
│ • Every proposal is strictly tagged [AI-PROPOSED]                                 │
└─────────────────────────────────────────┬─────────────────────────────────────────┘
                                          │
                                          ▼
┌───────────────────────────────────────────────────────────────────────────────────┐
│ STAGE 5: Exit Rehearsal Sandbox (Execution Proof)                                 │
│ • Clones repository into isolated temporary sandbox copies                        │
│ • Phase A: Baseline execution (`pytest tests/`)                                   │
│ • Phase B: Applies Gemma code edits & edits requirements.txt                      │
│ • Phase C: Tests individual proposals -> records passing/failing deltas           │
│ • Phase D: Unified Rehearsal applying all passing proposals                       │
└─────────────────────────────────────────┬─────────────────────────────────────────┘
                                          │
                                          ▼
┌───────────────────────────────────────────────────────────────────────────────────┐
│ STAGE 6: The Certified Dependency Exit Capsule (Deliverable)                      │
│ • Emits machine-readable EXIT_CAPSULE.json & rich EXIT_CAPSULE.md                 │
│ • Strict claim separation: [VERIFIED], [OBSERVED], [AI-PROPOSED], [REFUTED]      │
└───────────────────────────────────────────────────────────────────────────────────┘
```

---

## 🤖 AI Model Specifications & Compliance

As explicitly mandated by Track 1 Guidelines and Evaluation Criteria:

| Attribute | Specification |
|:---|:---|
| **Model Name** | Google Gemma 2 / Gemma 4 (`gemma2-9b-it` / `gemma2:2b`) |
| **Model Provider** | Google DeepMind / Google AI |
| **Model License** | Apache 2.0 (Open-Weights, Permissive) |
| **Access Method** | Dual-Engine: **Hosted API** (`Groq` cloud runner) or **Local Offline** (`Ollama` runner) |
| **Primary Role** | Semantic risk synthesis, code migration transpilation, and edge-case boundary reasoning |
| **Verification Boundary** | All model outputs are subjected to disk AST validation and sandboxed `pytest` execution before acceptance |

---

## 🛡️ Strict Separation of AI vs. Verified Facts

In strict compliance with **Line 136 of the Hackathon Handbook**:
* `[VERIFIED]`: Information definitively proven through code execution, AST parsing, or exit codes.
* `[OBSERVED]`: Runtime deprecation warnings, compiler notices, or test failures captured from stdout/stderr.
* `[AI-PROPOSED]`: Code migrations, rationale, and replacement packages generated by the Gemma model.
* `[REFUTED]`: Proposals generated by the model that failed during the sandboxed test suite execution.

---

## 🚀 Quickstart & Setup Guide

### 1. Prerequisites
* Python 3.12 or 3.13 installed.
* Target repository `LinkWatch` available locally.

### 2. Installation
```bash
# Clone the repository
git clone https://github.com/shaurya212121/H26_JVSS_GemmaMorph.git
cd H26_JVSS_GemmaMorph

# Install lightweight dependencies (No Docker required!)
pip install -r requirements.txt
```

### 3. Configure API Access (Optional for Hosted Gemma)
Copy `.env.example` to `.env` and insert your free Groq API key:
```bash
GROQ_API_KEY=gsk_your_key_here
LLM_BACKEND=groq
```
*(If running 100% offline, set `LLM_BACKEND=ollama` or run with local Ollama!)*

---

## 💻 CLI Usage

### Full End-to-End Exit Rehearsal:
```bash
python run.py --repo ../LinkWatch --hosted
```

### Fast Demonstration (Bypasses Sandbox Test Run):
```bash
python run.py --repo ../LinkWatch --hosted --skip-rehearsal
```

### Keep Sandbox Temporary Dirs for Inspection:
```bash
python run.py --repo ../LinkWatch --hosted --keep-sandbox
```

---

## 📊 Sample Output: The Dependency Exit Capsule

GemmaMorph writes its results into the `./output` directory:
* **`EXIT_CAPSULE.json`**: Complete structured telemetry schema with runtime deltas, commit hashes, and model parameters.
* **`EXIT_CAPSULE.md`**: Human-readable audit report ready for repository maintainers and pull requests.

---

## 👥 Team JVSS
Developed with passion for **Hacktoberfest Hack Day Mumbai 2026** at K. J. Somaiya School of Engineering (ISTE KJSSE).

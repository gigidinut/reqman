"""
ai_controller.py — Local LLM inference for requirement quality evaluation.

Uses `llama-cpp-python` to run a quantised Llama 3.2 1B Instruct model
on the CPU.  The model file is resolved dynamically relative to the
application root directory at:  <app_root>/ai_model/<model_file>.

Threading
─────────
LLM inference takes several seconds on CPU.  To prevent the PySide6 GUI
from freezing, all inference runs inside `AiWorker` (a QThread subclass)
which emits signals when the result is ready or an error occurs.  The
calling dialog connects to these signals and updates the UI on the main
thread.

Usage from a PySide6 dialog
───────────────────────────
    from reqman.controllers.ai_controller import AiWorker

    worker = AiWorker(body_text)
    worker.finished_signal.connect(self._on_ai_result)
    worker.error_signal.connect(self._on_ai_error)
    worker.start()

The `finished_signal` carries two strings: (score, critique).
The `error_signal` carries one string: the error message.
"""

import os
import re
from pathlib import Path

from PySide6.QtCore import QThread, Signal


# ═══════════════════════════════════════════════════════════════════
# MODEL PATH RESOLUTION
# ═══════════════════════════════════════════════════════════════════

# Resolve the application root directory dynamically.
# This file lives at reqman/controllers/ai_controller.py
# so the reqman root is one level up.
_APP_ROOT = Path(__file__).resolve().parent.parent

# Model file path — relative to the application root.
MODEL_DIR = _APP_ROOT / "ai_model"
MODEL_FILENAME = "Llama-3.2-1B-Instruct-Q4_K_M.gguf"
MODEL_PATH = MODEL_DIR / MODEL_FILENAME


# ═══════════════════════════════════════════════════════════════════
# SYSTEM PROMPT
# ═══════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = (
    "You are a ruthless expert Systems Engineer strictly grading technical requirements "
    "based on INCOSE best practices. A high-quality requirement must be atomic "
    "(one single thought), unambiguous, verifiable (testable), concise, and "
    "implementation-free (stating WHAT is required, not HOW to implement it). "
    "Subtract at least 2 points from the score for each missing characteristic of the"
    "Analyze the requirement provided. DO NOT rewrite it. DO NOT suggest a fix. "
    "Your grading MUST be strict and thorough, do NOT over-inflate the score, be very harsh with scoring."
    "Output exactly two lines. "
    "Line 1 must be 'SCORE: X/10'. "
    "Line 2 must be 'CRITIQUE: [Detail the exact violations of clarity, "
    "verifiability, or atomicity].'"
)


# ═══════════════════════════════════════════════════════════════════
# RESPONSE PARSING
# ═══════════════════════════════════════════════════════════════════

def parse_ai_response(raw_text: str) -> tuple:
    """
    Extract the score and critique from the raw LLM output.

    Expected format:
        SCORE: 7/10
        CRITIQUE: The requirement is ambiguous because...

    Returns:
        (score_str, critique_str) — e.g. ("7/10", "The requirement...")
        If parsing fails, returns ("N/A", <full raw text>).
    """
    score = "N/A"
    critique = raw_text.strip()

    # ── Try to extract "X/10" from a SCORE: line ─────────────────
    score_match = re.search(r"SCORE\s*:\s*(\d{1,2}\s*/\s*10)", raw_text, re.IGNORECASE)
    if score_match:
        # Normalise whitespace: "7 / 10" → "7/10"
        score = score_match.group(1).replace(" ", "")

    # ── Try to extract the CRITIQUE: text ────────────────────────
    critique_match = re.search(
        r"CRITIQUE\s*:\s*(.+)", raw_text, re.IGNORECASE | re.DOTALL
    )
    if critique_match:
        critique = critique_match.group(1).strip()

    return score, critique


# ═══════════════════════════════════════════════════════════════════
# AI WORKER THREAD
# ═══════════════════════════════════════════════════════════════════

class AiWorker(QThread):
    """
    Background thread that runs LLM inference on a requirement body.

    Signals
    ───────
    finished_signal(str, str) : (score, critique) on success.
    error_signal(str)         : error message on failure.

    The Llama model is loaded fresh for each evaluation to avoid
    keeping ~1 GB of RAM allocated when the AI is not in use.
    For production use with frequent calls, a singleton pattern
    with lazy loading would be more efficient.
    """

    # Signal payloads: (score_string, critique_string)
    finished_signal = Signal(str, str)
    # Error payload: error message string
    error_signal = Signal(str)

    def __init__(self, requirement_body: str, parent=None):
        super().__init__(parent)
        self._body = requirement_body

    def run(self):
        """Execute inference in the background thread.

        This method runs on a separate thread — it must NOT touch any
        Qt widgets directly.  All UI updates happen via the signals.
        """
        try:
            # ── Validate inputs ──────────────────────────────────
            if not self._body or not self._body.strip():
                self.error_signal.emit(
                    "The requirement Body field is empty.\n"
                    "Please write a requirement statement before checking with AI."
                )
                return

            # ── Verify model file exists ─────────────────────────
            if not MODEL_PATH.exists():
                self.error_signal.emit(
                    f"AI model file not found.\n\n"
                    f"Expected location:\n{MODEL_PATH}\n\n"
                    f"Please place the GGUF model file at that path."
                )
                return

            # ── Import llama-cpp-python (may not be installed) ───
            try:
                from llama_cpp import Llama
            except ImportError:
                self.error_signal.emit(
                    "The 'llama-cpp-python' package is not installed.\n\n"
                    "Install it with:\n"
                    "  pip install llama-cpp-python\n\n"
                    "For GPU acceleration (optional):\n"
                    "  CMAKE_ARGS=\"-DGGML_CUDA=on\" pip install llama-cpp-python"
                )
                return

            # ── Load the model ───────────────────────────────────
            # n_ctx=1024 keeps RAM usage low for the 1B model.
            # n_gpu_layers=0 forces CPU-only; set to -1 to offload
            # all layers to GPU if CUDA/Metal is available.
            # verbose=False suppresses llama.cpp's own logging.
            llm = Llama(
                model_path=str(MODEL_PATH),
                n_ctx=1024,
                n_gpu_layers=0,
                verbose=False,
            )

            # ── Build the prompt using chat format ───────────────
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": self._body.strip()},
            ]

            # ── Run inference ────────────────────────────────────
            response = llm.create_chat_completion(
                messages=messages,
                max_tokens=256,
                temperature=0.1,
                top_p=0.9,
            )

            # ── Extract the generated text ───────────────────────
            raw_text = response["choices"][0]["message"]["content"]

            # ── Parse score and critique ─────────────────────────
            score, critique = parse_ai_response(raw_text)

            self.finished_signal.emit(score, critique)

        except Exception as exc:
            self.error_signal.emit(f"AI inference failed:\n\n{exc}")

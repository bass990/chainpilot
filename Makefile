.PHONY: install test lint eval eval-small eval-dry eval-report clean help \
        backend frontend dev

help:
	@echo "Targets:"
	@echo ""
	@echo "  Dev / pipeline:"
	@echo "    install       -- pip install -e .[dev]"
	@echo "    backend       -- uvicorn backend.main:app --reload --port 8002 (cd chainpilot/)"
	@echo "    frontend      -- npm run dev (cd chainpilot/frontend/)"
	@echo ""
	@echo "  Quality:"
	@echo "    test          -- pytest tests/ -v (no LLM calls)"
	@echo "    lint          -- ruff check chainpilot eval tests"
	@echo ""
	@echo "  Eval harness (LLM calls — costs real money):"
	@echo "    eval-dry      -- run scoring on saved fixture outputs (no LLM, fast, free)"
	@echo "    eval-small    -- run eval on 5-scenario subset (~\$$1, ~30s)"
	@echo "    eval          -- run full eval: ~40 scenarios x 2 branches x 3 reps (~\$$14-43, ~10 min)"
	@echo "    eval-report   -- regenerate the markdown report from the most recent run"
	@echo ""
	@echo "  Cleanup:"
	@echo "    clean         -- remove __pycache__, .pytest_cache, .ruff_cache"
	@echo ""
	@echo "Always lint + test before running eval-small or eval."

# ── Install ───────────────────────────────────────────────────────────────────
install:
	pip install --upgrade pip
	pip install -e ".[dev]"

# ── Dev servers ───────────────────────────────────────────────────────────────
backend:
	cd chainpilot && uvicorn backend.main:app --reload --port 8002

frontend:
	cd chainpilot/frontend && npm run dev

# ── Quality ───────────────────────────────────────────────────────────────────
test:
	pytest tests/ -v --tb=short

lint:
	ruff check chainpilot eval tests

# ── Eval harness ──────────────────────────────────────────────────────────────
# These targets exist as scaffolds Day 1 — actual implementation lands Day 4-13.
# Each target calls into eval/runners.py + eval/scorers.py; on Day 1 those
# raise NotImplementedError if invoked, which is the correct guard.

eval-dry:
	python -m eval.runners --mode dry --report eval/reports/latest_dry.md

eval-small:
	python -m eval.runners --mode small --scenario-count 5 --reps 1

eval:
	python -m eval.runners --mode full --scenario-count 40 --reps 3 --branches full,stripped

eval-report:
	python -m eval.runners --mode report-only --from eval/reports/latest_run.json

# ── Cleanup ───────────────────────────────────────────────────────────────────
clean:
	rm -rf .pytest_cache .ruff_cache
	find . -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

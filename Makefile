# 17x9-Tetris: root GNU Makefile.
#
# FreeBSD: run `gmake`. Plain `make` there is BSD make, which can't read this file.
#
# A thin front door. The gate is bin/verify.sh, the tests are pytest, and each
# language keeps its own linter and test runner. A language's lint-<lang> and
# test-<lang> targets do nothing (and say so) until impl/<lang>/ exists, so this
# file grows with the rebuild: Python -> Hy -> Clojure/ClojureScript -> Guile.
#
#   gmake                 this help
#   gmake run SEED=7      a seeded bot game in the terminal
#   gmake demo            replay a conformance trace
#   gmake lint test verify

SHELL := /bin/sh
.DEFAULT_GOAL := help
.DELETE_ON_ERROR:

UV    ?= uv
RUFF  ?= ruff
BB    ?= bb
HY2PY ?= hy2py
EMACS ?= emacs
# FreeBSD packages Guile 3's tool as guild3; elsewhere it is usually guild.
GUILD ?= guild3

# pytest runs from the jail's shared venv when it exists (it sees the system
# numpy, hypothesis and hy); elsewhere through uv and the dev group in
# pyproject.toml (not synced by default: the simulator itself is stdlib-only).
VENV   ?= /scratch/venvs/tetris-py
PYTEST ?= $(if $(wildcard $(VENV)/bin/python),$(VENV)/bin/python -m pytest,$(UV) run --group dev python -m pytest)

# run / demo knobs
SEED   ?= 1
FRAMES ?= 900
SPEED  ?= 1.0
TRACE  ?= spec/conformance/traces/08-hold.json
SIM    := $(UV) run python -m tetris_sim

PY_DIR  ?= impl/python
HY_DIR  ?= impl/hy
CLJ_DIR ?= impl/clojure
EL_DIR  ?= impl/elisp
SCM_DIR ?= impl/guile

.PHONY: help deps run demo demo-final \
        lint lint-python lint-hy lint-clojure lint-elisp lint-guile \
        test test-python test-hy test-clojure test-guile \
        verify check fmt fmt-check

help: ## List targets (the default)
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## / {printf "  gmake %-13s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ----------------------------------------------------------------- tooling ---

deps: ## Report the toolchain (no sudo), then uv sync; prints the pkg line for anything missing
	@missing=""; \
	for spec in uv:uv python3.12:python312 ruff:ruff bb:babashka clojure:clojure guile3:guile3 hy:py312-hy; do \
	  cmd=$${spec%%:*}; pkg=$${spec#*:}; \
	  if p=$$(command -v $$cmd 2>/dev/null); then \
	    printf '  ok       %-11s %-28s %s\n' "$$cmd" "$$p" "$$($$cmd --version 2>&1 | head -n 1)"; \
	  else \
	    printf '  MISSING  %-11s (FreeBSD package: %s)\n' "$$cmd" "$$pkg"; missing="$$missing $$pkg"; \
	  fi; \
	done; \
	if [ -n "$$missing" ]; then echo "install with:  pkg install -r FreeBSD-latest$$missing"; fi; \
	if command -v $(UV) >/dev/null 2>&1; then $(UV) sync; else echo "deps: $(UV) not found, skipping uv sync"; exit 1; fi

# ------------------------------------------------------------ run and demo ---

run: ## Seeded bot game in the terminal (SEED=1 FRAMES=900 SPEED=1.0)
	$(SIM) --seed $(SEED) --bot --frames $(FRAMES) --speed $(SPEED) --ansi

demo: ## Replay a conformance trace in the terminal (TRACE=spec/conformance/traces/08-hold.json)
	$(SIM) --trace $(TRACE) --speed $(SPEED) --ansi

demo-final: ## Replay a trace and print only its final frame (TRACE=...)
	$(SIM) --trace $(TRACE) --ansi-final

# -------------------------------------------------------------------- lint ---

lint: lint-python lint-hy lint-clojure lint-elisp lint-guile ## Lint every implementation present

lint-python: ## ruff check impl/python (legacy/ is excluded in ruff.toml)
ifneq ($(wildcard $(PY_DIR)),)
	@command -v $(RUFF) >/dev/null 2>&1 || { echo "lint-python: $(RUFF) not found (FreeBSD: pkg install -r FreeBSD-latest ruff)"; exit 127; }
	$(RUFF) check $(PY_DIR)
else
	@echo "lint-python: no $(PY_DIR)/ yet, skipping"
endif

# lint-hy: STUB. The main session finalizes it with the Hy phase (impl/hy/).
# Hy has no linter of its own, so each .hy file goes through hy2py into a temp
# dir and ruff checks the generated Python for syntax errors and undefined names
# only. Style rules stay off: generated code breaks them by construction.
lint-hy: ## [stub] hy2py each impl/hy .hy file, then ruff (syntax + undefined names)
ifneq ($(wildcard $(HY_DIR)),)
	@tmp=$$(mktemp -d "$${TMPDIR:-/tmp}/lint-hy.XXXXXX"); trap 'rm -rf "$$tmp"' EXIT; \
	status=0; n=0; \
	for f in $$(find $(HY_DIR) -name '*.hy' | sort); do \
	  n=$$((n + 1)); out="$$tmp/$$(echo "$$f" | tr '/' '_' | sed 's/\.hy$$//').py"; \
	  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$(HY_DIR) $(HY2PY) "$$f" > "$$out" 2> "$$out.err" \
	    || { echo "lint-hy: hy2py failed on $$f"; cat "$$out.err"; status=1; }; \
	done; \
	echo "lint-hy: [stub] $$n file(s) through $(HY2PY)"; \
	if [ "$$n" -gt 0 ]; then \
	  $(RUFF) check --isolated --no-cache --select E9,F63,F7,F82 "$$tmp" || status=1; \
	fi; \
	exit $$status
else
	@echo "lint-hy: no $(HY_DIR)/ yet, skipping"
endif

lint-clojure: ## clj-kondo + cljfmt check in impl/clojure (bb lint, bb fmt)
ifneq ($(wildcard $(CLJ_DIR)),)
	cd $(CLJ_DIR) && $(BB) lint
	cd $(CLJ_DIR) && $(BB) fmt
else
	@echo "lint-clojure: no $(CLJ_DIR)/ yet, skipping"
endif

lint-elisp: ## byte-compile impl/elisp .el files, warnings as errors (no .elc left behind)
ifneq ($(wildcard $(EL_DIR)),)
	@tmp=$$(mktemp -d "$${TMPDIR:-/tmp}/lint-elisp.XXXXXX"); trap 'rm -rf "$$tmp"' EXIT; \
	files=$$(find $(EL_DIR) -name '*.el' | sort); \
	if [ -z "$$files" ]; then echo "lint-elisp: no .el files in $(EL_DIR)/"; exit 0; fi; \
	$(EMACS) -Q --batch -L $(EL_DIR) \
	  --eval "(setq byte-compile-error-on-warn t byte-compile-dest-file-function (lambda (f) (expand-file-name (concat (file-name-nondirectory f) \"c\") \"$$tmp\")))" \
	  -f batch-byte-compile $$files
else
	@echo "lint-elisp: no $(EL_DIR)/ yet, skipping"
endif

lint-guile: ## guild compile -W3 impl/guile .scm files, warnings as errors
ifneq ($(wildcard $(SCM_DIR)),)
	@tmp=$$(mktemp -d "$${TMPDIR:-/tmp}/lint-guile.XXXXXX"); trap 'rm -rf "$$tmp"' EXIT; \
	status=0; \
	for f in $$(find $(SCM_DIR) -name '*.scm' | sort); do \
	  out=$$(GUILE_AUTO_COMPILE=0 $(GUILD) compile -W3 -L $(SCM_DIR) -o "$$tmp/out.go" "$$f" 2>&1) || status=1; \
	  echo "$$out" | grep -v '^wrote ' | sed '/^$$/d'; \
	  if echo "$$out" | grep -q 'warning:'; then status=1; fi; \
	done; \
	exit $$status
else
	@echo "lint-guile: no $(SCM_DIR)/ yet, skipping"
endif

# -------------------------------------------------------------------- test ---

test: test-python test-hy test-clojure test-guile ## Every implementation present (a missing one is skipped)

test-python: ## pytest over impl/python: P1-P19, legacy differential, simulator, traces
	$(PYTEST) $(PY_DIR)

# test-hy: STUB. The main session finalizes it with the Hy phase.
test-hy: ## [stub] pytest over impl/hy (Hy test modules, collected via its conftest)
ifneq ($(wildcard $(HY_DIR)),)
	$(PYTEST) $(HY_DIR)
else
	@echo "test-hy: no $(HY_DIR)/ yet, skipping"
endif

test-clojure: ## bb test in impl/clojure
ifneq ($(wildcard $(CLJ_DIR)),)
	cd $(CLJ_DIR) && $(BB) test
else
	@echo "test-clojure: no $(CLJ_DIR)/ yet, skipping"
endif

test-guile: ## [stub] Guile test runner (wired with the Guile phase)
ifneq ($(wildcard $(SCM_DIR)),)
	@echo "test-guile: $(SCM_DIR)/ exists but no runner is wired yet"; exit 1
else
	@echo "test-guile: no $(SCM_DIR)/ yet, skipping"
endif

# -------------------------------------------------------------------- gate ---

verify: ## bin/verify.sh, the gate: verify the verifier, then every implementation vs the traces
	bin/verify.sh

check: lint test verify ## lint + test + verify

# ---------------------------------------------------------------- format ---

fmt: ## Format in place: ruff format impl/python (never legacy/), bb fmt:fix in impl/clojure
ifneq ($(wildcard $(PY_DIR)),)
	$(RUFF) format $(PY_DIR)
endif
ifneq ($(wildcard $(CLJ_DIR)),)
	cd $(CLJ_DIR) && $(BB) fmt:fix
endif

fmt-check: ## Check formatting without writing: ruff format --check, bb fmt
ifneq ($(wildcard $(PY_DIR)),)
	$(RUFF) format --check $(PY_DIR)
endif
ifneq ($(wildcard $(CLJ_DIR)),)
	cd $(CLJ_DIR) && $(BB) fmt
endif

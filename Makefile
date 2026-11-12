PYTHON ?= python3
PYTEST ?= $(PYTHON) -m pytest

.PHONY: bootstrap deploy-check smoke test

bootstrap:
	@echo "1. Copy .env.example to .env and fill secrets."
	@echo "2. Run: $(PYTHON) -m pip install -r requirements.txt"
	@echo "3. Run: $(PYTHON) scripts/register_discord_commands.py"
	@echo "4. Run: $(PYTHON) scripts/set_telegram_webhook.py"
	@echo "5. Review docs/BOOTSTRAP.md"

deploy-check:
	@$(PYTHON) -m compileall app
	@$(PYTHON) -m pytest -q

smoke:
	@curl -fsS http://localhost:8000/healthz

test:
	@$(PYTEST) -q

.PHONY: doctor up down test reproduce reset logs

doctor:
	@bash scripts/doctor.sh

up:
	docker compose up -d --build
	@bash scripts/wait-for-healthy.sh
	@echo ""
	@echo "Services ready:"
	@echo "  booking-api:  http://localhost:18080"
	@echo "  capacity-api: http://localhost:18081"
	@echo "  PostgreSQL:   localhost:25432"

down:
	docker compose down

test:
	docker compose exec booking-api pytest tests/ -v --tb=short
	docker compose exec capacity-api pytest tests/ -v --tb=short

reproduce:
	@bash scripts/reproduce.sh

reset:
	docker compose down -v
	docker compose up -d --build
	@bash scripts/wait-for-healthy.sh
	@echo "Reset complete."

logs:
	docker compose logs -f --tail=50

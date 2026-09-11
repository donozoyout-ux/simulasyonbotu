.PHONY: up down test backend frontend migrate
up:
	docker compose up --build
down:
	docker compose down
test:
	cd backend && pytest
backend:
	cd backend && uvicorn app.main:app --reload
frontend:
	cd frontend && npm run dev
migrate:
	cd backend && alembic upgrade head


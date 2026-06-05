.PHONY: install run test docker
install:
	cd backend && pip install -r requirements.txt
run:
	cd backend && python -m app.main
test:
	cd backend && pytest -q
docker:
	docker compose up --build

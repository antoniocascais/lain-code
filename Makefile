ARGS ?=

.PHONY: build serve run stop logs clean test

build:
	docker compose build

serve:
	docker compose up -d

run:
	docker compose run --rm lain python parse_models.py --dir /data $(ARGS)

stop:
	docker compose down

logs:
	docker compose logs -f

test:
	docker compose run --rm lain python -m pytest test_pricing.py -v

clean:
	docker compose down --rmi local

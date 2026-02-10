ARGS ?=

.PHONY: build serve run stop logs clean

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

clean:
	docker compose down --rmi local

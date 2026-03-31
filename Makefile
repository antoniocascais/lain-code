ARGS ?=

.PHONY: build rebuild serve run stop logs clean test

build:
	docker compose build

rebuild:
	docker compose build --no-cache

serve:
	docker compose up -d

run:
	docker compose run --rm lain python parse_models.py --dir /data $(ARGS)

stop:
	docker compose down

logs:
	docker compose logs -f

test:
	docker compose run --rm --build test

clean:
	docker compose down --rmi local

all: stop build test serve

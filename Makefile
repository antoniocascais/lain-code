ARGS ?=

.PHONY: build rebuild serve run stop logs clean test test-py test-js

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

test: test-js test-py

test-py:
	docker compose run --rm --build test

test-js:
	node --test 'static/**/*.test.js'

clean:
	docker compose down --rmi local

all: stop build test serve

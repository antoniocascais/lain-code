FROM python:3.12-slim AS base

RUN groupadd -r lain && useradd -r -g lain -d /app lain
RUN apt-get update && apt-get install -y --no-install-recommends iptables libcap2-bin \
    && setcap 'cap_net_admin+ep' "$(readlink -f /usr/sbin/iptables)" \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV HOME=/tmp
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py analyze_sessions.py parse_models.py ./
COPY static/ static/
COPY entrypoint.sh .

USER lain
EXPOSE 8000
ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]


FROM base AS test

USER root
COPY requirements-test.txt .
RUN pip install --no-cache-dir -r requirements-test.txt
COPY conftest.py pytest.ini test_*.py ./
USER lain
CMD ["python", "-m", "pytest", "-v"]

ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}
WORKDIR /stub
COPY server.py /stub/server.py
USER 1000:1000
EXPOSE 8000
CMD ["python", "-u", "/stub/server.py"]

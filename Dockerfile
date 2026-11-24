# Real standalone agentgateway, no Python web server or database.
FROM cr.agentgateway.dev/agentgateway:v1.7.0
WORKDIR /etc/agentgateway
COPY --chown=65532:65532 config/config.yaml /etc/agentgateway/config.yaml
COPY config/openapi.json /etc/agentgateway/openapi.json
EXPOSE 3000
# Upstream image entrypoint is /app/agentgateway.
CMD ["-f", "/etc/agentgateway/config.yaml"]

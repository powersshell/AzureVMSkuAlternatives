FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mcp_server.py .

# HTTP transport mode for Azure Container Apps / M365 Copilot
# Authentication is handled by Container Apps Easy Auth (Entra ID) at the infrastructure level
ENV MCP_TRANSPORT=http
ENV PORT=8000

EXPOSE 8000

CMD ["python", "mcp_server.py"]

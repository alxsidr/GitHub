# German A1 Learning Agents

Personal German A1 learning system with 5 AI agents, accessible via Telegram.

## Architecture
```
OneDrive → n8n → Python API → Claude + PONS → Telegram
```

## Quick Start (on your VPS)

### 1. Find your n8n Docker network name
```bash
docker network ls
```
Look for your n8n network (e.g., `n8n-network`, `n8n_default`, etc.).
If it doesn't exist or has a different name, update `docker-compose.yml` accordingly.

If your n8n doesn't have a named network yet, create one and connect n8n to it:
```bash
# Create the shared network
docker network create n8n-network

# Connect your running n8n container to it
docker network connect n8n-network <your-n8n-container-name>
```

### 2. Clone and configure
```bash
cd /opt
git clone https://github.com/YOUR_USERNAME/german-agent.git
cd german-agent

# Create your .env file from template
cp .env.example .env
nano .env  # Fill in your real API keys
```

### 3. Build and start
```bash
docker-compose up -d --build
```

### 4. Verify it's running
```bash
# Check container status
docker ps

# Check health endpoint
curl http://localhost:8000/api/health

# Check logs
docker logs german-api
```

### 5. Test from n8n
In n8n, create an HTTP Request node pointing to:
```
http://german-api:8000/api/health
```
If it returns JSON, n8n can talk to the Python API.

## Deploy updates
After pushing new code to GitHub:
```bash
cd /opt/german-agent
git pull
docker-compose up -d --build
```

## Useful commands
```bash
# View logs (follow mode)
docker logs -f german-api

# Restart container
docker-compose restart

# Stop everything
docker-compose down

# Rebuild from scratch (keeps data volumes)
docker-compose down
docker-compose up -d --build

# Delete everything including data (CAREFUL!)
docker-compose down -v
```

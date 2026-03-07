# BUJA Core Platform - Quick Start Guide

## 🚀 Running the Backend

### 1. Install Dependencies

```bash
cd backend
python -m venv venv

# Windows
venv\Scripts\activate

# Linux/Mac
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Generate Ed25519 Keys

**CRITICAL**: You must generate Ed25519 keys before running the backend.

```bash
python -c "
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization

private_key = ed25519.Ed25519PrivateKey.generate()
public_key = private_key.public_key()

private_pem = private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption()
)

public_pem = public_key.public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo
)

print('PRIVATE KEY (Copy to .env):')
print(private_pem.decode())
print('\nPUBLIC KEY (Copy to .env and worker agents.yaml):')
print(public_pem.decode())
"
```

### 3. Configure Environment

Create `.env` file in the `backend` directory:

```bash
cp ../.env.example .env
```

Edit `.env` and add your generated keys:

```env
# Minimal configuration for development
REDIS_URL=redis://localhost:6379/0

# JWT Secret (generate with: openssl rand -hex 32)
JWT_SECRET_KEY=your-secret-key-here

# Ed25519 Keys (from step 2)
JOB_SIGNING_PRIVATE_KEY=-----BEGIN PRIVATE KEY-----
MC4CAQAwBQYDK2VwBCIEI...
-----END PRIVATE KEY-----

JOB_SIGNING_PUBLIC_KEY=-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEA...
-----END PUBLIC KEY-----
```

### 4. Start Redis

```bash
# Using Docker
cd ../docker
docker-compose up -d redis

# Or install Redis locally
# Windows: https://redis.io/docs/getting-started/installation/install-redis-on-windows/
# Linux: sudo apt-get install redis-server
# Mac: brew install redis
```

### 5. Run the Backend

```bash
cd backend
python -m app.main

# Or with uvicorn directly
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The API will be available at: http://localhost:8000

---

## 🚀 Running Fullstack Locally (Backend + Frontend + Infra)

### 1. Prepare env files

```bash
cp .env.example .env
# Optional: frontend-only overrides
cp frontend/.env.local.example frontend/.env.local 2>/dev/null || true
```

### 2. Run shared infra (Redis/Neo4j)

```bash
cd docker
docker compose up -d redis neo4j
cd ..
```

### 3. Start backend and frontend together

```bash
cd scripts
bash run_fullstack.sh
```

This starts:

- Redis + Neo4j (via `docker compose`)
- backend (`uvicorn app.main:app`)
- frontend (`next dev --hostname 0.0.0.0 --port 3000`)

Stop all:

```bash
cd scripts
bash stop_fullstack.sh
```

### 4. Health Check

```bash
cd scripts
bash check_fullstack.sh
```

### Notes

- Backend default is `http://127.0.0.1:8000` in this project template.
- Frontend API URL is resolved by `process.env.NEXT_PUBLIC_API_URL` (`frontend/.env.local`).
- DB is configured through shared DB URL in `DATABASE_URL` (`.env`).

API Documentation: http://localhost:8000/docs

---

## 🧪 Testing the API

### 1. Login (Get JWT Token)

```bash
curl -X POST http://localhost:8000/api/v1/auth/token \
  -H "Content-Type: application/json" \
  -d '{
    "username": "admin",
    "password": "admin123"
  }'
```

Response:
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_in": 86400
}
```

### 2. Create a Job

```bash
export TOKEN="your-token-here"

curl -X POST http://localhost:8000/api/v1/jobs \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "execution_location": "LOCAL_MACHINE",
    "provider": "OLLAMA",
    "model": "mimo-v2-flash",
    "timeout_sec": 600,
    "repo_root": "/home/user/projects/test",
    "allowed_paths": ["src/", "tests/"],
    "metadata": {
      "objective": "Test job creation",
      "requirements": ["Create a simple function"]
    }
  }'
```

Response:
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "QUEUED",
  "message": "Job queued successfully for LOCAL_MACHINE execution"
}
```

### 3. Check Job Status

```bash
curl -X GET http://localhost:8000/api/v1/jobs/{job_id}/status \
  -H "Authorization: Bearer $TOKEN"
```

---

## 🔍 Verify Job Signing

Run the test suite to verify Ed25519 signing works correctly:

```bash
cd backend
pytest tests/test_security.py -v
```

Expected output:
```
🔒 Testing Ed25519 Job Signing Implementation
============================================================
✅ Job signed successfully: base64:SGVsbG9Xb3JsZA==...
✅ Signature verification successful
✅ Tampered job correctly rejected
============================================================
✅ All signature tests passed!
```

---

## 📝 Default Users

For development, the following users are available:

| Username | Password | Role | Tenant |
|----------|----------|------|--------|
| admin | admin123 | SUPER_ADMIN | tenant_hyungnim |
| user1 | user123 | STANDARD_USER | tenant_hyungnim |

**⚠️ Change these passwords in production!**

---

## 🐛 Troubleshooting

### Redis Connection Error
```
Failed to connect to Redis
```
**Solution**: Ensure Redis is running on localhost:6379

### Invalid Signature Format
```
JOB_SIGNING_PRIVATE_KEY must be in PEM format
```
**Solution**: Regenerate keys using the script in Step 2

### Permission Denied
```
LOCAL_MACHINE execution requires SUPER_ADMIN role
```
**Solution**: Login as `admin` user or use `CLOUD` execution location

---

## 📚 Next Steps

1. **Test Worker Integration**: Set up Local Worker to poll for jobs
2. **Add Neo4j**: Configure agent roles in graph database
3. **Implement Dispatcher**: Add intent classification logic
4. **Add Telegram Bot**: Integrate Telegram authentication

---

## 🔗 Useful Links

- API Documentation: http://localhost:8000/docs
- Health Check: http://localhost:8000/health
- Redis CLI: `redis-cli`
- Logs: Check console output or configure file logging

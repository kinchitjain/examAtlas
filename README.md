# examAtlas
PrerequisitesInstall these on your machine first:ToolVersionDownload
Python  3.11 or 3.12  python.org
Node.js  18 or highernodejs.org  
Git  any  git-scm.com  
Redis  optional  redis.io


\examatlas> uvicorn app.main:app --port 8000
\examatlas-bff>npm install
\examatlas-bff>npm run dev
\examatlas-frontend>npm install
\examatlas-frontend>npm run dev
Backend setup
Open .env in a text editor and fill in:
ANTHROPIC_API_KEY=sk-ant-your-key-here
BFF_SECRET_KEY=pick-any-long-random-string-here
BFF_HMAC_KEY=pick-a-different-long-random-string
LLM_MODEL=claude-sonnet-4-20250514
GATEWAY_TIMEOUT_S=120
LOG_LEVEL=INFO
REDIS_URL=                    # leave blank to skip Redis


BFF proxy setup
BFF_PORT=3000
BACKEND_URL=http://localhost:8000
BFF_SECRET_KEY=pick-any-long-random-string-here    ← same as backend
BFF_HMAC_KEY=pick-a-different-long-random-string   ← same as backend
JWT_SECRET=yet-another-long-random-string
ALLOWED_ORIGINS=http://localhost:5173
REQUIRE_AUTH=false



 Frontend setup

# Render Blueprint — deploys the API + a managed Postgres database.
# In Render: New > Blueprint, point at this repo. It reads this file.
services:
  - type: web
    name: sweepstake-api
    runtime: python
    region: frankfurt
    plan: starter
    rootDir: backend
    buildCommand: "pip install -r requirements.txt"
    startCommand: "uvicorn app.main:app --host 0.0.0.0 --port $PORT"
    healthCheckPath: /health
    envVars:
      - key: PYTHON_VERSION
        value: "3.12.7"
      - key: DATABASE_URL
        fromDatabase:
          name: sweepstake-db
          property: connectionString
      - key: JWT_SECRET
        generateValue: true
      - key: CORS_ORIGINS
        sync: false        # set to your Vercel URL after the frontend deploys
      - key: FOOTBALL_API_KEY
        sync: false        # paste your football-data.org key (or leave blank for offline)
      - key: FOOTBALL_API_URL
        value: https://api.football-data.org/v4
      - key: FOOTBALL_POLL_SECONDS
        value: "60"
      - key: ENVIRONMENT
        value: production

databases:
  - name: sweepstake-db
    plan: basic-256mb
    region: frankfurt

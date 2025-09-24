# Chat Backend

FastAPI backend providing REST APIs and WebSocket real-time messaging.

## Features
- JWT-based authentication (signup/signin)
- MongoDB persistence (users, channels, messages)
- Channel management and membership
- Message history with pagination
- WebSocket endpoint for real-time chat in channels
- OpenAPI docs at `/docs`

## Environment
Provide the following variables (see `.env.example`):
- MONGODB_URI
- MONGODB_DB
- JWT_SECRET_KEY
- JWT_ALGORITHM (default HS256)
- JWT_EXPIRE_MINUTES (default 60)

## Run
- Install dependencies: `pip install -r requirements.txt`
- Start: `uvicorn src.api.main:app --host 0.0.0.0 --port 3001 --reload`

## REST Endpoints
- POST `/auth/signup`
- POST `/auth/signin` (OAuth2 form: username=email, password=password)
- GET `/users/me`
- POST `/channels`
- GET `/channels`
- GET `/channels/{channel_id}`
- POST `/channels/{channel_id}/join`
- POST `/channels/{channel_id}/leave`
- GET `/channels/{channel_id}/messages?limit=50&before_id=<msg_id>`
- POST `/channels/{channel_id}/messages`

## WebSocket
- Endpoint: `/ws/channels/{channel_id}`
- Auth: pass JWT via `?token=<JWT>` or `Authorization: Bearer <JWT>`
- Client send:
  - `{ "type": "message", "content": "Hello" }`
  - `{ "type": "ping" }`
- Server events: `message:new`, `pong`, `error`

## OpenAPI
- Regenerate: `python -m src.api.generate_openapi`
- Output: `interfaces/openapi.json`

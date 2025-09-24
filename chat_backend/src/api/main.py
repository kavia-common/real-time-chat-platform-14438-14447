import os
import datetime
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, Depends, HTTPException, status, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, Field, EmailStr
from jose import JWTError, jwt
from passlib.context import CryptContext
from starlette.responses import JSONResponse
from starlette.websockets import WebSocketState

from pymongo import MongoClient
from bson import ObjectId

# App metadata and FastAPI initialization with OpenAPI tags
app = FastAPI(
    title="Real-Time Chat Backend",
    description="Backend for real-time chat with JWT auth, MongoDB persistence, and Socket.IO-like WebSocket events.",
    version="1.0.0",
    openapi_tags=[
        {"name": "Health", "description": "Health and diagnostics"},
        {"name": "Auth", "description": "User authentication and tokens"},
        {"name": "Users", "description": "User management"},
        {"name": "Channels", "description": "Channel management and membership"},
        {"name": "Messages", "description": "Message CRUD and retrieval"},
        {"name": "Realtime", "description": "WebSocket endpoints for real-time chat"},
    ],
)

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Environment variables (do not hardcode; require via .env)
# Note: These variables must be provided externally by orchestrator or deployment environment
MONGODB_URI = os.getenv("MONGODB_URI")  # e.g., mongodb+srv://user:pass@host/dbname?options
MONGODB_DB = os.getenv("MONGODB_DB")    # Database name
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")  # Provide securely
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))

if not MONGODB_URI or not MONGODB_DB or not JWT_SECRET_KEY:
    # Raise a descriptive error early if environment not configured
    # In CI this will still import; but runtime calls may fail
    pass

# MongoDB setup
def get_db():
    """Get Mongo client and db lazily to avoid issues in import-time."""
    client = MongoClient(MONGODB_URI)  # type: ignore[arg-type]
    return client[MONGODB_DB]  # type: ignore[index]

# Collections
def users_col():
    return get_db()["users"]

def channels_col():
    return get_db()["channels"]

def messages_col():
    return get_db()["messages"]

# Security setup
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/signin")

# Utility helpers
def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def create_access_token(data: dict, expires_delta: Optional[datetime.timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.datetime.utcnow() + (expires_delta or datetime.timedelta(minutes=JWT_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)  # type: ignore[arg-type]
    return encoded_jwt

def oid(obj_id: str) -> ObjectId:
    try:
        return ObjectId(obj_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ID format")

def user_public(user_doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(user_doc["_id"]),
        "email": user_doc.get("email"),
        "display_name": user_doc.get("display_name"),
        "created_at": user_doc.get("created_at"),
    }

def channel_public(ch_doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(ch_doc["_id"]),
        "name": ch_doc.get("name"),
        "description": ch_doc.get("description"),
        "members": [str(uid) for uid in ch_doc.get("members", [])],
        "created_by": str(ch_doc.get("created_by")) if ch_doc.get("created_by") else None,
        "created_at": ch_doc.get("created_at"),
    }

def message_public(m_doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(m_doc["_id"]),
        "channel_id": str(m_doc.get("channel_id")),
        "sender_id": str(m_doc.get("sender_id")),
        "content": m_doc.get("content"),
        "created_at": m_doc.get("created_at"),
    }

# Pydantic models
class Token(BaseModel):
    access_token: str = Field(..., description="JWT access token")
    token_type: str = Field(default="bearer", description="Type of token")

class SignupRequest(BaseModel):
    email: EmailStr = Field(..., description="User email")
    password: str = Field(..., min_length=6, description="Password (min 6 chars)")
    display_name: Optional[str] = Field(None, description="Display name")

class SigninRequest(BaseModel):
    email: EmailStr = Field(..., description="User email")
    password: str = Field(..., description="Password")

class UserProfile(BaseModel):
    id: str = Field(..., description="User ID")
    email: EmailStr = Field(..., description="Email")
    display_name: Optional[str] = Field(None, description="Display name")
    created_at: Optional[datetime.datetime] = Field(None, description="Creation timestamp")

class ChannelCreate(BaseModel):
    name: str = Field(..., min_length=1, description="Channel name")
    description: Optional[str] = Field(None, description="Channel description")

class Channel(BaseModel):
    id: str = Field(..., description="Channel ID")
    name: str = Field(..., description="Channel name")
    description: Optional[str] = Field(None, description="Channel description")
    members: List[str] = Field(default_factory=list, description="User IDs in channel")
    created_by: Optional[str] = Field(None, description="Creator user ID")
    created_at: Optional[datetime.datetime] = Field(None, description="Creation timestamp")

class MessageCreate(BaseModel):
    content: str = Field(..., min_length=1, description="Message content")

class Message(BaseModel):
    id: str = Field(..., description="Message ID")
    channel_id: str = Field(..., description="Channel ID")
    sender_id: str = Field(..., description="Sender user ID")
    content: str = Field(..., description="Message content")
    created_at: Optional[datetime.datetime] = Field(None, description="Creation timestamp")

# Authentication dependencies
async def get_current_user(token: str = Depends(oauth2_scheme)) -> Dict[str, Any]:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])  # type: ignore[arg-type]
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = users_col().find_one({"_id": oid(user_id)})
    if not user:
        raise credentials_exception
    return user

# Routes
@app.get("/", tags=["Health"], summary="Health Check")
def health_check():
    """Health check endpoint for monitoring."""
    return {"message": "Healthy"}

# PUBLIC_INTERFACE
@app.post("/auth/signup", response_model=UserProfile, tags=["Auth"], summary="User Sign Up")
def signup(payload: SignupRequest):
    """Register a new user with email and password."""
    existing = users_col().find_one({"email": payload.email.lower()})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    user_doc = {
        "email": payload.email.lower(),
        "password_hash": hash_password(payload.password),
        "display_name": payload.display_name or payload.email.split("@")[0],
        "created_at": datetime.datetime.utcnow(),
    }
    res = users_col().insert_one(user_doc)
    user_doc["_id"] = res.inserted_id
    return user_profile(user_doc)

def user_profile(user_doc: Dict[str, Any]) -> UserProfile:
    return UserProfile(**user_public(user_doc))

# PUBLIC_INTERFACE
@app.post("/auth/signin", response_model=Token, tags=["Auth"], summary="User Sign In")
def signin(form_data: OAuth2PasswordRequestForm = Depends()):
    """
    Sign in using OAuth2 password form (username=email, password=password).
    Returns JWT token upon successful authentication.
    """
    user = users_col().find_one({"email": form_data.username.lower()})
    if not user or not verify_password(form_data.password, user.get("password_hash", "")):
        raise HTTPException(status_code=400, detail="Invalid email or password")
    token = create_access_token({"sub": str(user["_id"])})
    return Token(access_token=token, token_type="bearer")

# PUBLIC_INTERFACE
@app.get("/users/me", response_model=UserProfile, tags=["Users"], summary="Get Current User")
def get_me(current_user: Dict[str, Any] = Depends(get_current_user)):
    """Retrieve current user profile."""
    return user_profile(current_user)

# PUBLIC_INTERFACE
@app.post("/channels", response_model=Channel, tags=["Channels"], summary="Create Channel")
def create_channel(payload: ChannelCreate, current_user: Dict[str, Any] = Depends(get_current_user)):
    """Create a new channel and add creator as a member."""
    existing = channels_col().find_one({"name": payload.name})
    if existing:
        raise HTTPException(status_code=400, detail="Channel name already exists")
    ch_doc = {
        "name": payload.name,
        "description": payload.description,
        "members": [current_user["_id"]],
        "created_by": current_user["_id"],
        "created_at": datetime.datetime.utcnow(),
    }
    res = channels_col().insert_one(ch_doc)
    ch_doc["_id"] = res.inserted_id
    return Channel(**channel_public(ch_doc))

# PUBLIC_INTERFACE
@app.get("/channels", response_model=List[Channel], tags=["Channels"], summary="List Channels")
def list_channels(current_user: Dict[str, Any] = Depends(get_current_user)):
    """List channels that the user is a member of."""
    cur = channels_col().find({"members": {"$in": [current_user["_id"]]}})
    return [Channel(**channel_public(doc)) for doc in cur]

# PUBLIC_INTERFACE
@app.get("/channels/{channel_id}", response_model=Channel, tags=["Channels"], summary="Get Channel")
def get_channel(channel_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """Get a channel by ID if user is a member."""
    ch = channels_col().find_one({"_id": oid(channel_id)})
    if not ch:
        raise HTTPException(status_code=404, detail="Channel not found")
    if current_user["_id"] not in ch.get("members", []):
        raise HTTPException(status_code=403, detail="Not a member of this channel")
    return Channel(**channel_public(ch))

# PUBLIC_INTERFACE
@app.post("/channels/{channel_id}/join", response_model=Channel, tags=["Channels"], summary="Join Channel")
def join_channel(channel_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """Join a channel by ID."""
    ch = channels_col().find_one({"_id": oid(channel_id)})
    if not ch:
        raise HTTPException(status_code=404, detail="Channel not found")
    if current_user["_id"] in ch.get("members", []):
        return Channel(**channel_public(ch))
    channels_col().update_one({"_id": ch["_id"]}, {"$addToSet": {"members": current_user["_id"]}})
    ch = channels_col().find_one({"_id": ch["_id"]})
    return Channel(**channel_public(ch))

# PUBLIC_INTERFACE
@app.post("/channels/{channel_id}/leave", response_model=Channel, tags=["Channels"], summary="Leave Channel")
def leave_channel(channel_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """Leave a channel by ID."""
    ch = channels_col().find_one({"_id": oid(channel_id)})
    if not ch:
        raise HTTPException(status_code=404, detail="Channel not found")
    channels_col().update_one({"_id": ch["_id"]}, {"$pull": {"members": current_user["_id"]}})
    ch = channels_col().find_one({"_id": ch["_id"]})
    return Channel(**channel_public(ch))

# PUBLIC_INTERFACE
@app.get("/channels/{channel_id}/messages", response_model=List[Message], tags=["Messages"], summary="Fetch Messages")
def fetch_messages(channel_id: str, limit: int = 50, before_id: Optional[str] = None, current_user: Dict[str, Any] = Depends(get_current_user)):
    """Fetch messages for a channel (latest first)."""
    ch = channels_col().find_one({"_id": oid(channel_id)})
    if not ch:
        raise HTTPException(status_code=404, detail="Channel not found")
    if current_user["_id"] not in ch.get("members", []):
        raise HTTPException(status_code=403, detail="Not a member of this channel")

    query: Dict[str, Any] = {"channel_id": ch["_id"]}
    if before_id:
        # Paginate using _id
        query["_id"] = {"$lt": oid(before_id)}

    cur = messages_col().find(query).sort([("_id", -1)]).limit(max(1, min(200, limit)))
    msgs = [Message(**message_public(doc)) for doc in cur]
    return msgs

# PUBLIC_INTERFACE
@app.post("/channels/{channel_id}/messages", response_model=Message, tags=["Messages"], summary="Send Message")
def send_message(channel_id: str, payload: MessageCreate, current_user: Dict[str, Any] = Depends(get_current_user)):
    """Send a message to a channel."""
    ch = channels_col().find_one({"_id": oid(channel_id)})
    if not ch:
        raise HTTPException(status_code=404, detail="Channel not found")
    if current_user["_id"] not in ch.get("members", []):
        raise HTTPException(status_code=403, detail="Not a member of this channel")

    msg_doc = {
        "channel_id": ch["_id"],
        "sender_id": current_user["_id"],
        "content": payload.content,
        "created_at": datetime.datetime.utcnow(),
    }
    res = messages_col().insert_one(msg_doc)
    msg_doc["_id"] = res.inserted_id

    # Broadcast to WebSocket channel subscribers
    broadcast_channel_event(str(ch["_id"]), "message:new", message_public(msg_doc))

    return Message(**message_public(msg_doc))

# Real-time WebSocket management (simple Socket.IO-like messaging)
class ConnectionManager:
    def __init__(self):
        # channel_id -> set of WebSocket
        self.active_connections: Dict[str, set[WebSocket]] = {}

    async def connect(self, channel_key: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.setdefault(channel_key, set()).add(websocket)

    def disconnect(self, channel_key: str, websocket: WebSocket):
        conns = self.active_connections.get(channel_key)
        if not conns:
            return
        if websocket in conns:
            conns.remove(websocket)
        if not conns:
            self.active_connections.pop(channel_key, None)

    async def broadcast(self, channel_key: str, message: Dict[str, Any]):
        conns = self.active_connections.get(channel_key, set())
        dead: List[WebSocket] = []
        for ws in list(conns):
            try:
                if ws.application_state != WebSocketState.CONNECTED:
                    dead.append(ws)
                    continue
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(channel_key, ws)

manager = ConnectionManager()

def broadcast_channel_event(channel_id: str, event: str, data: Dict[str, Any]):
    # Fire and forget via background task approach: schedule using loop if needed
    import anyio
    async def _broadcast():
        await manager.broadcast(f"channel:{channel_id}", {"event": event, "data": data})
    try:
        anyio.from_thread.run(_broadcast)
    except RuntimeError:
        # Not in thread; run directly
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            loop.create_task(_broadcast())
        except RuntimeError:
            # As a last resort, run blocking
            anyio.run(_broadcast)

# PUBLIC_INTERFACE
@app.websocket("/ws/channels/{channel_id}")
async def websocket_channel(websocket: WebSocket, channel_id: str, token: Optional[str] = None):
    """
    WebSocket endpoint for real-time chat in a channel.
    Authentication: provide JWT as query parameter 'token' or header 'Authorization: Bearer <token>'.
    Usage notes:
    - Connect to ws(s)://<host>/ws/channels/{channel_id}?token=<JWT>
    - Incoming messages should be JSON objects with:
        {"type":"message", "content":"Hello"} to send a message
    - Server events:
        {"event":"message:new","data":{...message...}}
    """
    # Authenticate
    if not token:
        # Try to extract from headers
        auth = websocket.headers.get("authorization") or websocket.headers.get("Authorization")
        if auth and auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip()
    if not token:
        await websocket.close(code=4401)
        return

    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])  # type: ignore[arg-type]
        user_id = payload.get("sub")
        if not user_id:
            await websocket.close(code=4401)
            return
    except JWTError:
        await websocket.close(code=4401)
        return

    # Validate membership
    ch = channels_col().find_one({"_id": oid(channel_id)})
    if not ch:
        await websocket.close(code=4404)
        return
    if oid(user_id) not in ch.get("members", []):
        await websocket.close(code=4403)
        return

    channel_key = f"channel:{channel_id}"
    await manager.connect(channel_key, websocket)
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            if msg_type == "ping":
                await websocket.send_json({"event": "pong", "ts": datetime.datetime.utcnow().isoformat()})
            elif msg_type == "message":
                content = data.get("content")
                if not content or not isinstance(content, str):
                    await websocket.send_json({"event": "error", "error": "Invalid content"})
                    continue
                # Save and broadcast similar to REST
                msg_doc = {
                    "channel_id": ch["_id"],
                    "sender_id": oid(user_id),
                    "content": content,
                    "created_at": datetime.datetime.utcnow(),
                }
                res = messages_col().insert_one(msg_doc)
                msg_doc["_id"] = res.inserted_id
                await manager.broadcast(channel_key, {"event": "message:new", "data": message_public(msg_doc)})
            else:
                await websocket.send_json({"event": "error", "error": "Unknown message type"})
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(channel_key, websocket)

# API docs helper for WebSocket usage
# PUBLIC_INTERFACE
@app.get("/realtime/docs", tags=["Realtime"], summary="Realtime WebSocket Usage Help")
def realtime_docs():
    """
    Returns usage info for WebSocket connections for API docs.
    """
    return JSONResponse(
        {
            "ws_endpoint": "/ws/channels/{channel_id}",
            "auth": "Provide JWT via query param 'token' or Authorization header 'Bearer <token>'",
            "send_message_example": {"type": "message", "content": "Hello"},
            "server_events": ["message:new", "pong", "error"],
        }
    )

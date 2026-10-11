#!/usr/bin/env python3
"""
Telegram TDLib Daemon using tdjson directly

A persistent daemon that efficiently syncs and queries Telegram messages.
Uses user authentication (not bot).

Usage:
    ./telegram-tdlib-daemon.py login      # One-time login
    ./telegram-tdlib-daemon.py start     # Start daemon
    ./telegram-tdlib-daemon.py get-daily # Get today's messages
"""

import json
import os
import sys
import threading
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import tdjson

# Config
SESSION_DIR = Path(os.path.expanduser("~/.tdlib_daemon"))
CONFIG_PATH = SESSION_DIR / "config.json"
STATE_PATH = SESSION_DIR / "sync-state.json"
LOG_PATH = SESSION_DIR / "daemon.log"
HTTP_PORT = 18784

SESSION_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


class TDLibClient:
    def __init__(self, api_id: int, api_hash: str, files_dir: str = None):
        self.api_id = api_id
        self.api_hash = api_hash
        self.files_dir = files_dir or str(SESSION_DIR / "tdlib_files")
        self.client_id = None
        self._request_id = 0
        self._pending = {}  # request_id -> callback
        self._auth_state = None
        self._running = False
        self._thread = None
        
        Path(self.files_dir).mkdir(parents=True, exist_ok=True)
    
    def _create_client(self):
        self.client_id = tdjson.td_create_client_id()
        log.info("Created TDLib client: %d", self.client_id)
    
    def _send(self, data: dict):
        if not self.client_id:
            self._create_client()
        payload = {**data, "@client_id": self.client_id}
        tdjson.td_send(self.client_id, json.dumps(payload).encode('utf-8'))
    
    def _execute(self, data: dict) -> dict:
        if not self.client_id:
            self._create_client()
        payload = {**data, "@client_id": self.client_id}
        result = tdjson.td_execute(json.dumps(payload).encode('utf-8'))
        if result:
            return json.loads(result)
        return None
    
    def _receive(self, timeout: float = 0.1) -> dict:
        result = tdjson.td_receive(timeout)
        if result:
            return json.loads(result)
        return None
    
    def _poll(self):
        """Background poll loop for responses."""
        while self._running:
            resp = self._receive(timeout=0.5)
            if not resp:
                continue
            
            # Handle authorization state updates
            if resp.get("@type") == "updateAuthorizationState":
                self._auth_state = resp.get("authorization_state", {})
                state_type = self._auth_state.get("@type", "unknown")
                log.info("Auth state: %s", state_type)
                
                # Trigger callbacks
                if state_type in self._pending:
                    for cb in self._pending[state_type]:
                        try:
                            cb(self._auth_state)
                        except Exception as e:
                            log.error("Callback error: %s", e)
            
            # Handle responses with @extra.request_id
            extra = resp.get("@extra", {})
            req_id = extra.get("request_id")
            
            if req_id is not None and req_id in self._pending.get("responses", {}):
                cb = self._pending["responses"].pop(req_id)
                try:
                    cb(resp)
                except Exception as e:
                    log.error("Response callback error: %s", e)
    
    def send(self, data: dict, timeout: float = 30.0) -> dict:
        """Send request and wait for response via poll thread."""
        if not self.client_id:
            self._create_client()
        
        req_id = self._request_id
        self._request_id += 1
        
        payload = {**data, "@client_id": self.client_id, "@extra": {"request_id": req_id}}
        
        # Fire-and-forget methods (no response expected)
        fire_and_forget = ["setLogVerbosityLevel"]
        if data.get("@type") in fire_and_forget:
            self._send(payload)
            return {"@type": "ok"}
        
        # Register a slot for the response
        event = threading.Event()
        result_box = [None]
        
        def on_result(resp):
            result_box[0] = resp
            event.set()
        
        if "responses" not in self._pending:
            self._pending["responses"] = {}
        self._pending["responses"][req_id] = on_result
        
        # Send the request
        self._send(payload)
        
        # Wait for response
        if event.wait(timeout=timeout):
            return result_box[0]
        else:
            self._pending["responses"].pop(req_id, None)
            log.warning("Timeout waiting for response to %s", data.get("@type"))
            return {"@type": "error", "message": "timeout"}
    
    def wait_for_auth_state(self, target_states: list, timeout: float = 60.0) -> dict:
        """Wait for authorization state to reach target."""
        import asyncio
        
        # Already in target state?
        if self._auth_state:
            current = self._auth_state.get("@type")
            if current in target_states:
                return self._auth_state
        
        # Setup callback
        future = asyncio.Future()
        
        for state in target_states:
            if state not in self._pending:
                self._pending[state] = []
            self._pending[state].append(lambda s: future.set_result(s))
        
        # Also check response
        if "response" not in self._pending:
            self._pending["response"] = {}
        self._pending["response"][self._request_id] = future
        
        # Wait
        deadline = time.time() + timeout
        while time.time() < deadline:
            if future.done():
                return future.result()
            time.sleep(0.1)
        
        return None
    
    def start(self, interactive: bool = True):
        self._create_client()
        self._running = True
        
        # Start poll thread
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()
        
        # Initialize tdlib
        self.send({"@type": "setLogVerbosityLevel", "verbosity_level": 0})
        
        resp = self.send({"@type": "setTdlibParameters",
            "api_id": self.api_id,
            "api_hash": self.api_hash,
            "device_model": "OpenClaw",
            "application_version": "1.0",
            "system_version": "Linux",
            "system_language_code": "en",
            "database_directory": os.path.join(self.files_dir, "database"),
            "use_message_database": True,
            "files_directory": os.path.join(self.files_dir, "files"),
            "use_secret_chats": False,
            "use_test_dc": False,
        })
        
        log.info("setTdlibParameters: %s", resp.get("@type"))
        
        # Wait for auth state update
        time.sleep(1)
        
        if not interactive:
            log.warning("Not interactive, returning early")
            return False
        
        # Auth loop
        while True:
            state = self._auth_state.get("@type") if self._auth_state else "unknown"
            log.info("Current auth state: %s", state)
            
            if state == "authorizationStateReady":
                log.info("Authenticated!")
                return True
            
            if state == "authorizationStateWaitCode":
                code = input("Enter Telegram code: ").strip()
                self.send({"@type": "checkAuthenticationCode", "code": code})
                time.sleep(1)
            
            elif state == "authorizationStateWaitPassword":
                password = input("Enter 2FA password: ").strip()
                self.send({"@type": "checkAuthenticationPassword", "password": password})
                time.sleep(1)
            
            elif state == "authorizationStateWaitPhoneNumber":
                phone = input("Enter phone number (+1234567890): ").strip()
                self.send({"@type": "setAuthenticationPhoneNumber", "phone_number": phone})
                log.info("Sent phone, waiting for code...")
                # Wait for code state
                for _ in range(20):  # 10 seconds
                    time.sleep(0.5)
                    if self._auth_state and self._auth_state.get("@type") != state:
                        break
                state = self._auth_state.get("@type") if self._auth_state else "unknown"
                if state != "authorizationStateWaitCode":
                    log.info("Still in %s, waiting more...", state)
            
            elif state == "authorizationStateWaitEncryptionKey":
                self.send({"@type": "checkDatabaseEncryptionKey", "encryption_key": ""})
                time.sleep(1)
            
            elif state == "authorizationStateWaitTdlibParameters":
                # Shouldn't happen after we set params
                time.sleep(1)
            
            else:
                log.error("Unknown state: %s", state)
                break
    
    def get_chats(self, limit: int = 100) -> list:
        resp = self.send({"@type": "getChats", "limit": limit})
        return resp.get("chat_ids", []) if resp else []
    
    def get_chat(self, chat_id: int) -> dict:
        return self.send({"@type": "getChat", "chat_id": chat_id})
    
    def get_messages(self, chat_id: int, from_message_id: int = 0, limit: int = 50, cutoff_ts: int = 0) -> list:
        """Fetch messages, paginating until we have `limit` results or hit cutoff_ts."""
        all_msgs = []
        current_from = from_message_id
        max_pages = 5
        for _ in range(max_pages):
            resp = self.send({
                "@type": "getChatHistory",
                "chat_id": chat_id,
                "from_message_id": current_from,
                "limit": min(limit, 100),
            })
            msgs = resp.get("messages", []) if resp else []
            if not msgs:
                break
            all_msgs.extend(msgs)
            # Stop if we've gone past the cutoff
            oldest = msgs[-1].get("date", 0)
            if cutoff_ts and oldest < cutoff_ts:
                break
            if len(all_msgs) >= limit:
                break
            current_from = msgs[-1].get("id", 0)
            if current_from == 0:
                break
        return all_msgs
    
    def search_messages(self, query: str, chat_id: int = None, limit: int = 50) -> list:
        """Search messages by query."""
        params = {
            "@type": "searchMessages",
            "query": query,
            "limit": limit,
        }
        if chat_id:
            params["chat_id"] = chat_id
        resp = self.send(params)
        return resp.get("messages", []) if resp else []
    
    def close(self):
        self._running = False
        if self.client_id:
            self.send({"@type": "close"})


class SyncState:
    def __init__(self):
        self.state = {}
        self.load()
    
    def load(self):
        if STATE_PATH.exists():
            with open(STATE_PATH) as f:
                self.state = json.load(f)
    
    def save(self):
        with open(STATE_PATH, "w") as f:
            json.dump(self.state, f, indent=2)
    
    def get_last_sync(self, chat_id: int) -> int:
        return self.state.get(str(chat_id), {}).get("last_message_id", 0)
    
    def set_last_sync(self, chat_id: int, message_id: int):
        self.state[str(chat_id)] = {
            "last_message_id": message_id,
            "last_sync": datetime.now(timezone.utc).isoformat(),
        }
        self.save()


# Global
_client = None
_sync_state = None
_user_name_cache = {}  # persistent user_id → display name cache across requests


def load_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def save_config(config):
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


class RequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _client, _sync_state
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        
        try:
            if parsed.path == "/chats":
                chat_ids = _client.get_chats(limit=100)
                chats = []
                for cid in chat_ids:
                    chat = _client.get_chat(cid)
                    chats.append({"id": cid, "title": chat.get("title", "Unknown")})
                self._json({"chats": chats})
            
            elif parsed.path == "/muted-chats":
                chat_ids = _client.get_chats(limit=200)
                muted = []
                for cid in chat_ids:
                    chat = _client.get_chat(cid)
                    ns = chat.get("notification_settings", {})
                    if ns.get("mute_for", 0) > 0:
                        muted.append({"id": cid, "title": chat.get("title", "Unknown")})
                self._json({"muted_chats": muted})

            elif parsed.path == "/messages":
                chat_id = params.get("chat_id", [None])[0]
                if not chat_id:
                    self._json({"error": "chat_id required"}, 400)
                    return
                
                limit = int(params.get("limit", [50])[0])
                chat_id = int(chat_id)
                cutoff_ts = int(params.get("cutoff_ts", [0])[0])
                # Use caller-supplied from_message_id if provided, else fall back to sync state
                from_id_param = params.get("from_message_id", [None])[0]
                from_id = int(from_id_param) if from_id_param else _sync_state.get_last_sync(chat_id)
                
                msgs = _client.get_messages(chat_id, from_message_id=from_id, limit=limit, cutoff_ts=cutoff_ts)
                
                # Use module-level persistent user name cache
                global _user_name_cache
                user_cache = _user_name_cache
                def get_user_name(uid):
                    if uid in user_cache:
                        return user_cache[uid]
                    try:
                        u = _client.send({"@type": "getUser", "user_id": uid})
                        if u and u.get("@type") == "user":
                            name = " ".join(filter(None, [u.get("first_name",""), u.get("last_name","")])).strip()
                            user_cache[uid] = name or str(uid)
                        else:
                            user_cache[uid] = str(uid)
                    except:
                        user_cache[uid] = str(uid)
                    return user_cache[uid]

                results = []
                for m in msgs:
                    content = m.get("content", {})
                    text = content.get("text", {}).get("text", "") if content.get("@type") == "messageText" else ""
                    sender = m.get("sender_id", {})
                    sender_user_id = sender.get("user_id") if sender.get("@type") == "messageSenderUser" else None
                    entry = {
                        "id": m.get("id"),
                        "date": m.get("date"),
                        "text": text,
                    }
                    if sender_user_id:
                        entry["sender_user_id"] = sender_user_id
                        entry["sender_name"] = get_user_name(sender_user_id)
                    results.append(entry)
                
                # Only update sync state if we weren't doing a manual pagination
                if not from_id_param and msgs:
                    _sync_state.set_last_sync(chat_id, msgs[0].get("id"))
                
                self._json({"messages": results})
            
            elif parsed.path == "/sync-status":
                self._json({"state": _sync_state.state})
            
            elif parsed.path == "/health":
                self._json({"status": "ok"})
            
            elif parsed.path == "/me":
                resp = _client.send({"@type": "getMe"})
                if resp and resp.get("@type") == "user":
                    self._json({"user_id": resp.get("id"), "first_name": resp.get("first_name", ""), "last_name": resp.get("last_name", "")})
                else:
                    self._json({"error": "Could not get user"}, 500)
            
            elif parsed.path == "/search":
                query = params.get("query", [""])[0]
                if not query:
                    self._json({"error": "query required"}, 400)
                    return
                
                chat_id = params.get("chat_id", [None])[0]
                limit = int(params.get("limit", [50])[0])
                
                results = _client.search_messages(query, chat_id=int(chat_id) if chat_id else None, limit=limit)
                
                formatted = []
                for m in results:
                    content = m.get("content", {})
                    text = content.get("text", {}).get("text", "") if content.get("@type") == "messageText" else ""
                    chat = m.get("chat_id")
                    chat_title = None
                    if chat:
                        chat_obj = _client.get_chat(chat)
                        chat_title = chat_obj.get("title")
                    formatted.append({
                        "id": m.get("id"),
                        "date": m.get("date"),
                        "text": text,
                        "chat_id": chat,
                        "chat": chat_title,
                    })
                
                self._json({"messages": formatted})
            
            else:
                self._json({"error": "Not found"}, 404)
        
        except Exception as e:
            log.exception("HTTP error")
            self._json({"error": str(e)}, 500)
    
    def _json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())


def cmd_login():
    config = load_config()
    if not config.get("api_id"):
        print("Missing API credentials")
        api_id = input("api_id: ").strip()
        api_hash = input("api_hash: ").strip()
        config = {"api_id": int(api_id), "api_hash": api_hash}
        save_config(config)
    
    client = TDLibClient(config["api_id"], config["api_hash"])
    client.start(interactive=True)


def cmd_start():
    global _client, _sync_state
    
    config = load_config()
    if not config.get("api_id"):
        print("Run 'login' first!")
        return
    
    _client = TDLibClient(config["api_id"], config["api_hash"])
    _sync_state = SyncState()
    
    print("Starting TDLib client...")
    _client.start(interactive=False)
    
    # Wait for tdlib to sync
    time.sleep(3)
    
    # Start HTTP server
    server = HTTPServer(("127.0.0.1", HTTP_PORT), RequestHandler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    
    print(f"Daemon running. HTTP API: http://127.0.0.1:{HTTP_PORT}")
    print("Endpoints: /chats, /messages?chat_id=XXX, /sync-status, /health")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        _client.close()


def cmd_get_daily(days=1, limit_per_chat=20):
    config = load_config()
    if not config.get("api_id"):
        print("Run 'login' first!")
        return
    
    client = TDLibClient(config["api_id"], config["api_hash"])
    print("Starting client...")
    client.start(interactive=False)
    
    # Wait for tdlib to sync chat list
    time.sleep(5)
    
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    chat_ids = client.get_chats(limit=30)
    
    all_messages = []
    for chat_id in chat_ids[:15]:
        chat = client.get_chat(chat_id)
        chat_name = chat.get("title", "Unknown")
        
        msgs = client.get_messages(chat_id, limit=limit_per_chat)
        
        for m in msgs:
            msg_date = datetime.fromtimestamp(m.get("date", 0), timezone.utc)
            if msg_date >= cutoff:
                content = m.get("content", {})
                text = content.get("text", {}).get("text", "") if content.get("@type") == "messageText" else ""
                if text:
                    all_messages.append({
                        "chat": chat_name,
                        "chat_id": chat_id,
                        "id": m.get("id"),
                        "date": msg_date.isoformat(),
                        "text": text,
                    })
    
    all_messages.sort(key=lambda x: x["date"], reverse=True)
    print(json.dumps({"messages": all_messages[:500]}, indent=2))
    
    client.close()


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["login", "start", "get-daily"])
    parser.add_argument("--days", type=int, default=1)
    args = parser.parse_args()
    
    if args.command == "login":
        cmd_login()
    elif args.command == "start":
        cmd_start()
    elif args.command == "get-daily":
        cmd_get_daily(days=args.days)


if __name__ == "__main__":
    main()

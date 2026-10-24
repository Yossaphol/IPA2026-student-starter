"""Part 4 student implementation."""

import json

import logging

import os

from concurrent.futures import ThreadPoolExecutor

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pathlib import Path

from tempfile import TemporaryDirectory

from app.integration import process_request

from app.webex_client import WEBEX_API_BASE, WebexClient

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 1_000_000

class WebexEventHandler:
    def __init__(self, client, bot_person_id, processor=None):
        self.client = client
        self.bot_person_id = bot_person_id
        self.processor = processor if processor is not None else process_request

    def handle_event(self, event):
        if not isinstance(event, dict):
            return None
        if event.get("resource") != "messages" or event.get("event") != "created":
            return None
        data = event.get("data")
        if not isinstance(data, dict) or not data.get("id"):
            return None
        if data.get("personId") == self.bot_person_id:
            return None

        message = self.client.get_message(data["id"])
        if message.get("personId") == self.bot_person_id:
            return None

        result = self._process_message(message)
        self.client.send_message(
            message["roomId"], json.dumps(result, separators=(",", ":"))
        )
        return result

    def _process_message(self, message):
        mentioned_people = message.get("mentionedPeople", [])
        files = message.get("files") or []

        if len(files) > 1:
            return {"status": "error", "result": "multiple_attachments"}
        if not files:
            return self.processor(mentioned_people, None, self.bot_person_id)

        with TemporaryDirectory() as directory:
            path = Path(directory) / "request.yaml"
            path.write_bytes(self.client.download_file(files[0]))
            return self.processor(mentioned_people, path, self.bot_person_id)
        
def make_http_handler(submit_event):
    class WebhookHTTPHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/health":
                self._send_json(200, {"status": "ok", "result": "healthy"})
            else:
                self._send_json(404, {"status": "error", "result": "not_found"})

        def do_POST(self):
            if self.path != "/webhook":
                self._send_json(404, {"status": "error", "result": "not_found"})
                return

            event = self._read_event()
            if event is None:
                self._send_json(400, {"status": "error", "result": "invalid_event"})
                return

            submit_event(event)
            self._send_json(200, {"status": "ok", "result": "accepted"})

        def _read_event(self):
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return None
            if not 1 <= length <= MAX_BODY_BYTES:
                return None

            try:
                event = json.loads(self.rfile.read(length))
            except ValueError:
                return None
            if not isinstance(event, dict):
                return None
            return event

        def _send_json(self, status, body):
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            logger.info("%s %s", self.address_string(), format % args)

    return WebhookHTTPHandler


def _get_bot_person_id(client):
    response = client.session.get(
        f"{WEBEX_API_BASE}/people/me",
        headers=client.headers,
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["id"]


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    host = os.environ.get("WEBHOOK_HOST", "0.0.0.0")
    port = int(os.environ.get("WEBHOOK_PORT", "8000"))

    client = WebexClient()
    bot_person_id = _get_bot_person_id(client)
    handler = WebexEventHandler(client, bot_person_id)
    executor = ThreadPoolExecutor(max_workers=1)

    def run(event):
        try:
            result = handler.handle_event(event)
            if result is not None:
                logger.info("replied: %s", result)
        except Exception:
            logger.exception("failed to handle webhook event")

    def submit_event(event):
        executor.submit(run, event)

    server = ThreadingHTTPServer((host, port), make_http_handler(submit_event))
    logger.info("listening on %s:%s", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        executor.shutdown(wait=False)

if __name__ == "__main__":
    main()

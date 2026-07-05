#!/usr/bin/env python3
"""Tiny A2A JSON-RPC client for talking to a Kagenti-deployed agent."""

from __future__ import annotations

import argparse
import atexit
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from uuid import uuid4


DEFAULT_URL = "http://127.0.0.1:18081/"
CARD_PATH = ".well-known/agent-card.json"


def request_json(url: str, *, payload: dict | None, headers: dict, timeout: float) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="GET" if payload is None else "POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
        return json.loads(body or "{}")


def agent_card_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/" + CARD_PATH


def build_message(prompt: str, context_id: str | None) -> dict:
    message = {
        "role": "user",
        "messageId": uuid4().hex,
        "parts": [{"kind": "text", "text": prompt}],
    }
    if context_id:
        message["contextId"] = context_id

    return {
        "jsonrpc": "2.0",
        "id": uuid4().hex,
        "method": "message/send",
        "params": {"message": message},
    }


def extract_text(response: dict) -> str:
    result = response.get("result", {})
    texts: list[str] = []

    for artifact in result.get("artifacts", []):
        for part in artifact.get("parts", []):
            if part.get("kind") == "text" or part.get("type") == "text":
                texts.append(part.get("text", ""))

    status_message = result.get("status", {}).get("message", {})
    for part in status_message.get("parts", []):
        if part.get("kind") == "text" or part.get("type") == "text":
            texts.append(part.get("text", ""))

    if "parts" in result:
        for part in result["parts"]:
            if part.get("kind") == "text" or part.get("type") == "text":
                texts.append(part.get("text", ""))

    return "\n".join(text for text in texts if text)


def extract_context_id(response: dict, current_context_id: str | None) -> str | None:
    if current_context_id:
        return current_context_id
    result = response.get("result", {})
    return result.get("contextId") or result.get("sessionId")


def start_port_forward(namespace: str, service: str, local_port: int, remote_port: int) -> subprocess.Popen:
    process = subprocess.Popen(
        [
            "kubectl",
            "-n",
            namespace,
            "port-forward",
            f"svc/{service}",
            f"{local_port}:{remote_port}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    atexit.register(process.terminate)

    deadline = time.time() + 10
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError("kubectl port-forward exited before it was ready")
        try:
            with socket.create_connection(("127.0.0.1", local_port), timeout=0.2):
                return process
        except OSError:
            time.sleep(0.2)

    process.terminate()
    raise RuntimeError("timed out waiting for kubectl port-forward")


def send_message(url: str, prompt: str, context_id: str | None, headers: dict, timeout: float) -> dict:
    return request_json(
        url.rstrip("/") + "/",
        payload=build_message(prompt, context_id),
        headers=headers,
        timeout=timeout,
    )


def print_response(response: dict, raw: bool) -> int:
    if raw or "error" in response:
        print(json.dumps(response, indent=2))
        return 1 if "error" in response else 0

    text = extract_text(response)
    print(text or json.dumps(response.get("result", response), indent=2))
    return 0


def chat_loop(
    url: str,
    context_id: str | None,
    headers: dict,
    timeout: float,
    raw: bool,
    first_prompt: str | None,
) -> int:
    print("A2A chat. Type /exit to quit.")
    pending_prompt = first_prompt
    while True:
        if pending_prompt:
            prompt = pending_prompt
            pending_prompt = None
            print(f"> {prompt}")
        else:
            try:
                prompt = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0

        if prompt in {"/exit", "/quit"}:
            return 0
        if not prompt:
            continue

        response = send_message(url, prompt, context_id, headers, timeout)
        status = print_response(response, raw)
        context_id = extract_context_id(response, context_id)
        if context_id:
            print(f"[contextId: {context_id}]", file=sys.stderr)
        if status:
            return status


def main() -> int:
    parser = argparse.ArgumentParser(description="Chat with an A2A agent from your host.")
    parser.add_argument("prompt", nargs="*", help="optional first message; omit for interactive chat")
    parser.add_argument("--url", default=os.getenv("AGENT_URL", DEFAULT_URL))
    parser.add_argument("--context-id", help="reuse an A2A context ID")
    parser.add_argument("--token", default=os.getenv("KAGENTI_AGENT_TOKEN"))
    parser.add_argument("--card", action="store_true", help="print the agent card")
    parser.add_argument("--raw", action="store_true", help="print raw JSON response")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--port-forward", action="store_true", help="open kubectl port-forward to the agent service")
    parser.add_argument("--namespace", default="team1")
    parser.add_argument("--service", default="cockroach-db-agent")
    parser.add_argument("--local-port", type=int, default=18081)
    parser.add_argument("--remote-port", type=int, default=8080)
    args = parser.parse_args()

    headers = {"Content-Type": "application/json"}
    if args.token:
        headers["Authorization"] = f"Bearer {args.token}"

    try:
        if args.port_forward:
            start_port_forward(args.namespace, args.service, args.local_port, args.remote_port)
            args.url = f"http://127.0.0.1:{args.local_port}/"

        if args.card:
            print(json.dumps(request_json(agent_card_url(args.url), headers=headers, timeout=args.timeout), indent=2))
            return 0

        prompt = " ".join(args.prompt).strip()
        return chat_loop(args.url, args.context_id, headers, args.timeout, args.raw, prompt or None)

    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"HTTP {exc.code}: {body}", file=sys.stderr)
        if exc.code in (401, 403):
            print("Tip: pass --token or set KAGENTI_AGENT_TOKEN if AuthBridge is enforcing auth.", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Could not reach agent: {exc.reason}", file=sys.stderr)
        print("Tip: run with --port-forward, or start kubectl port-forward and pass --url.", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"Port-forward failed: {exc}", file=sys.stderr)
        return 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())

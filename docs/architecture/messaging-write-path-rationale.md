# Messaging Write-Path Rationale: Why Both REST and WebSocket Are Kept

An external reviewer recommended removing the WebSocket `send_message` action and using REST as the only write path. This document explains why the dual-path design is correct for this system and what would break with REST-only writes.

---

## The Reviewer's Concern

The concern was rule duplication: if both REST and WS can create messages, where do the business rules (enrollment gate, instructor gate, max body length) live?

**Answer: one place — `messaging_service.send_message()`.**

Both paths call the same function:

```
REST:  SendMessageView.post()  →  messaging_service.send_message()
WS:    MessagingStreamHandler  →  messaging_service.send_message()
```

There is no duplication. The service layer is the single source of truth. The transport layer (HTTP vs WS) is irrelevant to the rules.

---

## Why Both Paths Are Correct for This System

### 1. The WS connection is already open

The frontend maintains a persistent WS connection for real-time message delivery. That connection is open whenever the user is on the messaging page. Using it to also *send* messages keeps all messaging I/O on one socket. REST-only would mean: open WS (receive) + open HTTP connection per send = two network paths for the same data stream.

### 2. Lower per-message overhead on WS

An HTTP POST carries:
- TCP handshake (if no keep-alive, or for the initial request)
- TLS overhead
- Full HTTP headers (~500–800 bytes) on every request
- Response headers on every reply

A WS frame carries:
- 2–10 bytes of framing overhead
- No headers

For a learner asking several follow-up questions in sequence, WS sends are materially cheaper, especially on mobile or low-bandwidth connections.

### 3. Faster optimistic UI

With WS send, the client receives `message_sent` in the same event loop as the send — it is part of the same open socket. With REST, the sequence is:

```
Client  →  HTTP POST
         ← 201 response            (one round-trip)
         ← WS push (new_message)   (second round-trip via channel layer)
```

The WS path collapses this to one step. The client displays the message as sent as soon as `message_sent` arrives, without waiting for a REST round-trip.

### 4. Symmetric architecture

WS-send produces the same end state as REST-send: a `Message` row in the database, `updated_at` bumped on the `Conversation`, and a `new_message` push to the recipient. The symmetry makes the system easier to reason about:
- One code path to test for business rules.
- One code path to trace in logs.
- No special-casing in the frontend for "I sent this via REST so I don't need to listen for WS echo".

---

## What Would Break with REST-Only Writes

### 1. Two connections instead of one

The frontend would have to maintain a WS connection for receiving and make HTTP requests for sending. Every message typed requires opening a new HTTP connection (or reusing keep-alive, which is less reliable on mobile). This doubles the operational surface for the same feature.

### 2. No improvement on retry behaviour

The reviewer argued REST is simpler for retries. This is only true if your WS client does not handle reconnection. This system's WS client sits on a persistent `PlatformConsumer` — if the WS is disconnected, the user cannot send via WS anyway. If the WS is connected, a WS error frame (`type: error, detail: ...`) surfaces failures with the same UX as an HTTP 4xx response. There is no retry advantage to REST in this context.

### 3. Not simpler to monitor

REST-only implies HTTP access logs are the primary trace. But `messaging_service.send_message()` already emits structured Django logs with `conversation_id` and `user_id` regardless of transport. The WS path logs failures in `messaging_stream.py` with the same fields. Monitoring is equivalent.

### 4. Mobile battery cost

Each REST POST wakes the device radio. A burst of five messages = five radio wake events. Five WS frames on an open socket = zero additional radio wake events. For a mobile-first platform, this matters.

---

## When REST-Only Writes ARE the Right Choice

REST-only is a valid choice in these scenarios — none of which apply here:

| Scenario | Reason REST-only makes sense |
|---|---|
| Public third-party API | WS availability cannot be guaranteed across all clients (scripts, bots, CLI tools). REST is universally accessible. |
| Complex idempotency requirements | Payment processing, inventory mutations — where duplicate requests are catastrophic and idempotency keys on HTTP verbs are mature tooling. |
| No existing WS infrastructure | Django Channels + Redis is already deployed for notifications. Adding WS messaging is additive, not a new system cost. |
| Compliance audit trails | Some regulated industries require every mutation to produce an HTTP log entry. WS frames do not appear in standard web proxy logs. |

---

## Conclusion

Keep both paths. The reviewer's concern (rule duplication) does not apply because the service layer is the single source of truth for all messaging business logic. REST remains available as an unconditional fallback for clients that do not use WS (scripts, bots, server-to-server calls, offline message queues). WS remains the primary path for interactive frontend sessions because it is faster, cheaper, and architecturally symmetric with the delivery path.

The one real issue the reviewer identified — duplicate delivery to the sender (receiving both `message_sent` and `new_message` when sending via WS) — has been fixed by pushing `new_message` only to the recipient's channel group. See `messaging/services/messaging_service.py → _push_ws_and_notify`.

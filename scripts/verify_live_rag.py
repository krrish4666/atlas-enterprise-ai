"""scripts/verify_live_rag.py — Live end-to-end verification for Phase 3 RAG & Memory.

Tests against live Docker stack and native Ollama:
1. Ingest an organizational policy document.
2. Query grounded RAG endpoint (POST /api/chat) -> checks citations & grounding.
3. Multi-turn follow-up question in same session_id -> checks memory continuity.
4. Verify session list and message history endpoints.
5. Submit feedback (POST /api/chat/feedback).
6. Clean up document atomically (DELETE /api/documents/{id}).
"""

import time

import httpx

API_BASE = "http://localhost:8000"

POLICY_DOC = """
ATLAS ENTERPRISE AI — GLOBAL HR & BENEFITS POLICY (2026 EDITION)

Section 1: Annual Leave & Time Off
All regular full-time employees are entitled to 25 days of paid annual leave per calendar year.
In addition, employees receive 10 days of paid sick leave and 3 personal floating holidays.
Leave requests exceeding 5 consecutive business days must be submitted at least two weeks in advance.

Section 2: Remote Work & Utilities Stipend
Employees authorized for remote or hybrid work receive a monthly home internet stipend of $75.
Expense claims for internet must be submitted through the payroll portal by the 25th of each month.
Equipment provided by Atlas includes a MacBook Pro or ThinkPad laptop, a 27-inch 4K monitor, and peripherals.
"""


def main():
    print("========================================================")
    print("STARTING PHASE 3 LIVE END-TO-END RAG & MEMORY VERIFICATION")
    print("========================================================")

    session_id = f"live-rag-test-{int(time.time())}"

    with httpx.Client(timeout=30.0) as client:
        # ── 1. Upload Document ────────────────────────────────────────────────
        print("\n── Step 1: Uploading Test Organizational Document ──")
        filename = "atlas_global_hr_policy_2026.txt"
        file_bytes = POLICY_DOC.encode("utf-8")

        url_resp = client.post(
            f"{API_BASE}/api/documents/upload-url",
            json={"filename": filename, "content_type": "text/plain", "file_size": len(file_bytes)},
        )
        assert url_resp.status_code == 201, f"Upload URL failed: {url_resp.text}"
        doc_data = url_resp.json()
        doc_id = doc_data["document_id"]
        upload_url = doc_data["upload_url"]
        print(f"Document ID: {doc_id}")

        put_resp = client.put(
            upload_url, content=file_bytes, headers={"Content-Type": "text/plain"}
        )
        assert put_resp.status_code in (200, 204), f"PUT failed: {put_resp.text}"

        ingest_resp = client.post(f"{API_BASE}/api/documents/{doc_id}/ingest")
        assert ingest_resp.status_code == 202

        # Poll until indexed
        print("Waiting for document to be indexed by Ollama embedder...")
        for _ in range(30):
            poll = client.get(f"{API_BASE}/api/documents/{doc_id}").json()
            if poll["status"] == "indexed":
                print(f"Document indexed successfully into {poll['chunk_count']} chunk(s)!")
                break
            time.sleep(1)
        else:
            raise TimeoutError("Document ingestion timed out.")

        # ── 2. Ask Grounded Question ──────────────────────────────────────────
        print("\n── Step 2: Asking Grounded Question (Turn 1) ──")
        q1 = "How many days of paid annual leave do employees receive per year?"
        print(f"User Query: '{q1}'")
        chat_resp1 = client.post(
            f"{API_BASE}/api/chat",
            json={"session_id": session_id, "query": q1},
        )
        assert chat_resp1.status_code == 200, f"Chat failed: {chat_resp1.text}"
        data1 = chat_resp1.json()
        print(f"Assistant Answer: {data1['answer']}")
        print(f"Citations count: {len(data1['citations'])}")
        assert len(data1["citations"]) > 0, "Expected at least 1 grounded citation"
        first_citation = data1["citations"][0]
        print(f"Top citation: {first_citation['filename']} (score: {first_citation['score']})")
        assert first_citation["filename"] == filename
        turn1_msg_id = data1["message_id"]

        # ── 3. Multi-Turn Follow-Up (Session Memory) ──────────────────────────
        print("\n── Step 3: Asking Follow-up Question in Same Session (Turn 2) ──")
        q2 = "What about the monthly internet stipend amount?"
        print(f"Follow-up Query: '{q2}'")
        chat_resp2 = client.post(
            f"{API_BASE}/api/chat",
            json={"session_id": session_id, "query": q2},
        )
        assert chat_resp2.status_code == 200, f"Chat 2 failed: {chat_resp2.text}"
        data2 = chat_resp2.json()
        print(f"Assistant Answer: {data2['answer']}")
        assert len(data2["citations"]) > 0

        # ── 4. Verify Session List & History ──────────────────────────────────
        print("\n── Step 4: Verifying Chat Sessions & Message History ──")
        sessions_resp = client.get(f"{API_BASE}/api/chat/sessions")
        assert sessions_resp.status_code == 200
        all_sessions = sessions_resp.json()["sessions"]
        current_session = next((s for s in all_sessions if s["session_id"] == session_id), None)
        assert current_session is not None, f"Session {session_id} not found in session list"
        print(f"Session '{session_id}' found: message_count={current_session['message_count']}")
        assert current_session["message_count"] == 4, (
            f"Expected 4 messages, got {current_session['message_count']}"
        )

        # Get message thread
        msg_resp = client.get(f"{API_BASE}/api/chat/sessions/{session_id}/messages")
        assert msg_resp.status_code == 200
        messages = msg_resp.json()["messages"]
        assert len(messages) == 4
        print("Verified all 4 conversation turns:")
        for idx, m in enumerate(messages, 1):
            print(f"  {idx}. [{m['role'].upper()}]: {m['content'][:60]}...")

        # ── 5. Feedback Submission ───────────────────────────────────────────
        print("\n── Step 5: Submitting Feedback for Assistant Message ──")
        fb_resp = client.post(
            f"{API_BASE}/api/chat/feedback",
            json={
                "message_id": turn1_msg_id,
                "rating": 1,
                "comment": "Accurate policy details with clear citation.",
            },
        )
        assert fb_resp.status_code == 201
        print(f"Feedback recorded: {fb_resp.json()}")

        # ── 6. Cleanup (Flow D Atomic Deletion) ───────────────────────────────
        print("\n── Step 6: Atomic Document Deletion ──")
        del_resp = client.delete(f"{API_BASE}/api/documents/{doc_id}")
        assert del_resp.status_code == 200
        assert del_resp.json()["deleted"] is True
        print("Document and its vectors deleted atomically.")

    print("\n========================================================")
    print("ALL PHASE 3 LIVE END-TO-END RAG VERIFICATION CHECKS PASSED!")
    print("========================================================")


if __name__ == "__main__":
    main()

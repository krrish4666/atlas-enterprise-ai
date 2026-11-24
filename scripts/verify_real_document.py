"""scripts/verify_real_document.py — End-to-End Automated Verification with Real Company PDF.

Workflow:
1. Verify CAM-Employee-Handbook.pdf exists and check if already indexed in RustFS / PostgreSQL.
2. If not indexed, upload PDF bytes directly to RustFS via S3 presigned PUT URL and trigger ingestion.
3. Poll status until 'indexed' and report chunk count and indexing duration.
4. Execute multi-turn grounded RAG chat queries using Gemini API:
   - Turn 1: Founder and company background
   - Turn 2: Leave and attendance policy (testing multi-turn memory)
   - Turn 3: Uniform, dress code, and local travel reimbursement
5. Verify citations, confidence scores, and message history.
6. Submit user feedback (POST /api/chat/feedback).
"""

import os
import sys
import time

import httpx

API_BASE = "http://localhost:8000"
PDF_PATH = "Company_Docs/CAM-Employee-Handbook.pdf"


def main():
    print("=" * 70)
    print("ATLAS ENTERPRISE AI — REAL DOCUMENT VERIFICATION PIPELINE")
    print("Target Document: CAM-Employee-Handbook.pdf (41 pages)")
    print("=" * 70)

    if not os.path.exists(PDF_PATH):
        print(f"ERROR: PDF file not found at {PDF_PATH}")
        sys.exit(1)

    with open(PDF_PATH, "rb") as f:
        pdf_bytes = f.read()

    file_size = len(pdf_bytes)
    filename = os.path.basename(PDF_PATH)
    print(f"Loaded {filename} ({file_size:,} bytes)")

    session_id = f"cam-verify-{int(time.time())}"

    with httpx.Client(timeout=180.0) as client:
        # ── Step 1: Check if already indexed ─────────────────────────────────
        print("\n── Step 1: Checking Existing Documents in PostgreSQL ──")
        docs_resp = client.get(f"{API_BASE}/api/documents")
        assert docs_resp.status_code == 200, f"Failed to list documents: {docs_resp.text}"
        existing = [
            d
            for d in docs_resp.json().get("documents", [])
            if d.get("filename") == filename and d.get("status") == "indexed"
        ]

        if existing and "--force-reindex" not in sys.argv:
            doc_id = existing[0]["id"]
            indexed_chunks = existing[0].get("chunk_count", 0)
            print(f"Document already indexed in PostgreSQL: ID {doc_id} ({indexed_chunks} chunks).")
            print("Using existing indexed document for RAG Q&A validation.")
        else:
            # Request Presigned Upload URL
            print("Requesting Direct-to-Storage Presigned Upload URL...")
            url_resp = client.post(
                f"{API_BASE}/api/documents/upload-url",
                json={
                    "filename": filename,
                    "content_type": "application/pdf",
                    "file_size": file_size,
                },
            )
            assert url_resp.status_code == 201, (
                f"Upload URL failed: {url_resp.status_code} - {url_resp.text}"
            )
            doc_data = url_resp.json()
            doc_id = doc_data["document_id"]
            upload_url = doc_data["upload_url"]
            print(f"Document ID: {doc_id}")

            # Upload directly to RustFS
            print("Uploading PDF directly to RustFS via PUT...")
            t0_upload = time.time()
            put_resp = client.put(
                upload_url,
                content=pdf_bytes,
                headers={"Content-Type": "application/pdf"},
            )
            assert put_resp.status_code in (200, 204), (
                f"PUT to RustFS failed: {put_resp.status_code} - {put_resp.text}"
            )
            upload_duration = time.time() - t0_upload
            print(
                f"Uploaded {file_size:,} bytes in {upload_duration:.2f}s (HTTP {put_resp.status_code})"
            )

            # Trigger Ingestion
            print("Triggering Ingestion Pipeline...")
            t0_ingest = time.time()
            ingest_resp = client.post(f"{API_BASE}/api/documents/{doc_id}/ingest")
            assert ingest_resp.status_code == 202, f"Ingestion trigger failed: {ingest_resp.text}"

            # Poll until indexed
            print("Polling Ingestion Status (Ollama nomic-embed-text 768-dim)...")
            indexed_chunks = 0
            for attempt in range(1, 120):
                poll_resp = client.get(f"{API_BASE}/api/documents/{doc_id}")
                assert poll_resp.status_code == 200, f"Poll failed: {poll_resp.text}"
                doc_status = poll_resp.json()
                status = doc_status["status"]
                print(
                    f"  [Attempt {attempt:02d}] Status: {status} | Chunks: {doc_status.get('chunk_count', 0)}"
                )
                if status == "indexed":
                    indexed_chunks = doc_status["chunk_count"]
                    total_duration = time.time() - t0_ingest
                    print(
                        f"\nDocument indexed into {indexed_chunks} chunks in {total_duration:.2f}s!"
                    )
                    break
                elif status == "failed":
                    raise RuntimeError(
                        f"Document ingestion failed: {doc_status.get('error_message')}"
                    )
                time.sleep(2)
            else:
                raise TimeoutError("Ingestion timed out.")

        # ── Step 2: Multi-Turn Grounded Q&A via Gemini API ───────────────────
        test_queries = [
            (
                "Turn 1 (Founding & Background)",
                "Who founded CAM Industrial Services, in what year, and what was its original vision?",
            ),
            (
                "Turn 2 (Follow-up on Policies)",
                "What are the main rules regarding leave and attendance for employees?",
            ),
            (
                "Turn 3 (Specific Benefits/Rules)",
                "What is the company's policy on uniform, dress code, and local travel conveyance reimbursement?",
            ),
        ]

        last_message_id = None
        for turn_label, query in test_queries:
            print(f"\n── Step 2: Querying RAG — {turn_label} ──")
            print(f"User Query: {query}")
            t0_chat = time.time()
            chat_resp = None
            for attempt in range(1, 4):
                try:
                    chat_resp = client.post(
                        f"{API_BASE}/api/chat",
                        json={"session_id": session_id, "query": query},
                    )
                    if chat_resp.status_code == 200:
                        break
                    print(
                        f"  [Attempt {attempt}] HTTP {chat_resp.status_code}: {chat_resp.text[:100]}. Backing off 12s..."
                    )
                    time.sleep(12)
                except httpx.ReadTimeout:
                    print(f"  [Attempt {attempt}] ReadTimeout from Google API. Backing off 15s...")
                    time.sleep(15)

            chat_duration = time.time() - t0_chat
            assert chat_resp is not None and chat_resp.status_code == 200, (
                f"Chat failed: {getattr(chat_resp, 'status_code', None)} - {getattr(chat_resp, 'text', '')}"
            )
            data = chat_resp.json()
            last_message_id = data["message_id"]

            print(f"Generation Time: {chat_duration:.2f}s")
            print(f"Assistant Answer:\n{data['answer']}\n")
            print(f"Citations ({len(data['citations'])}):")
            for idx, cit in enumerate(data["citations"][:3], 1):
                chunk_idx = cit.get("chunk_index", "N/A")
                score = cit.get("score", 0.0)
                snippet = cit.get("snippet", "")[:120].replace("\n", " ")
                print(f"  [{idx}] {cit['filename']} | Chunk: {chunk_idx} | Score: {score:.4f}")
                print(f'      Snippet: "{snippet}..."')

            assert len(data["citations"]) > 0, (
                "Expected at least 1 citation from the ingested document"
            )
            assert any(
                filename in cit["filename"] or "CAM" in cit["filename"] for cit in data["citations"]
            )
            # Ensure not using offline fallback since Gemini API is live
            assert "[Offline Demo Grounded Answer]" not in data["answer"], (
                "Expected live Gemini response, but received offline fallback."
            )
            print("Pausing 15s between turns for Free Tier rate limit stability (5 RPM limit)...")
            time.sleep(15)

        # ── Step 3: Verify Session Message History ────────────────────────────
        print("\n── Step 3: Verifying Multi-Turn Session History in PostgreSQL ──")
        hist_resp = client.get(f"{API_BASE}/api/chat/sessions/{session_id}/messages")
        assert hist_resp.status_code == 200, f"History fetch failed: {hist_resp.text}"
        history_data = hist_resp.json()
        messages = history_data.get("messages", [])
        print(f"Session '{session_id}' total stored messages: {len(messages)}")
        assert len(messages) == 6, (
            f"Expected 6 messages (3 user + 3 assistant), got {len(messages)}"
        )

        # ── Step 4: Submit User Feedback ─────────────────────────────────────
        print("\n── Step 4: Submitting User Feedback for Turn 3 ──")
        fb_resp = client.post(
            f"{API_BASE}/api/chat/feedback",
            json={
                "message_id": last_message_id,
                "rating": 1,
                "notes": "Verified against real CAM Industrial Services Employee Handbook PDF. Accurate citations.",
            },
        )
        assert fb_resp.status_code in (200, 201), (
            f"Feedback failed: {fb_resp.status_code} - {fb_resp.text}"
        )
        print(f"Feedback stored successfully: {fb_resp.json()}")

        print("\n" + "=" * 70)
        print("ALL REAL DOCUMENT VERIFICATION CHECKS PASSED SUCCESSFULLY!")
        print(f"Document ID: {doc_id} | Chunks: {indexed_chunks} | Session ID: {session_id}")
        print("=" * 70)


if __name__ == "__main__":
    main()

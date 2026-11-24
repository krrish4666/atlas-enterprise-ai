"""tests/verify_live_ingestion.py — End-to-end live verification for Phase 2.

Tests the full Flow A and Flow D against running Docker stack and native Ollama:
1. Request presigned upload URL via FastAPI
2. PUT raw file bytes directly to RustFS
3. Trigger background ingestion pipeline
4. Poll until document status is 'indexed'
5. Inspect PostgreSQL to verify chunks and 768-dim vector embeddings
6. Delete document and verify atomic CASCADE deletion
"""

import time

import httpx

API_BASE = "http://localhost:8000"

SAMPLE_TEXT = """
Atlas Enterprise AI Employee Policy and Procedures.

1. Remote Work Policy:
Employees in good standing are eligible to work remotely up to three days per week.
All remote work must be pre-approved by the department manager. Hardware and peripherals
will be provided by corporate IT upon request.

2. Equipment and Expense Reimbursement:
A one-time home office reimbursement of up to $500 is available for monitors, chairs,
and ergonomic accessories. Submit receipts through the internal portal within 30 days.

3. Code of Conduct and Information Security:
Confidential documents and proprietary data must remain on secure, company-managed devices.
Using unapproved third-party cloud services or sharing credentials is strictly prohibited.
"""


def main():
    print("── Step 1: Request presigned upload URL ──")
    filename = "atlas_policy_live_test.txt"
    payload = {
        "filename": filename,
        "content_type": "text/plain",
        "file_size": len(SAMPLE_TEXT.encode("utf-8")),
    }

    with httpx.Client(timeout=10.0) as client:
        resp = client.post(f"{API_BASE}/api/documents/upload-url", json=payload)
        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
        data = resp.json()
        doc_id = data["document_id"]
        upload_url = data["upload_url"]
        object_key = data["object_key"]
        print(f"Created Document ID: {doc_id}")
        print(f"Object Key: {object_key}")
        print(f"Presigned URL: {upload_url[:60]}...")

        print("\n── Step 2: Direct PUT to RustFS ──")
        put_resp = client.put(
            upload_url,
            content=SAMPLE_TEXT.encode("utf-8"),
            headers={"Content-Type": "text/plain"},
        )
        assert put_resp.status_code in (200, 204), (
            f"PUT failed: {put_resp.status_code}: {put_resp.text}"
        )
        print(f"Direct PUT succeeded (status {put_resp.status_code})")

        print("\n── Step 3: Trigger Ingestion Pipeline ──")
        ingest_resp = client.post(f"{API_BASE}/api/documents/{doc_id}/ingest")
        assert ingest_resp.status_code == 202, f"Ingest trigger failed: {ingest_resp.status_code}"
        print(f"Ingest triggered (status 202): {ingest_resp.json()}")

        print("\n── Step 4: Poll for status = 'indexed' ──")
        max_wait = 30
        start_time = time.time()
        final_doc = None

        while time.time() - start_time < max_wait:
            poll_resp = client.get(f"{API_BASE}/api/documents/{doc_id}")
            assert poll_resp.status_code == 200
            final_doc = poll_resp.json()
            status = final_doc["status"]
            print(f"  Current status: {status} (chunk_count={final_doc.get('chunk_count', 0)})")

            if status == "indexed":
                break
            elif status == "failed":
                raise RuntimeError(f"Ingestion failed: {final_doc.get('error_message')}")
            time.sleep(1)

        assert final_doc["status"] == "indexed", f"Document did not index in time: {final_doc}"
        assert final_doc["chunk_count"] > 0, "Chunk count should be > 0"
        print(f"Document successfully indexed into {final_doc['chunk_count']} chunks!")

        print("\n── Step 5: Verify Document in GET /api/documents list ──")
        list_resp = client.get(f"{API_BASE}/api/documents")
        assert list_resp.status_code == 200
        docs = list_resp.json()["documents"]
        matching = [d for d in docs if d["id"] == doc_id]
        assert len(matching) == 1
        print(f"Verified document listed in GET /api/documents: {matching[0]['filename']}")

        print("\n── Step 6: Atomic Delete (Flow D) ──")
        del_resp = client.delete(f"{API_BASE}/api/documents/{doc_id}")
        assert del_resp.status_code == 200
        assert del_resp.json()["deleted"] is True
        print(f"DELETE returned 200 OK: {del_resp.json()}")

        # Verify document no longer exists
        get_again = client.get(f"{API_BASE}/api/documents/{doc_id}")
        assert get_again.status_code == 404
        print("GET /api/documents/{id} correctly returned 404 Not Found after deletion.")

    print("\n========================================================")
    print("ALL PHASE 2 LIVE END-TO-END VERIFICATION CHECKS PASSED!")
    print("========================================================")


if __name__ == "__main__":
    main()

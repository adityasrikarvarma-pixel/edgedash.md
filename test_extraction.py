#!/usr/bin/env python3
"""Test extraction: first run calls LLM, second run uses cache."""

import json
import time
from edgedash.agents.extractor import extract
from edgedash.storage import get_listings

db_path = "./edgedash.db"

# Get the first listing
listings = get_listings(db_path, limit=1)
if not listings:
    print("❌ No listings found in database. Run the fetcher first.")
    exit(1)

listing = listings[0]

print(f"\n{'='*70}")
print(f"TEST 1: First extraction (calls LLM, caches result)")
print(f"{'='*70}")
print(f"Listing: {listing['title']}")
print(f"Company: {listing['company']}")
print(f"Description length: {len(listing['description'] or '')} chars")
print()

start = time.time()
result1 = extract(listing, db_path)
elapsed1 = time.time() - start

print(f"Result (elapsed: {elapsed1:.2f}s):")
print(json.dumps(result1, indent=2))

print(f"\n{'='*70}")
print(f"TEST 2: Second extraction (instant from cache, no LLM call)")
print(f"{'='*70}")
print()

start = time.time()
result2 = extract(listing, db_path)
elapsed2 = time.time() - start

print(f"Result (elapsed: {elapsed2:.2f}s):")
print(json.dumps(result2, indent=2))

print(f"\n{'='*70}")
print(f"VERIFICATION")
print(f"{'='*70}")
print(f"Results identical: {result1 == result2}")
print(f"Test 1 time: {elapsed1:.2f}s (with LLM)")
print(f"Test 2 time: {elapsed2:.2f}s (cache hit)")
print(f"Speedup: {elapsed1 / max(elapsed2, 0.001):.0f}x faster with cache")

if elapsed2 < 0.1:  # Cache hit should be nearly instant
    print("✓ Cache working correctly")
else:
    print("⚠ Cache hit suspiciously slow, may have called LLM again")

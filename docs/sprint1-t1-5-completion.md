# T1-5 Completion: TLS Verification & Safe Binary Downloads

**Date:** 2026-06-24
**Status:** ✅ Completed

## Summary

Implemented TLS certificate verification for binary downloads with safe fallback options. Downloads now verify TLS certificates by default and require an explicit `--insecure` flag to disable verification.

## Changes Made

### Source Code (`src/llama_orchestrator/binaries/downloader.py`)

- Added `VERIFY_TLS = True` global constant (verify by default)
- Added `INSECURE_MODE = False` global constant
- Added `TLSVerificationError` exception class
- Added `TLSVerificationWarning` exception class
- `download_file()` function accepts `verify_tls` parameter
- TLS verification disabled logs security warning
- `--insecure` CLI flag propagates through download stack

### Test Coverage (`tests/test_tls_verification.py`)

- 32 tests covering:
  - TLS verification enabled (default)
  - TLS verification disabled (--insecure flag)
  - TLS certificate errors
  - Metadata caching (30-min TTL)
  - Download retry with TLS verification
  - Error handling and propagation

## Test Results

- **32 passed, 0 failed** in `test_tls_verification.py`
- Full suite: **562 passed, 0 failed**

## Security Properties

1. **Default-deny**: TLS verification is enabled by default
2. **Explicit opt-out**: Must use `--insecure` flag to disable
3. **Security warnings**: Logs warnings when TLS is disabled
4. **Metadata caching**: Release metadata cached with 30-min TTL to reduce network calls

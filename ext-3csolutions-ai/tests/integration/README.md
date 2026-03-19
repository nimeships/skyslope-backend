# Integration Tests

This directory contains integration tests that validate critical P0 fixes and end-to-end workflows.

## Test Coverage

### P0 Critical Fixes Validated

1. **test_s3_adapter_delete.py**
   - ✅ Validates S3Adapter.delete_file() implementation (Issue #1 fix)
   - ✅ Validates S3Adapter.delete_objects() batch operations
   - ✅ Tests AWS 1000-key limit chunking
   - ✅ Tests idempotent deletion behavior

2. **test_saga_cleanup.py**
   - ✅ Validates Saga compensation calls delete_file() (Issue #1 fix)
   - ✅ Tests AutoCompensatingSaga automatic cleanup
   - ✅ Tests compensation continues on individual failure
   - ✅ Critical test that would have caught missing delete_file() bug

3. **test_result_cache_invalidation.py**
   - ✅ Validates ResultCache.invalidate() deletes from S3 (Issue #2 fix)
   - ✅ Tests complete cache lifecycle: set → get → invalidate → get
   - ✅ Tests invalidation with file bytes (hash calculation)

4. **test_end_to_end_pipeline.py**
   - ✅ Complete pipeline workflow with Saga cleanup
   - ✅ Tests graceful shutdown with cleanup
   - ✅ Tests scalability (1-1500 files)
   - ✅ Multi-stage compensation in reverse order

## Running Tests

### Run All Integration Tests
```bash
cd ext-3csolutions-ai
pytest tests/integration/ -v
```

### Run Specific Test File
```bash
pytest tests/integration/test_saga_cleanup.py -v
```

### Run with Coverage
```bash
pytest tests/integration/ --cov=src --cov-report=term --cov-report=html
```

### Run with Verbose Output
```bash
pytest tests/integration/ -v -s
```

### Run Specific Test
```bash
pytest tests/integration/test_s3_adapter_delete.py::test_delete_file_success -v
```

## Test Requirements

Integration tests use moto for AWS service mocking:

```bash
pip install -r requirements-test.txt
```

Required packages:
- pytest>=7.4.3
- pytest-cov>=4.1.0
- pytest-mock>=3.12.0
- moto[s3,dynamodb]>=4.2.9

## Test Structure

```
tests/integration/
├── __init__.py
├── README.md (this file)
├── test_s3_adapter_delete.py       # S3 delete operations
├── test_saga_cleanup.py            # Saga compensation logic
├── test_result_cache_invalidation.py # Cache invalidation
└── test_end_to_end_pipeline.py     # Complete workflows
```

## Key Tests Explained

### Critical: test_saga_cleanup_deletes_s3_files()
**Why it's important:** This test validates Issue #1 fix. Before implementing delete_file(), this test would fail with AttributeError. Now it passes, proving Saga cleanup works.

```python
# Simulates pipeline failure and compensation
saga.mark_stage_complete('Unzip')
saga.compensate()  # Would crash before fix

# Verifies files are deleted
assert all files deleted from S3
```

### Critical: test_cache_invalidate_removes_from_s3()
**Why it's important:** Validates Issue #2 fix. Before completing invalidate(), this test would fail because cache files remained in S3.

```python
cache.set(file_hash, result)
cache.invalidate(file_hash)  # Now actually deletes

# Verifies cache file is gone
assert cached file deleted from S3
```

### Critical: test_pipeline_cleanup_on_failure()
**Why it's important:** End-to-end test proving all fixes work together in real pipeline scenario.

```python
# Complete workflow:
1. Create intermediate files
2. Register Saga compensations
3. Simulate failure
4. Verify cleanup deletes all intermediate files
5. Verify input files remain
```

## Continuous Integration

These tests should run on every commit:

```yaml
# .github/workflows/test.yml
- name: Run Integration Tests
  run: |
    pip install -r requirements-test.txt
    pytest tests/integration/ -v --cov=src
```

## Expected Results

All tests should pass:

```
tests/integration/test_s3_adapter_delete.py ............ [ 35%]
tests/integration/test_saga_cleanup.py ................ [ 65%]
tests/integration/test_result_cache_invalidation.py ... [ 80%]
tests/integration/test_end_to_end_pipeline.py ......... [100%]

======================== 30 passed in 5.23s ========================
```

## Troubleshooting

### Test fails with "No module named 'moto'"
```bash
pip install moto[s3,dynamodb]
```

### Test fails with "AttributeError: 'S3Adapter' object has no attribute 'delete_file'"
This means Issue #1 fix was not applied. Check that s3_adapter.py has delete_file() method.

### Test fails with "Cache file still exists after invalidation"
This means Issue #2 fix was not applied. Check that result_cache.py invalidate() calls s3.delete_file().

## Performance

Integration tests run fast due to moto mocking:
- Average test duration: ~0.15s per test
- Full suite: ~5 seconds
- No actual AWS API calls made

## Next Steps

Additional integration tests to add:
- [ ] DynamoDB adapter integration tests
- [ ] Direct PDF extraction service tests
- [ ] Validation service parallel processing tests
- [ ] EventBus integration tests

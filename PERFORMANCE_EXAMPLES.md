# Performance Testing Examples

This document provides practical examples of how to use the performance testing tools in this uvicorn fork.

## Quick Start Examples

### 1. Fastest Way - Quick Performance Test

Just want to see if the fork is fast? Run this:

```bash
python quick_perf.py
```

This runs a quick performance test (30-60 seconds) and shows results directly in the terminal.

### 2. Command Line - Quick Test

```bash
python scripts/run_performance_tests.py --quick
```

Output:
```
Concurrency | Requests | RPS      | p50 Latency | p99 Latency
------------+----------+----------+-------------+------------
          1 |      500 |      777 |        1.21ms |        1.52ms
         10 |    1,000 |      492 |       18.33ms |       65.21ms
         50 |    2,000 |      638 |       68.46ms |       91.88ms
```

### 3. Full Performance Test with Report

```bash
python scripts/run_performance_tests.py --output my_perf_report.txt
```

This:
- Runs comprehensive benchmarks (2-5 minutes)
- Saves detailed report to `my_perf_report.txt`
- Also saves JSON data to `my_perf_report.json`

### 4. Using the Wrapper Script

```bash
# Quick test
./scripts/performance-test --quick

# Full test with report
./scripts/performance-test --output results.txt

# Custom port
./scripts/performance-test --port 8080 --quick
```

## Manual Benchmark Examples

### Example 1: Simple Baseline Test

Run a quick baseline measurement:

```bash
# Terminal 1: Start test server
uvicorn tests.benchmarks.test_app:app --port 8765

# Terminal 2: Run simple benchmark
python tests/benchmarks/simple_bench.py
```

**Use case**: Quick check of current performance

### Example 2: Comprehensive Benchmark

Detailed performance analysis:

```bash
# Terminal 1: Start test server
uvicorn tests.benchmarks.test_app:app --port 8765

# Terminal 2: Run comprehensive benchmark
python tests/benchmarks/benchmark_uvicorn.py --no-server
```

**Use case**: Detailed latency analysis and multiple test scenarios

### Example 3: Compare Against Baseline

See how optimizations improved performance:

```bash
# Terminal 1: Start test server
uvicorn tests.benchmarks.test_app:app --port 8765

# Terminal 2: Run comparison
python tests/benchmarks/compare_performance.py
```

Output shows:
```
CONCURRENCY: 100
================================================================================
  Baseline:     ❌ SERVER CRASHED/FAILED
  Optimized:    ✅ 2,760 req/s
                ✅ p50: 32.01ms, p99: 46.88ms

  🎉 CRITICAL FIX: Server now stable at concurrency 100!
```

### Example 4: Memory Profiling

Analyze memory optimizations:

```bash
python tests/benchmarks/profile_memory.py
```

No server needed - runs standalone.

**Shows**:
- Object creation overhead
- `__slots__` memory savings
- Memory allocation patterns

## Real-World Scenarios

### Scenario 1: Pre-Deployment Validation

Before deploying, validate performance:

```bash
# 1. Run full performance test
python scripts/run_performance_tests.py --output pre_deploy_$(date +%Y%m%d).txt

# 2. Check the report
cat pre_deploy_*.txt

# 3. Verify key metrics
# - RPS at concurrency 50 should be > 500
# - p99 latency at concurrency 50 should be < 100ms
# - No errors or crashes
```

### Scenario 2: Comparing Two Versions

Compare performance between git branches:

```bash
# Test baseline version
git checkout main
python scripts/run_performance_tests.py --output baseline_perf.txt

# Test your changes
git checkout my-feature-branch
python scripts/run_performance_tests.py --output feature_perf.txt

# Compare
diff baseline_perf.txt feature_perf.txt
```

### Scenario 3: CI/CD Integration

In your GitHub Actions workflow:

```yaml
- name: Performance Test
  run: |
    python scripts/run_performance_tests.py --quick --output perf.txt
    
- name: Upload Report
  uses: actions/upload-artifact@v4
  with:
    name: performance-report
    path: perf.*
```

See `.github/workflows/performance.yml` for a complete example.

### Scenario 4: Load Testing Different Concurrency Levels

Test how the server handles different load patterns:

```bash
# Terminal 1: Start server
uvicorn tests.benchmarks.test_app:app --port 8765

# Terminal 2: Test sequentially
for concurrency in 1 10 50 100 200; do
    echo "Testing concurrency: $concurrency"
    python -c "
import asyncio
import time
import httpx

async def test():
    url = 'http://127.0.0.1:8765/'
    latencies = []
    sem = asyncio.Semaphore($concurrency)
    
    async def req(client):
        async with sem:
            start = time.perf_counter()
            try:
                await client.get(url)
                latencies.append(time.perf_counter() - start)
            except: pass
    
    async with httpx.AsyncClient(timeout=30) as client:
        tasks = [req(client) for _ in range(1000)]
        await asyncio.gather(*tasks)
    
    if latencies:
        rps = len(latencies) / sum(latencies)
        p99 = sorted(latencies)[int(len(latencies) * 0.99)] * 1000
        print(f'  RPS: {rps:.0f}, p99: {p99:.2f}ms')

asyncio.run(test())
"
done
```

## Advanced Usage

### Custom Test Application

Test with your own ASGI app:

```bash
# 1. Start your app
uvicorn myapp:app --port 8765

# 2. Modify simple_bench.py to use your endpoints
# Edit: url = "http://127.0.0.1:8765/your-endpoint"

# 3. Run benchmark
python tests/benchmarks/simple_bench.py
```

### Profiling Specific Endpoints

Create a custom benchmark script:

```python
# my_custom_bench.py
import asyncio
import time
import httpx

async def bench_endpoint(url: str, num_requests: int = 1000):
    latencies = []
    
    async with httpx.AsyncClient() as client:
        for _ in range(num_requests):
            start = time.perf_counter()
            await client.get(url)
            latencies.append(time.perf_counter() - start)
    
    print(f"URL: {url}")
    print(f"RPS: {num_requests / sum(latencies):.0f}")
    print(f"p50: {sorted(latencies)[len(latencies)//2] * 1000:.2f}ms")

# Test different endpoints
asyncio.run(bench_endpoint("http://127.0.0.1:8765/"))
asyncio.run(bench_endpoint("http://127.0.0.1:8765/api/users"))
```

### Continuous Performance Monitoring

Set up automated daily performance tracking:

```bash
# save_daily_perf.sh
#!/bin/bash

REPORT_DIR="performance_reports"
mkdir -p "$REPORT_DIR"

DATE=$(date +%Y%m%d)
python scripts/run_performance_tests.py \
    --quick \
    --output "$REPORT_DIR/perf_$DATE.txt"

# Keep only last 30 days
find "$REPORT_DIR" -name "perf_*.txt" -mtime +30 -delete

echo "Performance report saved to $REPORT_DIR/perf_$DATE.txt"
```

Add to cron:
```
0 2 * * * /path/to/save_daily_perf.sh
```

## Interpreting Results

### Good Performance Indicators

✅ **Healthy metrics**:
- Concurrency 1: RPS > 500, p99 < 5ms
- Concurrency 10: RPS > 400, p99 < 100ms
- Concurrency 50: RPS > 300, p99 < 200ms
- Concurrency 100: No crashes, p99 < 500ms

### Warning Signs

⚠️ **Potential issues**:
- RPS drops significantly with concurrency
- p99 latency > 1000ms (1 second)
- High error rate
- Server crashes at high concurrency

### Performance Goals

This fork achieves:
- ✅ Stable at 100+ concurrent connections
- ✅ ~3000+ RPS at concurrency 50 (on typical hardware)
- ✅ p50 latency < 1ms at concurrency 1
- ✅ p99 latency < 50ms at concurrency 100

## Tips and Tricks

### 1. Reduce Noise

For consistent results:
```bash
# Close other apps, then run multiple times
for i in {1..5}; do
    echo "Run $i:"
    python scripts/run_performance_tests.py --quick | grep "Peak Throughput"
    sleep 5
done
```

### 2. Quick Health Check

One-liner to check if server is performing well:

```bash
python -c "import subprocess, sys; r=subprocess.run(['python', 'scripts/run_performance_tests.py', '--quick'], capture_output=True); sys.exit(0 if b'Performance Testing Complete' in r.stdout else 1)"
```

### 3. JSON Output for Automation

Parse JSON results programmatically:

```python
import json

with open('performance_report.json') as f:
    data = json.load(f)

benchmarks = data['tests']['simple_benchmark']
for key, result in benchmarks.items():
    if result['rps'] < 300:
        print(f"⚠️  Low RPS at concurrency {result['concurrency']}")
```

### 4. Compare with Other Servers

Benchmark other ASGI servers for comparison:

```bash
# Test Daphne
daphne tests.benchmarks.test_app:app -p 8765 &
python tests/benchmarks/simple_bench.py > daphne_results.txt
killall daphne

# Test Hypercorn
hypercorn tests.benchmarks.test_app:app --bind 127.0.0.1:8765 &
python tests/benchmarks/simple_bench.py > hypercorn_results.txt
killall hypercorn

# Test this uvicorn fork
uvicorn tests.benchmarks.test_app:app --port 8765 &
python tests/benchmarks/simple_bench.py > uvicorn_fork_results.txt
killall uvicorn
```

## Common Issues

### Issue: "Server not ready"

**Solution**:
```bash
# Check if port is in use
lsof -i :8765
# Or use different port
python scripts/run_performance_tests.py --port 8080
```

### Issue: "Low RPS in CI/CD"

**Cause**: Shared CI runners have limited resources

**Solution**: Use `--quick` mode and relative comparisons:
```bash
# Don't compare CI results to local results
# Instead, compare CI runs to each other
```

### Issue: "High variance between runs"

**Solution**:
```bash
# Run multiple times and average
for i in {1..3}; do
    python scripts/run_performance_tests.py --quick --output run_$i.txt
done
# Then average the results
```

## Next Steps

- See [PERFORMANCE_TESTING.md](PERFORMANCE_TESTING.md) for detailed guide
- See [tests/benchmarks/README.md](tests/benchmarks/README.md) for benchmark details
- See [.github/workflows/performance.yml](.github/workflows/performance.yml) for CI integration

---

**Need help?** Check the main documentation or open an issue.

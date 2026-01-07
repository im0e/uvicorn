#!/usr/bin/env python3
"""
Quick performance test helper
Just run: python quick_perf.py
"""

import subprocess
import sys
from pathlib import Path

project_root = Path(__file__).parent

print("=" * 80)
print("QUICK PERFORMANCE TEST")
print("=" * 80)
print("\nThis will run a quick performance benchmark of this uvicorn fork.")
print("The test takes about 30-60 seconds.\n")

try:
    # Run the quick performance test
    subprocess.run(
        [
            sys.executable,
            str(project_root / "scripts" / "run_performance_tests.py"),
            "--quick",
        ],
        cwd=project_root,
        check=True,
    )
    
    print("\n" + "=" * 80)
    print("✅ Performance test completed successfully!")
    print("=" * 80)
    
except subprocess.CalledProcessError as e:
    print("\n" + "=" * 80)
    print("❌ Performance test failed")
    print("=" * 80)
    print(f"\nError code: {e.returncode}")
    print("\nMake sure you have installed the required dependencies:")
    print("  pip install httpx psutil")
    print("  pip install -e .")
    sys.exit(1)
    
except KeyboardInterrupt:
    print("\n\n⚠️  Test interrupted by user")
    sys.exit(130)

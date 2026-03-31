#!/usr/bin/env python3
"""
benchmarks/run_all.py — Run all benchmarks with server load statistics

Usage:
    python -m benchmarks.run_all          # run all suites
    python -m benchmarks.run_all eventbus # run specific suite
    python -m benchmarks.run_all --quick  # fast subset only
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from benchmarks.server_stats import ServerStats


SUITES = {
    "eventbus": "benchmarks/bench_eventbus.py",
    "module_bus": "benchmarks/bench_module_bus.py",
    "api": "benchmarks/bench_api.py",
    "registry": "benchmarks/bench_registry.py",
    "hw_monitor": "benchmarks/bench_hw_monitor.py",
}


async def run_suite(name: str, path: str) -> dict:
    """Run a single benchmark suite via pytest subprocess (async)."""
    print(f"\n{'='*70}")
    print(f"  SUITE: {name}")
    print(f"{'='*70}")

    start = time.perf_counter()
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "pytest",
        path,
        "-v",
        "-s",
        "--tb=short",
        "--no-header",
        "-q",
        cwd=str(PROJECT_ROOT),
    )
    returncode = await proc.wait()
    elapsed = time.perf_counter() - start

    return {
        "suite": name,
        "exit_code": returncode,
        "duration_sec": round(elapsed, 2),
        "status": "PASS" if returncode == 0 else "FAIL",
    }


async def main() -> None:
    args = sys.argv[1:]
    quick = "--quick" in args
    args = [a for a in args if not a.startswith("--")]

    # Select suites
    if args:
        selected = {k: v for k, v in SUITES.items() if k in args}
        if not selected:
            print(f"Unknown suite(s): {args}")
            print(f"Available: {list(SUITES.keys())}")
            sys.exit(1)
    else:
        selected = SUITES

    print("=" * 70)
    print("  SELENACORE BENCHMARK SUITE")
    print(f"  {datetime.now(timezone.utc).isoformat()}")
    print(f"  Suites: {list(selected.keys())}")
    print("=" * 70)

    # Start server stats collection
    stats = ServerStats(interval=2.0)
    await stats.start()

    # Take initial snapshot
    initial = stats.collect_snapshot()
    print(f"\n  Initial state:")
    print(f"    RAM: {initial.ram_used_pct:.1f}% ({initial.ram_used_mb:.0f}/{initial.ram_total_mb:.0f} MB)")
    print(f"    Disk: {initial.disk_used_pct:.1f}% ({initial.disk_free_gb:.1f} GB free)")
    if initial.cpu_temp_c:
        print(f"    CPU temp: {initial.cpu_temp_c:.1f}°C")

    # Run suites
    results = []
    total_start = time.perf_counter()

    for name, path in selected.items():
        suite_result = await run_suite(name, path)
        results.append(suite_result)

    total_elapsed = time.perf_counter() - total_start

    # Stop stats collection
    await stats.stop()

    # Print results summary
    print("\n" + "=" * 70)
    print("  BENCHMARK RESULTS SUMMARY")
    print("=" * 70)
    print(f"  {'Suite':<20} {'Status':<10} {'Duration':>10}")
    print("-" * 70)
    for r in results:
        status_mark = "OK" if r["status"] == "PASS" else "FAIL"
        print(f"  {r['suite']:<20} {status_mark:<10} {r['duration_sec']:>8.2f}s")
    print("-" * 70)
    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    print(f"  Total: {len(results)} suites, {passed} passed, {failed} failed")
    print(f"  Total time: {total_elapsed:.2f}s")

    # Print server load statistics
    stats.print_report()

    # Save results to JSON
    report_path = PROJECT_ROOT / "benchmarks" / "report.json"
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_duration_sec": round(total_elapsed, 2),
        "suites": results,
        "server_stats": stats.report(),
    }
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\n  Report saved to: {report_path}")

    # Exit with failure if any suite failed
    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    asyncio.run(main())

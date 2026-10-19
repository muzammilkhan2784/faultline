# Faultline (backend)

The Python package: a two-pass static analyzer for Python repositories, a Neo4j
graph writer, analysis queries, and the FastAPI service.

```bash
pip install -e ".[dev]"
faultline analyze path/to/repo --impact pkg.module.function   # no database needed
pytest tests/unit                                             # no database needed
```

See the [project README](../README.md) for what it does, how it resolves calls,
the benchmarks, and the accuracy measurements.

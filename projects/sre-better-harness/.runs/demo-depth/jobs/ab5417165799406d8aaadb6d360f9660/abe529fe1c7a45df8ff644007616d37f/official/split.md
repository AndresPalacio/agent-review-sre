# Split Manifest

## Train

- `eks`: `tests/evals/test_rca.py::test_rca[eks/001-oomkilled-crashloop]`
- `rds_postgres`: `tests/evals/test_rca.py::test_rca[rds_postgres/001-replication-lag]`

## Holdout

- `eks`: `tests/evals/test_rca.py::test_rca[eks/002-image-pull-backoff]`
- `rds_postgres`: `tests/evals/test_rca.py::test_rca[rds_postgres/002-connection-exhaustion]`

## Scorecard

- `eks`: `tests/evals/test_rca.py::test_rca[eks/006-dns-resolution-failure]`
- `rds_postgres`: `tests/evals/test_rca.py::test_rca[rds_postgres/003-storage-full]`

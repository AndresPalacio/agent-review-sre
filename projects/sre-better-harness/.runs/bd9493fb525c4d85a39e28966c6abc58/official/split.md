# Split Manifest

## Train

- `eks`: `evals/test_rca.py::test_rca[eks/001-oomkilled-crashloop]`
- `rds_postgres`: `evals/test_rca.py::test_rca[rds_postgres/001-replication-lag]`

## Holdout

- `eks`: `evals/test_rca.py::test_rca[eks/002-image-pull-backoff]`
- `rds_postgres`: `evals/test_rca.py::test_rca[rds_postgres/002-connection-exhaustion]`

## Scorecard

- `eks`: `evals/test_rca.py::test_rca[eks/006-dns-resolution-failure]`
- `rds_postgres`: `evals/test_rca.py::test_rca[rds_postgres/003-storage-full]`

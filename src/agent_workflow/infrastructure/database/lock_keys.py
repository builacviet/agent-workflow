"""Stable PostgreSQL transaction advisory lock keys."""

from enum import IntEnum

# Two-integer advisory lock namespace; do not renumber deployed keys.
WORKFLOW_LOCK_NAMESPACE = 5727811

# Separate single-bigint advisory lock key space for task authorization.
TASK_AUTHORIZATION_HASH_SEED = 5727811


class TransactionLockDomain(IntEnum):
    SCHEMA_MIGRATION = 1
    DISPATCH_CLAIM = 2
